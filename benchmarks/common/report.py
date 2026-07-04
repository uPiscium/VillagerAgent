from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


REPORT_FIELDS = [
    "benchmark",
    "run_name",
    "status",
    "task_id",
    "seed",
    "episodes",
    "successes",
    "success_rate",
    "mean_progress",
    "mean_steps",
    "failed_runs",
    "physical_action_count",
    "communication_action_count",
    "action_counts",
    "error_type",
    "error_message",
]


class CommonReportInputError(ValueError):
    """Raised when benchmark artifacts cannot be summarized."""


def summarize_inputs(inputs: list[Path]) -> list[dict[str, Any]]:
    if not inputs:
        raise CommonReportInputError("At least one input path is required.")
    rows: list[dict[str, Any]] = []
    for path in inputs:
        rows.extend(summarize_path(path))
    return rows


def summarize_path(path: Path) -> list[dict[str, Any]]:
    if path.is_dir():
        matrix_summary = path / "matrix_summary.json"
        normalized_summary = path / "summary.json"
        craft_summary = path / "normalized" / "summary.json"
        if matrix_summary.exists():
            return summarize_cwah_matrix(matrix_summary)
        if normalized_summary.exists():
            return [summarize_cwah_summary(normalized_summary)]
        if craft_summary.exists():
            return [summarize_craft_run(path)]
    if path.name == "matrix_summary.json":
        return summarize_cwah_matrix(path)
    if path.name == "summary.json":
        summary = _read_json(path)
        if summary.get("benchmark") == "cwah":
            return [summarize_cwah_summary(path, summary=summary)]
        return [summarize_craft_run(path.parent.parent)]
    raise CommonReportInputError(f"Unsupported benchmark report input: {path}")


def summarize_cwah_matrix(path: Path) -> list[dict[str, Any]]:
    summary = _read_json(path)
    rows = []
    for run in summary.get("runs", []):
        row = _base_row(benchmark="cwah", run_name=str(run.get("run_name") or ""))
        metrics = run.get("metrics", {}) if isinstance(run.get("metrics"), dict) else {}
        action_counts = run.get("action_counts", {}) if isinstance(run.get("action_counts"), dict) else {}
        row.update({
            "status": "completed" if run.get("passed") else "failed",
            "task_id": run.get("task_id", ""),
            "seed": run.get("seed", ""),
            "episodes": 1,
            "successes": 1 if metrics.get("task_success") else 0,
            "success_rate": 1.0 if metrics.get("task_success") else 0.0,
            "mean_progress": _as_float(metrics.get("normalized_progress")),
            "mean_steps": _as_float(metrics.get("episode_steps")),
            "failed_runs": 0 if run.get("passed") else 1,
            "physical_action_count": _physical_action_count(action_counts),
            "communication_action_count": int(action_counts.get("send_message", 0) or 0),
            "action_counts": _json_counts(action_counts),
        })
        rows.append(row)
    return rows


def summarize_cwah_summary(path: Path, *, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = summary or _read_json(path)
    run_config = summary.get("run_config", {}) if isinstance(summary.get("run_config"), dict) else {}
    metrics = summary.get("metrics", {}) if isinstance(summary.get("metrics"), dict) else {}
    action_counts = summary.get("action_counts", {}) if isinstance(summary.get("action_counts"), dict) else {}
    run_name = run_config.get("episode_id") or path.parent.parent.name or path.parent.name
    success = bool(metrics.get("task_success"))
    row = _base_row(benchmark="cwah", run_name=str(run_name))
    row.update({
        "status": "completed",
        "task_id": run_config.get("task_id", ""),
        "seed": run_config.get("seed", ""),
        "episodes": 1,
        "successes": 1 if success else 0,
        "success_rate": 1.0 if success else 0.0,
        "mean_progress": _as_float(metrics.get("normalized_progress")),
        "mean_steps": _as_float(metrics.get("episode_steps")),
        "physical_action_count": _physical_action_count(action_counts),
        "communication_action_count": int(action_counts.get("send_message", 0) or 0),
        "action_counts": _json_counts(action_counts),
    })
    return row


def summarize_craft_run(run_dir: Path) -> dict[str, Any]:
    from benchmarks.craft.report import load_run_summary

    row = load_run_summary(run_dir.name, result_root=run_dir.parent)
    status = row.get("status", "completed")
    episodes = int(row.get("num_games") or 0)
    completion_rate = _as_float(row.get("completion_rate"))
    action_counts = {
        "place": int(row.get("place_action_count") or 0),
        "remove": int(row.get("remove_action_count") or 0),
        "clarify": int(row.get("clarify_count") or 0),
        "wait": int(row.get("wait_count") or 0),
        "fallback": int(row.get("fallback_count") or 0),
        "no_op": int(row.get("no_op_count") or 0),
        "invalid": int(row.get("invalid_action_count") or 0),
    }
    common = _base_row(benchmark="craft", run_name=str(row.get("run_name") or run_dir.name))
    common.update({
        "status": status,
        "seed": row.get("seed", ""),
        "episodes": episodes,
        "successes": int(round(completion_rate * episodes)) if episodes else 0,
        "success_rate": completion_rate,
        "mean_progress": _as_float(row.get("mean_final_progress")),
        "mean_steps": _as_float(row.get("turns")),
        "failed_runs": 0 if status == "completed" else 1,
        "physical_action_count": int(row.get("physical_action_count") or 0),
        "communication_action_count": int(row.get("clarify_count") or 0),
        "action_counts": _json_counts(action_counts),
        "error_type": row.get("error_type", ""),
        "error_message": row.get("error_message", ""),
    })
    return common


def aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    episodes = sum(int(row.get("episodes") or 0) for row in rows)
    successes = sum(int(row.get("successes") or 0) for row in rows)
    progress_values = [_as_float(row.get("mean_progress")) for row in rows]
    step_values = [_as_float(row.get("mean_steps")) for row in rows]
    return {
        "runs": len(rows),
        "episodes": episodes,
        "successes": successes,
        "success_rate": successes / episodes if episodes else 0.0,
        "mean_progress": sum(progress_values) / len(progress_values) if progress_values else 0.0,
        "mean_steps": sum(step_values) / len(step_values) if step_values else 0.0,
        "failed_runs": sum(int(row.get("failed_runs") or 0) for row in rows),
    }


def write_csv_report(rows: list[dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in REPORT_FIELDS})


def write_json_report(rows: list[dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "aggregate": aggregate_rows(rows), "runs": rows}
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize normalized benchmark artifacts with a common schema.")
    parser.add_argument("inputs", nargs="+", help="C-WAH matrix dirs/files, C-WAH normalized dirs, or CRAFT run dirs.")
    parser.add_argument("--output", required=True, help="CSV output path.")
    parser.add_argument("--json-output", default="", help="Optional JSON output path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = summarize_inputs([Path(item) for item in args.inputs])
    write_csv_report(rows, Path(args.output))
    if args.json_output:
        write_json_report(rows, Path(args.json_output))
    print(f"Wrote common benchmark report: {args.output}")


def _base_row(*, benchmark: str, run_name: str) -> dict[str, Any]:
    return {field: "" for field in REPORT_FIELDS} | {
        "benchmark": benchmark,
        "run_name": run_name,
        "failed_runs": 0,
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CommonReportInputError(f"Missing benchmark report input: {path}")
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise CommonReportInputError(f"Expected JSON object in benchmark report input: {path}")
    return payload


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _physical_action_count(action_counts: dict[str, Any]) -> int:
    total = 0
    for action_type, count in action_counts.items():
        if action_type in {"send_message", "wait", "unknown"}:
            continue
        total += int(count or 0)
    return total


def _json_counts(counts: dict[str, Any]) -> str:
    return json.dumps({str(key): int(value or 0) for key, value in counts.items()}, sort_keys=True)


if __name__ == "__main__":
    main()
