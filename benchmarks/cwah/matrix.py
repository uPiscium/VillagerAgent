from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.common.run_artifacts import (
    finalize_run_directory,
    prepare_run_directory,
    validate_run_attempt,
)
from benchmarks.common.sanitization import redact_text
from benchmarks.cwah.failure_diagnostics import failure_reason_counts_from_process_output, merge_count_dicts
from benchmarks.cwah.provenance import (
    is_provider_timeout,
    model_metadata,
    model_provider,
    provenance_assets,
    resolved_external_paths,
)
from benchmarks.experiment_provenance import (
    finalize_provenance,
    model_identity,
    update_provenance_assets,
    write_provenance,
)


@dataclass(frozen=True)
class MatrixRun:
    index: int
    task_id: int
    seed: int


def main() -> None:
    args = parse_args()
    immutable_model_metadata = model_metadata(args.base_url, args.model)
    output_dir = Path(args.output_dir)
    attempt_id = prepare_run_directory(
        output_dir,
        producer="benchmarks.cwah.matrix",
        overwrite=args.overwrite,
    )
    try:
        runs = build_matrix(parse_int_list(args.tasks), parse_int_list(args.seeds))
        write_provenance(
            output_dir,
            benchmark="cwah",
            command=[sys.executable, "-m", "benchmarks.cwah.matrix", *sys.argv[1:]],
            resolved_config={
                **vars(args),
                "api_key": os.environ.get("CWAH_LLM_API_KEY", ""),
                "run_plan": [run.__dict__ for run in runs],
                "attempt_id": attempt_id,
            },
            environment_notes=f"matrix=true; env={args.env}",
            assets=provenance_assets(args, metadata=immutable_model_metadata),
        )
        results = []
        for run in runs:
            results.append(run_matrix_item(args=args, output_dir=output_dir, run=run))
        write_matrix_summary(output_dir=output_dir, results=results, attempt_id=attempt_id)
    except BaseException as exc:
        finalize_provenance(
            output_dir,
            status="timeout" if is_provider_timeout(exc) else "failure",
        )
        finalize_run_directory(
            output_dir,
            attempt_id=attempt_id,
            producer="benchmarks.cwah.matrix",
            status="failed",
            stamp_nested=False,
        )
        raise
    provenance_status = _matrix_provenance_status(results)
    finalize_provenance(output_dir, status=provenance_status)
    finalize_run_directory(
        output_dir,
        attempt_id=attempt_id,
        producer="benchmarks.cwah.matrix",
        status="completed" if all(result.get("passed") for result in results) else "failed",
        stamp_nested=False,
    )
    print(json.dumps(aggregate_results(results), sort_keys=True))
    if not all(result.get("passed") for result in results):
        raise SystemExit(1)


def parse_int_list(value: str) -> tuple[int, ...]:
    values = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        values.append(int(item))
    if not values:
        raise ValueError("Expected at least one integer value.")
    return tuple(values)


def build_matrix(tasks: tuple[int, ...], seeds: tuple[int, ...]) -> tuple[MatrixRun, ...]:
    return tuple(
        MatrixRun(index=index, task_id=task_id, seed=seed)
        for index, (task_id, seed) in enumerate((task_id, seed) for task_id in tasks for seed in seeds)
    )


def matrix_port(*, base_port: int, run: MatrixRun, port_stride: int) -> int:
    if port_stride < 1:
        raise ValueError("port_stride must be >= 1")
    return base_port + (run.index * port_stride)


def run_matrix_item(*, args: argparse.Namespace, output_dir: Path, run: MatrixRun) -> dict[str, Any]:
    attempt_state: dict = {}
    try:
        return _run_matrix_item_attempt(
            args=args,
            output_dir=output_dir,
            run=run,
            attempt_state=attempt_state,
        )
    except BaseException as exc:
        if attempt_state:
            finalize_provenance(
                attempt_state["run_dir"],
                status="timeout" if is_provider_timeout(exc) else "failure",
            )
            finalize_run_directory(
                attempt_state["run_dir"],
                attempt_id=attempt_state["attempt_id"],
                producer="benchmarks.cwah.matrix.run",
                status="failed",
            )
        raise


def _run_matrix_item_attempt(
    *,
    args: argparse.Namespace,
    output_dir: Path,
    run: MatrixRun,
    attempt_state: dict,
) -> dict[str, Any]:
    run_name = f"task_{run.task_id}_seed_{run.seed}"
    run_dir = output_dir / run_name
    immutable_model_metadata = model_metadata(args.base_url, args.model)
    attempt_id = prepare_run_directory(
        run_dir,
        producer="benchmarks.cwah.matrix.run",
    )
    attempt_state.update({"run_dir": run_dir, "attempt_id": attempt_id})
    raw_output = run_dir / "raw.json"
    artifact_dir = run_dir / "normalized"
    episode_id = f"cwah-{run_name}"
    temperature = getattr(args, "temperature", 0.0)
    max_tokens = getattr(args, "max_tokens", 128)
    effective_max_policy_steps = args.max_steps if args.full_episode else args.max_policy_steps
    external_paths = resolved_external_paths(args)
    command = [
        sys.executable,
        "-m",
        "benchmarks.cwah.llm_smoke",
        "--env",
        args.env,
        "--episode-id",
        episode_id,
        "--task-id",
        str(run.task_id),
        "--seed",
        str(run.seed),
        "--max-steps",
        str(args.max_steps),
        "--max-policy-steps",
        str(args.max_policy_steps),
        "--prefer-physical-after-steps",
        str(args.prefer_physical_after_steps),
        "--navigation-loop-threshold",
        str(args.navigation_loop_threshold),
        "--base-url",
        args.base_url,
        "--model",
        args.model,
        "--temperature",
        str(temperature),
        "--max-tokens",
        str(max_tokens),
        "--output",
        str(raw_output),
        "--artifact-dir",
        str(artifact_dir),
        "--attempt-id",
        attempt_id,
    ]
    if args.full_episode:
        command.append("--full-episode")
    command.extend(["--coela-cwah-path", external_paths["coela_cwah_path"]])
    command.extend(["--dataset-path", external_paths["dataset_path"]])
    command.extend(["--executable-file", external_paths["executable_file"]])
    assigned_port = None
    if args.base_port:
        assigned_port = matrix_port(base_port=args.base_port, run=run, port_stride=args.port_stride)
        command.extend(["--base-port", str(assigned_port)])

    write_provenance(
        run_dir,
        benchmark="cwah",
        command=command,
        resolved_config={
            "env": args.env,
            "episode_id": episode_id,
            "task_id": run.task_id,
            "seed": run.seed,
            "matrix_index": run.index,
            "max_steps": args.max_steps,
            "max_policy_steps": effective_max_policy_steps,
            "full_episode": args.full_episode,
            "prefer_physical_after_steps": args.prefer_physical_after_steps,
            "navigation_loop_threshold": args.navigation_loop_threshold,
            "base_url": args.base_url,
            "model": args.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "api_key": os.environ.get("CWAH_LLM_API_KEY", ""),
            "base_port": assigned_port,
            "output": str(raw_output),
            "artifact_dir": str(artifact_dir),
            "attempt_id": attempt_id,
            **external_paths,
        },
        environment_notes=f"matrix_child=true; env={args.env}",
        assets=provenance_assets(args, metadata=immutable_model_metadata),
    )

    completed = subprocess.run(command, cwd=Path.cwd(), text=True, capture_output=True, check=False)
    secret_values = (
        getattr(args, "api_key", ""),
        os.environ.get("CWAH_LLM_API_KEY", ""),
    )
    stdout = redact_text(completed.stdout.strip(), secret_values=secret_values)
    stderr = redact_text(completed.stderr.strip(), secret_values=secret_values)
    passed = completed.returncode == 0
    result = {
        "attempt_id": attempt_id,
        "task_id": run.task_id,
        "seed": run.seed,
        "matrix_index": run.index,
        "base_port": assigned_port,
        "run_name": run_name,
        "returncode": completed.returncode,
        "passed": passed,
        "stdout": stdout,
        "stderr": stderr,
        "raw_output": str(raw_output),
        "artifact_dir": str(artifact_dir),
        "provenance": str(run_dir / "provenance.json"),
    }
    summary_path = artifact_dir / "summary.json"
    if passed and summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary_attempt_id = summary.get("attempt_id") or summary.get("run_config", {}).get("attempt_id")
        if summary_attempt_id != attempt_id:
            result["passed"] = False
            result["artifact_error"] = "summary_attempt_mismatch"
        else:
            result["metrics"] = summary.get("metrics", {})
            result["action_counts"] = summary.get("action_counts", {})
            result["event_counts"] = summary.get("event_counts", {})
            result["diagnostics"] = summary.get("diagnostics", {})
    elif passed:
        result["passed"] = False
        result["artifact_error"] = "missing_current_attempt_summary"
    output_failure_counts = failure_reason_counts_from_process_output("\n".join([stdout, stderr]))
    if output_failure_counts:
        diagnostics = result.get("diagnostics", {}) if isinstance(result.get("diagnostics"), dict) else {}
        existing_failure_counts = diagnostics.get("failure_reason_counts", {}) if isinstance(diagnostics.get("failure_reason_counts"), dict) else {}
        if any(reason != "execution_failed" for reason in output_failure_counts):
            existing_failure_counts = {reason: count for reason, count in existing_failure_counts.items() if reason != "execution_failed"}
        diagnostics["failure_reason_counts"] = merge_count_dicts(
            existing_failure_counts,
            output_failure_counts,
        )
        result["diagnostics"] = diagnostics
    provider_metadata = {**immutable_model_metadata, **_provider_metadata(raw_output)}
    if provider_metadata:
        update_provenance_assets(
            run_dir,
            [model_identity(
                name="policy_model",
                provider=model_provider(args.base_url),
                model=args.model,
                metadata=provider_metadata,
            )],
        )
    provenance_status = _child_provenance_status(run_dir, passed=result["passed"])
    result["provenance_status"] = provenance_status
    finalize_provenance(run_dir, status=provenance_status)
    finalize_run_directory(
        run_dir,
        attempt_id=attempt_id,
        producer="benchmarks.cwah.matrix.run",
        status="completed" if result["passed"] else "failed",
    )
    validate_run_attempt(
        run_dir,
        attempt_id=attempt_id,
        require_completed=result["passed"],
    )
    return result


def _provider_metadata(raw_output: Path) -> dict[str, Any]:
    if not raw_output.exists():
        return {}
    try:
        payload = json.loads(raw_output.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    for event in payload.get("events", []):
        metadata = event.get("decision", {}).get("provider_metadata", {})
        if isinstance(metadata, dict) and any(
            metadata.get(key) for key in ("digest", "revision", "system_fingerprint")
        ):
            return metadata
    return {}


def _child_provenance_status(run_dir: Path, *, passed: bool) -> str:
    if passed:
        return "success"
    path = run_dir / "provenance.json"
    if path.exists():
        lifecycle = json.loads(path.read_text(encoding="utf-8")).get("lifecycle", {})
        if lifecycle.get("status") == "timeout":
            return "timeout"
    return "failure"


def _matrix_provenance_status(results: list[dict[str, Any]]) -> str:
    if all(result.get("passed") for result in results):
        return "success"
    if any(result.get("provenance_status") == "timeout" for result in results):
        return "timeout"
    return "failure"


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for result in results if result.get("passed"))
    successes = sum(1 for result in results if result.get("metrics", {}).get("task_success"))
    progress_values = [float(result.get("metrics", {}).get("normalized_progress", 0.0) or 0.0) for result in results]
    return {
        "runs": len(results),
        "passed_runs": passed,
        "failed_runs": len(results) - passed,
        "task_successes": successes,
        "average_progress": sum(progress_values) / len(progress_values) if progress_values else 0.0,
    }


def write_matrix_summary(
    *,
    output_dir: Path,
    results: list[dict[str, Any]],
    attempt_id: str | None = None,
) -> None:
    summary = {"attempt_id": attempt_id, "aggregate": aggregate_results(results), "runs": results}
    (output_dir / "matrix_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output_dir / "matrix_metrics.csv").open("w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "matrix_index",
            "task_id",
            "seed",
            "base_port",
            "passed",
            "task_success",
            "normalized_progress",
            "episode_steps",
            "policy_overrides",
            "failed_action_records",
            "open_failure_records",
            "navigation_loop_count",
            "result_failures",
            "failure_reason_counts",
            "open_failure_reason_counts",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            metrics = result.get("metrics", {})
            event_counts = result.get("event_counts", {})
            diagnostics = result.get("diagnostics", {})
            writer.writerow({
                "task_id": result.get("task_id"),
                "matrix_index": result.get("matrix_index"),
                "seed": result.get("seed"),
                "base_port": result.get("base_port"),
                "passed": result.get("passed"),
                "task_success": metrics.get("task_success"),
                "normalized_progress": metrics.get("normalized_progress"),
                "episode_steps": metrics.get("episode_steps"),
                "policy_overrides": event_counts.get("policy_overrides"),
                "failed_action_records": diagnostics.get("failed_action_record_count"),
                "open_failure_records": diagnostics.get("open_failure_record_count"),
                "navigation_loop_count": diagnostics.get("navigation_loop_count"),
                "result_failures": diagnostics.get("result_failure_count"),
                "failure_reason_counts": json.dumps(diagnostics.get("failure_reason_counts", {}), sort_keys=True),
                "open_failure_reason_counts": json.dumps(diagnostics.get("open_failure_reason_counts", {}), sort_keys=True),
            })


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a C-WAH smoke matrix across task/seed combinations.")
    parser.add_argument("--env", choices=["mock", "coela"], default="mock")
    parser.add_argument("--tasks", default="0")
    parser.add_argument("--seeds", default="0")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-steps", type=int, default=25)
    parser.add_argument("--max-policy-steps", type=int, default=2)
    parser.add_argument("--full-episode", action="store_true")
    parser.add_argument("--prefer-physical-after-steps", type=int, default=2)
    parser.add_argument("--navigation-loop-threshold", type=int, default=12, help="Suppress repeated walktowards signatures after this many episode-local selections; use 0 to disable.")
    parser.add_argument("--base-url", default="http://ollama.arc.upiscium.dev/v1")
    parser.add_argument("--model", default="gemma4:e4b")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--coela-cwah-path", default="")
    parser.add_argument("--dataset-path", default="")
    parser.add_argument("--executable-file", default="")
    parser.add_argument("--base-port", type=int, default=6314)
    parser.add_argument("--port-stride", type=int, default=1, help="Port increment between matrix entries. Ports are assigned as base_port + matrix_index * port_stride.")
    parser.add_argument("--overwrite", action="store_true", help="Explicitly replace an existing non-empty matrix directory")
    return parser.parse_args()


if __name__ == "__main__":
    main()
