from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from benchmarks.common.report import aggregate_rows, summarize_minecraft_run
from benchmarks.common.run_artifacts import (
    finalize_run_directory,
    prepare_run_directory,
    validate_run_attempt,
)
from benchmarks.experiment_provenance import (
    file_identity,
    finalize_provenance,
    standard_run_name,
    write_provenance,
)
from benchmarks.minecraft.experiment import TASK_SELECTION_POLICIES, run_minecraft_experiment, validate_minecraft_config


DEFAULT_MATRIX_OUTPUT_ROOT = Path("result/minecraft_matrix")


def run_minecraft_matrix(
    *,
    config_path: str | Path,
    output_dir: str | Path = DEFAULT_MATRIX_OUTPUT_ROOT,
    config_indices: list[int] | None = None,
    run_names: list[str] | None = None,
    enable_dual_dag_task_selection: bool = True,
    task_selection_policy: str = "dual-dag",
    execute: bool = False,
    execute_timeout_seconds: float | None = None,
    retain_runtime_result: bool = False,
    command_text: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    attempt_state: dict = {}
    try:
        return _run_minecraft_matrix_attempt(
            config_path=config_path,
            output_dir=output_dir,
            config_indices=config_indices,
            run_names=run_names,
            enable_dual_dag_task_selection=enable_dual_dag_task_selection,
            task_selection_policy=task_selection_policy,
            execute=execute,
            execute_timeout_seconds=execute_timeout_seconds,
            retain_runtime_result=retain_runtime_result,
            command_text=command_text,
            overwrite=overwrite,
            attempt_state=attempt_state,
        )
    except BaseException:
        if attempt_state:
            finalize_provenance(attempt_state["output_dir"], status="failure")
            finalize_run_directory(
                attempt_state["output_dir"],
                attempt_id=attempt_state["attempt_id"],
                producer="benchmarks.minecraft.matrix",
                status="failed",
                stamp_nested=False,
            )
        raise


def _run_minecraft_matrix_attempt(
    *,
    config_path: str | Path,
    output_dir: str | Path,
    config_indices: list[int] | None,
    run_names: list[str] | None,
    enable_dual_dag_task_selection: bool,
    task_selection_policy: str,
    execute: bool,
    execute_timeout_seconds: float | None,
    retain_runtime_result: bool,
    command_text: str | None,
    overwrite: bool,
    attempt_state: dict,
) -> dict[str, Any]:
    """Run a CI-safe Minecraft benchmark matrix and write a matrix summary.

    Dry-run remains the default. ``execute=True`` is explicit and preserves the
    single-run harness as the only path that touches the real environment.
    """
    configs = _load_config_entries(config_path)
    selected_indices = config_indices if config_indices is not None else list(range(len(configs)))
    if not selected_indices:
        raise ValueError("Expected at least one Minecraft config index.")
    if run_names is not None and len(run_names) != len(selected_indices):
        raise ValueError("run_names length must match config_indices length.")

    matrix_dir = Path(output_dir)
    run_output_root = matrix_dir / "runs"
    attempt_id = prepare_run_directory(
        matrix_dir,
        producer="benchmarks.minecraft.matrix",
        overwrite=overwrite,
    )
    attempt_state.update({"output_dir": matrix_dir, "attempt_id": attempt_id})
    run_output_root.mkdir(parents=True, exist_ok=True)

    run_plan = [
        {
            "matrix_index": matrix_index,
            "config_index": config_index,
            "run_name": (
                run_names[matrix_index]
                if run_names is not None
                else _default_matrix_run_name(configs[config_index], config_index=config_index)
            ),
        }
        for matrix_index, config_index in enumerate(selected_indices)
    ]
    write_provenance(
        matrix_dir,
        benchmark="minecraft",
        command=command_text or _command_text(),
        resolved_config={
            "config_path": str(config_path),
            "run_plan": run_plan,
            "task_selection_policy": task_selection_policy,
            "execute": execute,
            "execute_timeout_seconds": execute_timeout_seconds,
            "retain_runtime_result": retain_runtime_result,
            "attempt_id": attempt_id,
        },
        environment_notes="matrix=true; real_environment_execute=" + str(bool(execute)).lower(),
        assets=[file_identity(config_path, name="matrix_config", kind="task")],
    )

    results = []
    common_rows = []
    for matrix_index, config_index in enumerate(selected_indices):
        if config_index < 0 or config_index >= len(configs):
            raise IndexError(f"Minecraft config index out of range: {config_index}")
        run_name = (
            run_names[matrix_index]
            if run_names is not None
            else _default_matrix_run_name(configs[config_index], config_index=config_index)
        )
        summary = run_minecraft_experiment(
            config_path=config_path,
            output_root=run_output_root,
            run_name=run_name,
            config_index=config_index,
            enable_dual_dag_task_selection=enable_dual_dag_task_selection,
            task_selection_policy=configs[config_index].get("task_selection_policy", task_selection_policy),
            execute=execute,
            execute_timeout_seconds=execute_timeout_seconds,
            retain_runtime_result=retain_runtime_result,
            command_text=command_text or _command_text(),
        )
        run_dir = Path(summary["output_dir"])
        validate_run_attempt(
            run_dir,
            attempt_id=summary["attempt_id"],
            require_completed=summary.get("error") is None,
        )
        common_row = summarize_minecraft_run(run_dir, summary=summary)
        metrics = _read_json(run_dir / "metrics.json")
        result = {
            "benchmark": "minecraft",
            "matrix_index": matrix_index,
            "config_index": config_index,
            "run_name": summary.get("run_name", ""),
            "attempt_id": summary.get("attempt_id", ""),
            "run_dir": str(run_dir),
            "provenance": str(run_dir / "provenance.json"),
            "mode": summary.get("mode", ""),
            "execute_timeout_seconds": summary.get("execute_timeout_seconds"),
            "passed": summary.get("error") is None,
            "task_type": summary.get("task_type", ""),
            "task_idx": summary.get("task_idx"),
            "progress": summary.get("progress"),
            "task_selection_policy": summary.get("task_selection_policy", ""),
            "runtime_result_retained": bool(summary.get("runtime_result_retained", False)),
            "metrics": metrics,
            "common_report": common_row,
        }
        results.append(result)
        common_rows.append(common_row)

    payload = {
        "attempt_id": attempt_id,
        "benchmark": "minecraft",
        "mode": "execute" if execute else "dry_run",
        "matrix_output_dir": str(matrix_dir),
        "run_output_root": str(run_output_root),
        "run_count": len(results),
        "dual_dag_runtime_enabled": True,
        "dual_dag_task_selection_enabled": any(
            run.get("task_selection_policy") == "dual-dag" for run in results
        ),
        "task_selection_policy": task_selection_policy,
        "execute_timeout_seconds": execute_timeout_seconds,
        "runtime_result_retained": any(run["runtime_result_retained"] for run in results),
        "aggregate": aggregate_rows(common_rows),
        "runs": results,
    }
    _write_json(matrix_dir / "matrix_summary.json", payload)
    provenance_status = "success" if all(run["passed"] for run in results) else "failure"
    finalize_provenance(matrix_dir, status=provenance_status)
    finalize_run_directory(
        matrix_dir,
        attempt_id=attempt_id,
        producer="benchmarks.minecraft.matrix",
        status="completed" if all(run["passed"] for run in results) else "failed",
        stamp_nested=False,
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = run_minecraft_matrix(
        config_path=args.config,
        output_dir=args.output_dir,
        config_indices=_parse_indices(args.config_indices),
        run_names=_parse_run_names(args.run_names),
        enable_dual_dag_task_selection=args.dual_dag_task_selection,
        task_selection_policy=args.task_selection_policy,
        execute=args.execute,
        execute_timeout_seconds=args.execute_timeout_seconds,
        retain_runtime_result=args.retain_runtime_result,
        command_text=_command_text(args),
        overwrite=args.overwrite,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["aggregate"].get("failed_runs", 0) == 0 else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a Minecraft benchmark matrix.")
    parser.add_argument("--config", required=True, help="Launch config JSON object or list file")
    parser.add_argument("--output-dir", default=str(DEFAULT_MATRIX_OUTPUT_ROOT))
    parser.add_argument("--config-indices", default="", help="Comma-separated config indices; defaults to all entries")
    parser.add_argument("--run-names", default="", help="Comma-separated run names matching selected config indices")
    parser.add_argument("--task-selection-policy", choices=TASK_SELECTION_POLICIES, default="dual-dag", help="Default runtime task ordering policy")
    parser.add_argument("--dual-dag-task-selection", action="store_true", help="Deprecated compatibility flag; equivalent to --task-selection-policy dual-dag")
    parser.add_argument("--no-dual-dag-task-selection", action="store_true", help="Deprecated compatibility flag; equivalent to --task-selection-policy original")
    parser.add_argument("--execute", action="store_true", help="Explicitly run the real Minecraft environment")
    parser.add_argument("--execute-timeout-seconds", type=float, default=None, help="Bound real execute mode and preserve artifacts on timeout")
    parser.add_argument("--retain-runtime-result", action="store_true", help="Keep each run's internal runtime result after artifact normalization")
    parser.add_argument("--overwrite", action="store_true", help="Explicitly replace an existing non-empty matrix directory")
    args = parser.parse_args(argv)
    if args.no_dual_dag_task_selection:
        args.task_selection_policy = "original"
        args.dual_dag_task_selection = False
    elif args.dual_dag_task_selection:
        args.task_selection_policy = "dual-dag"
    return args


def _load_config_entries(config_path: str | Path) -> list[dict[str, Any]]:
    payload = _read_json(Path(config_path))
    if isinstance(payload, list):
        entries = []
        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                raise ValueError(f"Minecraft config entry at index {index} must be an object")
            entries.append(validate_minecraft_config(dict(item), context=f"config[{index}]"))
        return entries
    if isinstance(payload, dict):
        return [validate_minecraft_config(dict(payload), context="config")]
    raise ValueError(f"Unsupported Minecraft config shape: {config_path}")


def _parse_indices(value: str) -> list[int] | None:
    if not value.strip():
        return None
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def _parse_run_names(value: str) -> list[str] | None:
    if not value.strip():
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _default_matrix_run_name(config: dict[str, Any], *, config_index: int) -> str:
    base_name = config.get("task_name") or config.get("task_goal") or "minecraft"
    return standard_run_name(f"config_{config_index}_{base_name}")


def _command_text(args: argparse.Namespace | None = None) -> str:
    if args is None:
        return "python -m benchmarks.minecraft.matrix"
    parts = ["python -m benchmarks.minecraft.matrix", "--config", args.config]
    if args.output_dir != str(DEFAULT_MATRIX_OUTPUT_ROOT):
        parts.extend(["--output-dir", args.output_dir])
    if args.config_indices:
        parts.extend(["--config-indices", args.config_indices])
    if args.run_names:
        parts.extend(["--run-names", args.run_names])
    if args.task_selection_policy != "dual-dag":
        parts.extend(["--task-selection-policy", args.task_selection_policy])
    if args.dual_dag_task_selection:
        parts.append("--dual-dag-task-selection")
    if args.no_dual_dag_task_selection:
        parts.append("--no-dual-dag-task-selection")
    if args.execute:
        parts.append("--execute")
    if args.execute_timeout_seconds is not None:
        parts.extend(["--execute-timeout-seconds", str(args.execute_timeout_seconds)])
    if args.retain_runtime_result:
        parts.append("--retain-runtime-result")
    if getattr(args, "overwrite", False):
        parts.append("--overwrite")
    return " ".join(parts)


def _read_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
