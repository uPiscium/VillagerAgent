import json
import shlex
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from benchmarks.common.run_artifacts import read_attempt_id, validate_run_attempt
from benchmarks.minecraft.real_smoke import (
    main,
    run_bridge_smoke,
    run_judged_smoke,
    run_ollama_preflight,
    run_port_preflight,
)


def test_real_smoke_is_disabled_without_explicit_opt_in(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("VILLAGER_OLLAMA_REAL_SMOKE", raising=False)

    assert main(["ollama", "--output-dir", str(tmp_path)]) == 0
    assert "SKIP ollama: set VILLAGER_OLLAMA_REAL_SMOKE=1 to opt in" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_ollama_preflight_records_digest_and_valid_attempt(monkeypatch, tmp_path):
    class OllamaHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/api/tags"
            payload = json.dumps({
                "models": [{"name": "smoke:latest", "digest": "sha256:immutable"}],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), OllamaHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    monkeypatch.setenv("OLLAMA_API_BASE", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("OLLAMA_MODEL", "smoke:latest")
    try:
        result = run_ollama_preflight(output_root=tmp_path, timeout_seconds=2, overwrite=False)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    run_dir = tmp_path / "ollama_preflight"
    assert result["model_digest"] == "sha256:immutable"
    provenance = json.loads((run_dir / "provenance.json").read_text(encoding="utf-8"))
    model = next(asset for asset in provenance["assets"] if asset["name"] == "runtime_model")
    assert model["digest"] == "sha256:immutable"
    _assert_recorded_command(run_dir, [
        sys.executable,
        "-m",
        "benchmarks.minecraft.real_smoke",
        "ollama",
        "--output-dir",
        str(tmp_path),
        "--timeout-seconds",
        "2.0",
    ])
    validate_run_attempt(run_dir, attempt_id=read_attempt_id(run_dir))


def test_minecraft_port_preflight_records_reachable_endpoint(tmp_path):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    try:
        result = run_port_preflight(
            output_root=tmp_path,
            host="127.0.0.1",
            port=port,
            timeout_seconds=2,
            overwrite=True,
        )
    finally:
        server.close()

    run_dir = tmp_path / "minecraft_port"
    assert result["reachable"] is True
    assert result["host"] == "127.0.0.1"
    assert result["port"] == port
    _assert_recorded_command(run_dir, [
        sys.executable,
        "-m",
        "benchmarks.minecraft.real_smoke",
        "port",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--output-dir",
        str(tmp_path),
        "--timeout-seconds",
        "2.0",
        "--overwrite",
    ])
    validate_run_attempt(run_dir, attempt_id=read_attempt_id(run_dir))


def test_bridge_records_exact_runnable_command(monkeypatch, tmp_path):
    class Process:
        returncode = 0

        def __init__(self, command):
            result_path = Path(command[command.index("--result-path") + 1])
            result_path.write_text(json.dumps({"bridge_ping": {"status": True}}), encoding="utf-8")

        def communicate(self, timeout):
            return "", ""

    real_popen = __import__("subprocess").Popen
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.subprocess.Popen",
        lambda command, **kwargs: (
            Process(command) if "--result-path" in command else real_popen(command, **kwargs)
        ),
    )

    run_bridge_smoke(
        output_root=tmp_path,
        host="minecraft.example.test",
        port=25565,
        timeout_seconds=7,
        overwrite=True,
    )

    _assert_recorded_command(tmp_path / "minecraft_bridge", [
        sys.executable,
        "-m",
        "benchmarks.minecraft.real_smoke",
        "bridge",
        "--host",
        "minecraft.example.test",
        "--port",
        "25565",
        "--output-dir",
        str(tmp_path),
        "--timeout-seconds",
        "7.0",
        "--overwrite",
    ])


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf", "-inf"])
def test_environment_timeout_must_be_finite_and_positive(timeout, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VILLAGER_MINECRAFT_PORT_SMOKE", "1")
    monkeypatch.setenv("VILLAGER_REAL_SMOKE_TIMEOUT_SECONDS", timeout)
    monkeypatch.setenv("MINECRAFT_HOST", "127.0.0.1")
    monkeypatch.setenv("MINECRAFT_PORT", "25565")
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.run_port_preflight",
        lambda **kwargs: pytest.fail("invalid timeout must be rejected before the check starts"),
    )

    assert main(["port", "--output-dir", str(tmp_path)]) == 2
    assert "timeout must be a finite positive number" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_bridge_rejects_invalid_timeout_before_process_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("invalid timeout must be rejected before process launch"),
    )

    with pytest.raises(ValueError, match="timeout must be a finite positive number"):
        run_bridge_smoke(
            output_root=tmp_path,
            host="127.0.0.1",
            port=25565,
            timeout_seconds=float("nan"),
            overwrite=False,
        )

    assert not list(tmp_path.iterdir())


def test_ollama_preflight_redacts_url_credential_sentinels(monkeypatch, tmp_path):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    response = Response()
    monkeypatch.setenv(
        "OLLAMA_API_BASE",
        "http://USER_SENTINEL:PASSWORD_SENTINEL@example.test/v1?token=QUERY_SENTINEL",
    )
    monkeypatch.setenv("OLLAMA_MODEL", "smoke:latest")
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.urlopen",
        lambda request, timeout: response,
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.json.load",
        lambda stream: {"models": [{"name": "smoke:latest", "digest": "sha256:immutable"}]},
    )

    result = run_ollama_preflight(output_root=tmp_path, timeout_seconds=2, overwrite=False)

    assert result["api_base"] == "http://example.test/v1"
    artifact_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (tmp_path / "ollama_preflight").rglob("*")
        if path.is_file()
    )
    for sentinel in ("USER_SENTINEL", "PASSWORD_SENTINEL", "QUERY_SENTINEL"):
        assert sentinel not in artifact_text


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda config: config.update(agent_num=2), "agent_num=1"),
        (lambda config: config.pop("evaluation_arg"), "scenario evaluation_arg"),
        (lambda config: config.pop("reset_snapshot_path"), "reset/world identity path"),
        (lambda config: config.pop("bridge_path"), "bridge identity path"),
        (lambda config: config.pop("server_version"), "server_version and server_protocol"),
    ],
)
def test_judged_smoke_rejects_missing_safety_prerequisites(tmp_path, mutate, message):
    config_path = _write_judged_config(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    mutate(config)
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        run_judged_smoke(
            output_root=tmp_path / "output",
            config_path=config_path,
            timeout_seconds=10,
            overwrite=False,
        )

    assert not (tmp_path / "output").exists()


def test_judged_smoke_checks_reachability_before_mutation(monkeypatch, tmp_path):
    config_path = _write_judged_config(tmp_path)
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.socket.create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionRefusedError("closed")),
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.run_minecraft_experiment",
        lambda **kwargs: pytest.fail("unreachable server must not launch the experiment"),
    )

    with pytest.raises(ConnectionRefusedError, match="closed"):
        run_judged_smoke(
            output_root=tmp_path / "output",
            config_path=config_path,
            timeout_seconds=10,
            overwrite=False,
        )

    parent_dir = tmp_path / "output" / "minecraft_judged_smoke"
    manifest = validate_run_attempt(
        parent_dir,
        attempt_id=read_attempt_id(parent_dir),
        require_completed=False,
    )
    assert manifest["status"] == "failed"
    assert not (parent_dir / "_COMPLETED").exists()


def test_judged_missing_score_finalizes_parent_attempt_as_failure(monkeypatch, tmp_path):
    config_path = _write_judged_config(tmp_path)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(
        "benchmarks.minecraft.real_smoke.socket.create_connection",
        lambda *args, **kwargs: Connection(),
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.experiment._execute_real_runtime",
        lambda *args, **kwargs: {"score": {}, "action_log": {}},
    )

    summary = run_judged_smoke(
        output_root=tmp_path / "output",
        config_path=config_path,
        timeout_seconds=10,
        overwrite=False,
    )

    assert summary["score_available"] is False
    parent_dir = tmp_path / "output" / "minecraft_judged_smoke"
    manifest = validate_run_attempt(
        parent_dir,
        attempt_id=read_attempt_id(parent_dir),
        require_completed=False,
    )
    provenance = json.loads((parent_dir / "provenance.json").read_text(encoding="utf-8"))
    verification = json.loads((parent_dir / "verification.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert provenance["lifecycle"]["status"] == "failure"
    assert verification["status"] == "failure"
    assert not (parent_dir / "_COMPLETED").exists()
    _assert_recorded_command(parent_dir, [
        sys.executable,
        "-m",
        "benchmarks.minecraft.real_smoke",
        "judged",
        "--config",
        str(config_path),
        "--output-dir",
        str(tmp_path / "output"),
        "--timeout-seconds",
        "10.0",
    ])
    experiment_dir = tmp_path / "output" / "minecraft_judged_meta"
    experiment_manifest = validate_run_attempt(
        experiment_dir,
        attempt_id=read_attempt_id(experiment_dir),
    )
    assert experiment_manifest["status"] == "completed"


def _assert_recorded_command(run_dir: Path, expected: list[str]) -> None:
    provenance = json.loads((run_dir / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["argv"] == expected
    assert (run_dir / "command.txt").read_text(encoding="utf-8") == shlex.join(expected) + "\n"


def _write_judged_config(tmp_path: Path) -> Path:
    reset_path = tmp_path / "world-reset"
    bridge_path = tmp_path / "bridge"
    reset_path.mkdir()
    bridge_path.mkdir()
    config_path = tmp_path / "judged.json"
    config_path.write_text(json.dumps({
        "task_type": "meta",
        "task_idx": 0,
        "agent_num": 1,
        "task_goal": "Move to the target",
        "host": "127.0.0.1",
        "port": 25565,
        "task_name": "judged-smoke",
        "task_scenario": "move",
        "evaluation_arg": {"target": [1, 2, 3]},
        "reset_snapshot_path": str(reset_path),
        "bridge_path": str(bridge_path),
        "server_version": "1.19.2",
        "server_protocol": "760",
    }), encoding="utf-8")
    return config_path
