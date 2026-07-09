from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MatrixRun:
    index: int
    task_id: int
    seed: int


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    runs = build_matrix(parse_int_list(args.tasks), parse_int_list(args.seeds))
    results = []
    for run in runs:
        results.append(run_matrix_item(args=args, output_dir=output_dir, run=run))
    write_matrix_summary(output_dir=output_dir, results=results)
    print(json.dumps(aggregate_results(results), sort_keys=True))


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
    run_name = f"task_{run.task_id}_seed_{run.seed}"
    run_dir = output_dir / run_name
    raw_output = run_dir / "raw.json"
    artifact_dir = run_dir / "normalized"
    command = [
        sys.executable,
        "-m",
        "benchmarks.cwah.llm_smoke",
        "--env",
        args.env,
        "--episode-id",
        f"cwah-{run_name}",
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
        "--api-key",
        args.api_key,
        "--model",
        args.model,
        "--output",
        str(raw_output),
        "--artifact-dir",
        str(artifact_dir),
    ]
    if args.full_episode:
        command.append("--full-episode")
    if args.coela_cwah_path:
        command.extend(["--coela-cwah-path", args.coela_cwah_path])
    if args.dataset_path:
        command.extend(["--dataset-path", args.dataset_path])
    if args.executable_file:
        command.extend(["--executable-file", args.executable_file])
    assigned_port = None
    if args.base_port:
        assigned_port = matrix_port(base_port=args.base_port, run=run, port_stride=args.port_stride)
        command.extend(["--base-port", str(assigned_port)])

    completed = subprocess.run(command, cwd=Path.cwd(), text=True, capture_output=True, check=False)
    result = {
        "task_id": run.task_id,
        "seed": run.seed,
        "matrix_index": run.index,
        "base_port": assigned_port,
        "run_name": run_name,
        "returncode": completed.returncode,
        "passed": completed.returncode == 0,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "raw_output": str(raw_output),
        "artifact_dir": str(artifact_dir),
    }
    summary_path = artifact_dir / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        result["metrics"] = summary.get("metrics", {})
        result["action_counts"] = summary.get("action_counts", {})
        result["event_counts"] = summary.get("event_counts", {})
        result["diagnostics"] = summary.get("diagnostics", {})
    return result


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


def write_matrix_summary(*, output_dir: Path, results: list[dict[str, Any]]) -> None:
    summary = {"aggregate": aggregate_results(results), "runs": results}
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
            "navigation_loop_count",
            "result_failures",
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
                "navigation_loop_count": diagnostics.get("navigation_loop_count"),
                "result_failures": diagnostics.get("result_failure_count"),
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
    parser.add_argument("--api-key", default="ollama")
    parser.add_argument("--model", default="gemma4:e4b")
    parser.add_argument("--coela-cwah-path", default="")
    parser.add_argument("--dataset-path", default="")
    parser.add_argument("--executable-file", default="")
    parser.add_argument("--base-port", type=int, default=6314)
    parser.add_argument("--port-stride", type=int, default=1, help="Port increment between matrix entries. Ports are assigned as base_port + matrix_index * port_stride.")
    return parser.parse_args()


if __name__ == "__main__":
    main()
