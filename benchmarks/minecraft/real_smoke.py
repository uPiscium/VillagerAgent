from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from benchmarks.common.run_artifacts import finalize_run_directory, prepare_run_directory
from benchmarks.common.sanitization import sanitize_artifact_value
from benchmarks.experiment_provenance import (
    finalize_provenance,
    model_identity,
    update_provenance_assets,
    write_provenance,
)
from benchmarks.minecraft.experiment import _load_config, run_minecraft_experiment


ENABLE_ENV = {
    "ollama": "VILLAGER_OLLAMA_REAL_SMOKE",
    "port": "VILLAGER_MINECRAFT_PORT_SMOKE",
    "bridge": "VILLAGER_MINECRAFT_BRIDGE_SMOKE",
    "judged": "VILLAGER_MINECRAFT_JUDGED_SMOKE",
}
DEFAULT_TIMEOUT_SECONDS = 30.0
JUDGED_PARENT_RUN_NAME = "minecraft_judged_smoke"


def run_ollama_preflight(*, output_root: Path, timeout_seconds: float, overwrite: bool) -> dict:
    timeout_seconds = _finite_positive_timeout(timeout_seconds)
    api_base = os.environ.get("OLLAMA_API_BASE", "http://localhost:11434/v1")
    safe_api_base = _sanitize_url(api_base)
    url_secret_values = _url_secret_values(api_base)
    model = os.environ.get("OLLAMA_MODEL", "gemma4:12b")
    split = urlsplit(api_base)
    tags_url = urlunsplit((split.scheme, split.netloc, "/api/tags", "", ""))

    def check() -> dict:
        request = Request(tags_url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.load(response)
        models = payload.get("models", []) if isinstance(payload, dict) else []
        selected = next(
            (item for item in models if item.get("name") == model or item.get("model") == model),
            None,
        )
        if selected is None:
            available = sorted(str(item.get("name") or item.get("model")) for item in models)
            raise RuntimeError(f"Ollama model {model!r} is not installed; available models: {available}")
        return {
            "api_base": safe_api_base,
            "model": model,
            "model_digest": selected.get("digest"),
            "model_available": True,
        }

    result = _run_check_bundle(
        output_root=output_root,
        run_name="ollama_preflight",
        check_name="ollama_preflight",
        command=_smoke_command(
            "ollama",
            output_root=output_root,
            timeout_seconds=timeout_seconds,
            overwrite=overwrite,
        ),
        settings={
            "api_base": safe_api_base,
            "model": model,
            "output_root": str(output_root),
            "timeout_seconds": timeout_seconds,
            "overwrite": overwrite,
        },
        check=check,
        overwrite=overwrite,
        asset_builder=lambda payload: [model_identity(
            name="runtime_model",
            provider="ollama",
            model=model,
            metadata={"digest": payload.get("model_digest")},
        )],
        secret_values=url_secret_values,
    )
    return result


def run_port_preflight(*, output_root: Path, host: str, port: int, timeout_seconds: float, overwrite: bool) -> dict:
    timeout_seconds = _finite_positive_timeout(timeout_seconds)
    def check() -> dict:
        started = time.monotonic()
        with socket.create_connection((host, port), timeout=timeout_seconds):
            pass
        return {
            "host": host,
            "port": port,
            "reachable": True,
            "connect_duration_seconds": round(time.monotonic() - started, 6),
        }

    return _run_check_bundle(
        output_root=output_root,
        run_name="minecraft_port",
        check_name="minecraft_port_reachability",
        command=_smoke_command(
            "port",
            output_root=output_root,
            timeout_seconds=timeout_seconds,
            overwrite=overwrite,
            host=host,
            port=port,
        ),
        settings={
            "host": host,
            "port": port,
            "output_root": str(output_root),
            "timeout_seconds": timeout_seconds,
            "overwrite": overwrite,
        },
        check=check,
        overwrite=overwrite,
    )


def run_bridge_smoke(
    *,
    output_root: Path,
    host: str,
    port: int,
    timeout_seconds: float,
    overwrite: bool,
) -> dict:
    timeout_seconds = _finite_positive_timeout(timeout_seconds)
    agent_name = os.environ.get("MINECRAFT_SMOKE_AGENT_NAME", "Alice")
    local_port = int(os.environ.get("MINECRAFT_SMOKE_LOCAL_PORT", "5000"))
    world = os.environ.get("MINECRAFT_SMOKE_WORLD", "world")

    def check() -> dict:
        result_path = output_root / "minecraft_bridge" / ".runtime" / "bridge_result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "benchmarks.minecraft.real_smoke",
            "_bridge-child",
            "--host",
            host,
            "--port",
            str(port),
            "--agent-name",
            agent_name,
            "--local-port",
            str(local_port),
            "--world",
            world,
            "--result-path",
            str(result_path),
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=os.name == "posix",
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            _stop_process(process)
            raise TimeoutError(f"env_type.none bridge smoke timed out after {timeout_seconds} seconds") from exc
        if process.returncode != 0:
            raise RuntimeError(
                f"env_type.none bridge smoke failed with exit code {process.returncode}: "
                f"{stderr.strip() or stdout.strip()}"
            )
        if not result_path.exists():
            raise RuntimeError("env_type.none bridge smoke produced no result artifact")
        return json.loads(result_path.read_text(encoding="utf-8"))

    return _run_check_bundle(
        output_root=output_root,
        run_name="minecraft_bridge",
        check_name="env_type.none_bridge_action",
        command=_smoke_command(
            "bridge",
            output_root=output_root,
            timeout_seconds=timeout_seconds,
            overwrite=overwrite,
            host=host,
            port=port,
        ),
        settings={
            "host": host,
            "port": port,
            "agent_name": agent_name,
            "local_port": local_port,
            "world": world,
            "output_root": str(output_root),
            "timeout_seconds": timeout_seconds,
            "overwrite": overwrite,
        },
        check=check,
        overwrite=overwrite,
    )


def run_judged_smoke(
    *,
    output_root: Path,
    config_path: Path,
    timeout_seconds: float,
    overwrite: bool,
) -> dict:
    timeout_seconds = _finite_positive_timeout(timeout_seconds)
    launch_config = _load_judged_smoke_config(config_path)
    parent_dir = output_root / JUDGED_PARENT_RUN_NAME
    attempt_id = prepare_run_directory(
        parent_dir,
        producer="benchmarks.minecraft.real_smoke",
        overwrite=overwrite,
    )
    command = _smoke_command(
        "judged",
        output_root=output_root,
        timeout_seconds=timeout_seconds,
        overwrite=overwrite,
        config_path=config_path,
    )
    write_provenance(
        parent_dir,
        benchmark="minecraft_real_smoke",
        command=command,
        resolved_config={
            "check": "minecraft_judged_meta",
            "config_path": str(config_path),
            "host": launch_config["host"],
            "port": launch_config["port"],
            "output_root": str(output_root),
            "timeout_seconds": timeout_seconds,
            "overwrite": overwrite,
            "attempt_id": attempt_id,
        },
        environment_notes="opt-in judged real-environment smoke; mutates Minecraft",
    )
    try:
        _check_server_reachable(
            host=launch_config["host"],
            port=launch_config["port"],
            timeout_seconds=timeout_seconds,
        )
        summary = run_minecraft_experiment(
            config_path=config_path,
            output_root=output_root,
            run_name="minecraft_judged_meta",
            execute=True,
            execute_timeout_seconds=timeout_seconds,
            command_text=shlex.join(command),
            overwrite=overwrite,
        )
        successful = not summary.get("error") and bool(summary.get("score_available"))
        verification = {
            "check": "minecraft_judged_meta",
            "attempt_id": attempt_id,
            "status": "success" if successful else "failure",
            "experiment_output_dir": summary.get("output_dir"),
            "experiment_attempt_id": summary.get("attempt_id"),
            "score_available": bool(summary.get("score_available")),
            "load_status": summary.get("load_status"),
            "error": summary.get("error") or (None if successful else "no non-empty score was produced"),
        }
        _write_json(parent_dir / "verification.json", verification)
        finalize_provenance(parent_dir, status="success" if successful else "failure")
        finalize_run_directory(
            parent_dir,
            attempt_id=attempt_id,
            producer="benchmarks.minecraft.real_smoke",
            status="completed" if successful else "failed",
            stamp_nested=False,
        )
        return summary
    except BaseException as exc:
        _write_json(parent_dir / "verification.json", {
            "check": "minecraft_judged_meta",
            "attempt_id": attempt_id,
            "status": "failure",
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        })
        finalize_provenance(parent_dir, status="timeout" if isinstance(exc, TimeoutError) else "failure")
        finalize_run_directory(
            parent_dir,
            attempt_id=attempt_id,
            producer="benchmarks.minecraft.real_smoke",
            status="failed",
            stamp_nested=False,
        )
        raise


def _run_check_bundle(
    *,
    output_root: Path,
    run_name: str,
    check_name: str,
    command: list[str],
    settings: dict,
    check,
    overwrite: bool,
    asset_builder=None,
    secret_values: tuple[str, ...] = (),
) -> dict:
    output_dir = output_root / run_name
    attempt_id = prepare_run_directory(
        output_dir,
        producer="benchmarks.minecraft.real_smoke",
        overwrite=overwrite,
    )
    settings = {**settings, "attempt_id": attempt_id}
    write_provenance(
        output_dir,
        benchmark="minecraft_real_smoke",
        command=command,
        resolved_config={"check": check_name, **settings},
        environment_notes="optional real-environment smoke; not benchmark evidence",
    )
    started = time.monotonic()
    try:
        result = {
            "check": check_name,
            "attempt_id": attempt_id,
            "status": "success",
            **check(),
        }
        result = sanitize_artifact_value(result, secret_values=secret_values)
    except BaseException as exc:
        result = {
            "check": check_name,
            "attempt_id": attempt_id,
            "status": "failure",
            "error_type": exc.__class__.__name__,
            "error": sanitize_artifact_value(str(exc), secret_values=secret_values),
        }
        _write_json(output_dir / "verification.json", result)
        finalize_provenance(output_dir, status="timeout" if isinstance(exc, TimeoutError) else "failure")
        finalize_run_directory(
            output_dir,
            attempt_id=attempt_id,
            producer="benchmarks.minecraft.real_smoke",
            status="failed",
        )
        raise
    result["duration_seconds"] = round(time.monotonic() - started, 6)
    _write_json(output_dir / "verification.json", result)
    if asset_builder is not None:
        update_provenance_assets(output_dir, asset_builder(result))
    finalize_provenance(output_dir, status="success")
    finalize_run_directory(
        output_dir,
        attempt_id=attempt_id,
        producer="benchmarks.minecraft.real_smoke",
        status="completed",
    )
    return result


def _bridge_child(args: argparse.Namespace) -> int:
    from env.env import Agent, VillagerBench, env_type

    environment = VillagerBench(
        env_type.none,
        task_id=0,
        dig_needed=False,
        host=args.host,
        port=args.port,
        task_name="issue_243_bridge_smoke",
        _virtual_debug=False,
    )
    environment.base_port = args.local_port
    environment.agent_register(agent_number=1, name_list=[args.agent_name])
    try:
        Agent.launch(host=args.host, port=args.port, world=args.world, fast=True)
        environment.running = True
        ping = Agent.ping(args.agent_name)
        environment_info = Agent.get_environment_info_dict(args.agent_name)
        if not isinstance(ping, dict) or ping.get("status") is False:
            raise RuntimeError(f"bridge ping failed: {ping}")
        if not isinstance(environment_info, dict) or environment_info.get("status") is False:
            raise RuntimeError(f"read-only environment action failed: {environment_info}")
        _write_json(Path(args.result_path), {
            "env_type": "none",
            "host": args.host,
            "port": args.port,
            "agent_name": args.agent_name,
            "bridge_ping": ping,
            "read_only_action": "get_environment_info_dict",
            "action_result": environment_info,
        })
        return 0
    finally:
        environment.stop()
        for process in Agent.agent_process.values():
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        os.killpg(process.pid, 15)
    else:
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, 9)
        else:
            process.kill()
        process.wait()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _smoke_command(
    check: str,
    *,
    output_root: Path,
    timeout_seconds: float,
    overwrite: bool,
    host: str | None = None,
    port: int | None = None,
    config_path: Path | None = None,
) -> list[str]:
    command = [sys.executable, "-m", "benchmarks.minecraft.real_smoke", check]
    if host is not None:
        command.extend(["--host", host])
    if port is not None:
        command.extend(["--port", str(port)])
    if config_path is not None:
        command.extend(["--config", str(config_path)])
    command.extend([
        "--output-dir",
        str(output_root),
        "--timeout-seconds",
        str(timeout_seconds),
    ])
    if overwrite:
        command.append("--overwrite")
    return command


def _finite_positive_timeout(value: str | float) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout must be a finite positive number") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite positive number")
    return timeout


def _positive_timeout(value: str) -> float:
    try:
        return _finite_positive_timeout(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _sanitize_url(value: str) -> str:
    split = urlsplit(value)
    hostname = split.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    try:
        if split.port is not None:
            netloc += f":{split.port}"
    except ValueError:
        netloc = split.netloc.rsplit("@", 1)[-1]
    return urlunsplit((split.scheme, netloc, split.path, "", ""))


def _url_secret_values(value: str) -> tuple[str, ...]:
    split = urlsplit(value)
    values = [split.username, split.password]
    values.extend(query_value for _, query_value in parse_qsl(split.query, keep_blank_values=True))
    return tuple(item for item in values if item)


def _load_judged_smoke_config(config_path: Path) -> dict:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Judged real smoke requires one explicit config object, not a config list")
    config = _load_config(config_path, config_index=0, execute=True)
    if config.get("task_type") != "meta":
        raise ValueError("Judged real smoke requires task_type=meta")
    if int(config.get("agent_num")) != 1:
        raise ValueError("Judged real smoke requires agent_num=1")
    task_scenario = config.get("task_scenario")
    if not isinstance(task_scenario, str) or not task_scenario.strip():
        raise ValueError("Judged real smoke requires a non-empty task_scenario")
    evaluation_arg = config.get("evaluation_arg")
    if not isinstance(evaluation_arg, dict) or not evaluation_arg:
        raise ValueError("Judged real smoke requires a non-empty scenario evaluation_arg object")
    _require_identity_path(config, ("world_snapshot_path", "reset_snapshot_path"), "reset/world")
    _require_identity_path(config, ("bridge_path",), "bridge")
    if not config.get("server_version") or not config.get("server_protocol"):
        raise ValueError("Judged real smoke requires server_version and server_protocol identity fields")
    return config


def _require_identity_path(config: dict, fields: tuple[str, ...], label: str) -> Path:
    configured = next((config.get(field) for field in fields if config.get(field)), None)
    if not isinstance(configured, str) or not configured.strip():
        raise ValueError(f"Judged real smoke requires an explicit {label} identity path")
    path = Path(configured).expanduser()
    if not path.exists():
        raise ValueError(f"Judged real smoke {label} identity path does not exist: {path}")
    return path


def _check_server_reachable(*, host: str, port: int, timeout_seconds: float) -> None:
    with socket.create_connection((host, int(port)), timeout=_finite_positive_timeout(timeout_seconds)):
        pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Opt-in Minecraft real-environment smoke checks")
    subparsers = parser.add_subparsers(dest="check", required=True)
    for name in ENABLE_ENV:
        child = subparsers.add_parser(name)
        child.add_argument("--output-dir", type=Path)
        child.add_argument("--timeout-seconds", type=_positive_timeout, default=None)
        child.add_argument("--overwrite", action="store_true")
        if name in {"port", "bridge"}:
            child.add_argument("--host")
            child.add_argument("--port", type=int)
        if name == "judged":
            child.add_argument("--config", type=Path)
    child = subparsers.add_parser("_bridge-child")
    child.add_argument("--host", required=True)
    child.add_argument("--port", required=True, type=int)
    child.add_argument("--agent-name", required=True)
    child.add_argument("--local-port", required=True, type=int)
    child.add_argument("--world", required=True)
    child.add_argument("--result-path", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.check == "_bridge-child":
        return _bridge_child(args)
    enable_variable = ENABLE_ENV[args.check]
    if os.environ.get(enable_variable) != "1":
        print(f"SKIP {args.check}: set {enable_variable}=1 to opt in")
        return 0
    output_root = args.output_dir or (
        Path(os.environ["VILLAGER_REAL_SMOKE_OUTPUT_DIR"])
        if os.environ.get("VILLAGER_REAL_SMOKE_OUTPUT_DIR")
        else None
    )
    missing = []
    if output_root is None:
        missing.append("VILLAGER_REAL_SMOKE_OUTPUT_DIR or --output-dir")
    host = getattr(args, "host", None) or os.environ.get("MINECRAFT_HOST")
    port = getattr(args, "port", None) or (
        int(os.environ["MINECRAFT_PORT"]) if os.environ.get("MINECRAFT_PORT") else None
    )
    if args.check in {"port", "bridge"}:
        if not host:
            missing.append("MINECRAFT_HOST or --host")
        if port is None:
            missing.append("MINECRAFT_PORT or --port")
    config = getattr(args, "config", None) or (
        Path(os.environ["MINECRAFT_JUDGED_CONFIG"])
        if os.environ.get("MINECRAFT_JUDGED_CONFIG")
        else None
    )
    if args.check == "judged" and config is None:
        missing.append("MINECRAFT_JUDGED_CONFIG or --config")
    if missing:
        print(f"ERROR {args.check}: missing prerequisite(s): {', '.join(missing)}", file=sys.stderr)
        return 2
    timeout_source = args.timeout_seconds or os.environ.get(
        "MINECRAFT_JUDGED_TIMEOUT_SECONDS" if args.check == "judged" else "VILLAGER_REAL_SMOKE_TIMEOUT_SECONDS",
        "600" if args.check == "judged" else str(DEFAULT_TIMEOUT_SECONDS),
    )
    try:
        timeout = _finite_positive_timeout(timeout_source)
    except ValueError as exc:
        print(f"ERROR {args.check}: {exc}", file=sys.stderr)
        return 2
    overwrite = args.overwrite or os.environ.get("VILLAGER_REAL_SMOKE_OVERWRITE") == "1"
    try:
        if args.check == "ollama":
            result = run_ollama_preflight(output_root=output_root, timeout_seconds=timeout, overwrite=overwrite)
        elif args.check == "port":
            result = run_port_preflight(
                output_root=output_root, host=host, port=port, timeout_seconds=timeout, overwrite=overwrite
            )
        elif args.check == "bridge":
            result = run_bridge_smoke(
                output_root=output_root, host=host, port=port, timeout_seconds=timeout, overwrite=overwrite
            )
        else:
            result = run_judged_smoke(
                output_root=output_root, config_path=config, timeout_seconds=timeout, overwrite=overwrite
            )
    except Exception as exc:
        print(f"FAIL {args.check}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if args.check == "judged" and (result.get("error") or not result.get("score_available")):
        print("FAIL judged: no non-empty score was produced; inspect the run artifacts", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
