from __future__ import annotations

import argparse
from collections import Counter
import gzip
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from benchmarks.partnr.real_env import (
    DEFAULT_DATASET_PATH,
    DEFAULT_SCENE_ROOT,
    DEFAULT_SOURCE_ROOT,
    PARTNRRuntimeConfig,
    build_bounded_smoke_command,
    build_step_zero_command,
    inspect_real_preflight,
    write_bounded_dataset,
)


def run_official_gate(runtime: PARTNRRuntimeConfig, *, mode: str) -> dict[str, Any]:
    preflight = inspect_real_preflight(runtime)
    if not preflight["ready"]:
        raise RuntimeError("PARTNR real preflight failed: " + ", ".join(preflight["missing"]))
    episode_limit = 1 if mode == "step-zero" else runtime.episode_limit
    subset = runtime.output_dir / "inputs" / f"val_mini_first_{episode_limit}.json.gz"
    subset_audit = write_bounded_dataset(
        runtime.dataset_path, subset, episode_limit=episode_limit
    )
    command = (
        build_step_zero_command(runtime, subset.resolve())
        if mode == "step-zero"
        else build_bounded_smoke_command(runtime, subset.resolve())
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(runtime.source_root.resolve()), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    try:
        completed = subprocess.run(
            command,
            cwd=runtime.source_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=runtime.wall_timeout_seconds,
            check=False,
        )
        returncode = completed.returncode
        subprocess_status = "completed" if returncode == 0 else "failed"
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        returncode = None
        subprocess_status = "timed_out"
        stdout = _subprocess_text(exc.stdout)
        stderr = _subprocess_text(exc.stderr)
    official_metrics = _collect_official_metrics(runtime, mode)
    gate_completed = (
        subprocess_status == "completed"
        and bool(official_metrics["expected_episode_ids"])
        and official_metrics["exact_episode_accounting"]
        and not official_metrics["failed_episode_ids"]
    )
    result = {
        "schema_version": 1,
        "benchmark": "partnr",
        "gate": "official_step_zero" if mode == "step-zero" else "bounded_heuristic_full_obs",
        "baseline_classification": "official_oracle_heuristic" if mode == "bounded" else "dataset_verifier",
        "performance_claim": False,
        "preflight": preflight,
        "subset": subset_audit,
        "command": command,
        "timeout_seconds": runtime.wall_timeout_seconds,
        "returncode": returncode,
        "subprocess_status": subprocess_status,
        "status": "completed" if gate_completed else "failed",
        "stdout": stdout[-20000:],
        "stderr": stderr[-20000:],
    }
    result["official_metrics"] = official_metrics
    return result


def _collect_official_metrics(runtime: PARTNRRuntimeConfig, mode: str) -> dict[str, Any]:
    root = runtime.output_dir / ("step_zero" if mode == "step-zero" else "bounded_heuristic")
    records = []
    if root.exists():
        for path in sorted(root.rglob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and (
                "success_init" in payload or "stats" in payload
            ):
                records.append(
                    _normalize_official_record(
                        path=path,
                        output_dir=runtime.output_dir,
                        payload=payload,
                    )
                )
    expected_limit = 1 if mode == "step-zero" else runtime.episode_limit
    subset = runtime.output_dir / "inputs" / f"val_mini_first_{expected_limit}.json.gz"
    expected_ids = _read_subset_episode_ids(subset)
    completed_ids = [record["episode_id"] for record in records]
    successful_ids = [record["episode_id"] for record in records if record["success"]]
    failed_ids = [record["episode_id"] for record in records if not record["success"]]
    expected_counts = Counter(expected_ids)
    completed_counts = Counter(completed_ids)
    return {
        "expected_episode_ids": expected_ids,
        "completed_episode_ids": completed_ids,
        "successful_episode_ids": successful_ids,
        "failed_episode_ids": failed_ids,
        "missing_episode_ids": [episode_id for episode_id in expected_ids if episode_id not in completed_ids],
        "unexpected_episode_ids": [
            episode_id for episode_id in completed_ids if episode_id not in expected_counts
        ],
        "duplicate_episode_ids": sorted(
            episode_id for episode_id, count in completed_counts.items() if count > 1
        ),
        "exact_episode_accounting": completed_counts == expected_counts,
        "record_count": len(records),
        "records": records,
    }


def _normalize_official_record(
    *, path: Path, output_dir: Path, payload: dict[str, Any]
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": str(path.relative_to(output_dir)),
        "episode_id": path.stem,
        "success": bool(payload.get("success", payload.get("success_init", False))),
    }
    stats = payload.get("stats", payload.get("info", {}))
    if isinstance(stats, str):
        try:
            stats = json.loads(stats)
        except json.JSONDecodeError:
            stats = {}
    if isinstance(stats, dict):
        for key in ("task_percent_complete", "task_state_success", "runtime", "sim_step_count"):
            if key in stats:
                record[key] = stats[key]
    return record


def _read_subset_episode_ids(path: Path) -> list[str]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    episodes = payload.get("episodes", []) if isinstance(payload, dict) else []
    return [str(episode.get("episode_id", "")) for episode in episodes if isinstance(episode, dict)]


def _subprocess_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight or run bounded official PARTNR gates.")
    parser.add_argument("--mode", choices=("preflight", "step-zero", "bounded"), default="preflight")
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE_ROOT))
    parser.add_argument("--dataset-path", default=str(DEFAULT_DATASET_PATH))
    parser.add_argument("--scene-root", default=str(DEFAULT_SCENE_ROOT))
    parser.add_argument("--runtime-output", default="result/partnr/issue_378_real")
    parser.add_argument("--python-executable", default=os.environ.get("PARTNR_PYTHON", os.sys.executable))
    parser.add_argument("--episode-limit", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    runtime = PARTNRRuntimeConfig(
        source_root=Path(args.source_root),
        dataset_path=Path(args.dataset_path),
        scene_root=Path(args.scene_root),
        output_dir=Path(args.runtime_output),
        python_executable=Path(args.python_executable),
        episode_limit=args.episode_limit,
        wall_timeout_seconds=args.timeout,
    )
    payload = (
        inspect_real_preflight(runtime)
        if args.mode == "preflight"
        else run_official_gate(runtime, mode=args.mode)
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    ready = bool(payload.get("ready", payload.get("status") == "completed"))
    print(json.dumps({"ready": ready, "output": str(output)}))
    return 2 if args.require_ready and not ready else 0


if __name__ == "__main__":
    raise SystemExit(main())
