from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from benchmarks.common.report import aggregate_rows, summarize_minecraft_run
from benchmarks.experiment_provenance import standard_run_name
from benchmarks.minecraft.experiment import run_minecraft_experiment


DEFAULT_MATRIX_OUTPUT_ROOT = Path("result/minecraft_matrix")


def run_minecraft_matrix(
    *,
    config_path: str | Path,
    output_dir: str | Path = DEFAULT_MATRIX_OUTPUT_ROOT,
    config_indices: list[int] | None = None,
    run_names: list[str] | None = None,
    enable_dual_dag_task_selection: bool = True,
    execute: bool = False,
    execute_timeout_seconds: float | None = None,
    command_text: str | None = None,
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
    matrix_dir.mkdir(parents=True, exist_ok=True)
    run_output_root.mkdir(parents=True, exist_ok=True)

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
            enable_dual_dag_task_selection=True,
            execute=execute,
            execute_timeout_seconds=execute_timeout_seconds,
            command_text=command_text or _command_text(),
        )
        run_dir = Path(summary["output_dir"])
        common_row = summarize_minecraft_run(run_dir, summary=summary)
        metrics = _read_json(run_dir / "metrics.json")
        result = {
            "benchmark": "minecraft",
            "matrix_index": matrix_index,
            "config_index": config_index,
            "run_name": summary.get("run_name", ""),
            "run_dir": str(run_dir),
            "mode": summary.get("mode", ""),
            "execute_timeout_seconds": summary.get("execute_timeout_seconds"),
            "passed": summary.get("error") is None,
            "task_type": summary.get("task_type", ""),
            "task_idx": summary.get("task_idx"),
            "progress": summary.get("progress"),
            "metrics": metrics,
            "common_report": common_row,
        }
        results.append(result)
        common_rows.append(common_row)

    payload = {
        "benchmark": "minecraft",
        "mode": "execute" if execute else "dry_run",
        "matrix_output_dir": str(matrix_dir),
        "run_output_root": str(run_output_root),
        "run_count": len(results),
        "dual_dag_runtime_enabled": True,
        "dual_dag_task_selection_enabled": True,
        "execute_timeout_seconds": execute_timeout_seconds,
        "aggregate": aggregate_rows(common_rows),
        "runs": results,
    }
    _write_json(matrix_dir / "matrix_summary.json", payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = run_minecraft_matrix(
        config_path=args.config,
        output_dir=args.output_dir,
        config_indices=_parse_indices(args.config_indices),
        run_names=_parse_run_names(args.run_names),
        enable_dual_dag_task_selection=args.dual_dag_task_selection,
        execute=args.execute,
        execute_timeout_seconds=args.execute_timeout_seconds,
        command_text=_command_text(args),
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["aggregate"].get("failed_runs", 0) == 0 else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a Minecraft benchmark matrix.")
    parser.add_argument("--config", required=True, help="Launch config JSON object or list file")
    parser.add_argument("--output-dir", default=str(DEFAULT_MATRIX_OUTPUT_ROOT))
    parser.add_argument("--config-indices", default="", help="Comma-separated config indices; defaults to all entries")
    parser.add_argument("--run-names", default="", help="Comma-separated run names matching selected config indices")
    parser.add_argument("--dual-dag-task-selection", action="store_true", help="Compatibility flag; Dual-DAG runtime task selection is always enabled")
    parser.add_argument("--execute", action="store_true", help="Explicitly run the real Minecraft environment")
    parser.add_argument("--execute-timeout-seconds", type=float, default=None, help="Bound real execute mode and preserve artifacts on timeout")
    return parser.parse_args(argv)


def _load_config_entries(config_path: str | Path) -> list[dict[str, Any]]:
    payload = _read_json(Path(config_path))
    if isinstance(payload, list):
        return [dict(item) for item in payload]
    if isinstance(payload, dict):
        return [dict(payload)]
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
    if args.dual_dag_task_selection:
        parts.append("--dual-dag-task-selection")
    if args.execute:
        parts.append("--execute")
    if args.execute_timeout_seconds is not None:
        parts.extend(["--execute-timeout-seconds", str(args.execute_timeout_seconds)])
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
