from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


REPORT_FIELDS = [
    "benchmark",
    "run_name",
    "attempt_id",
    "status",
    "task_id",
    "seed",
    "evaluation_unit",
    "episodes",
    "successes",
    "success_rate",
    "task_count",
    "completed_task_count",
    "task_completion_rate",
    "mean_progress",
    "progress_available",
    "mean_steps",
    "steps_available",
    "failed_runs",
    "action_log_available",
    "physical_action_count",
    "communication_action_count",
    "action_counts",
    "policy_override_count",
    "policy_override_rate",
    "failed_action_record_count",
    "open_failure_record_count",
    "navigation_loop_count",
    "result_failure_count",
    "failed_action_counts",
    "failure_reason_counts",
    "open_failure_reason_counts",
    "policy_override_reason_counts",
    "error_type",
    "error_message",
]

_MINECRAFT_COMMUNICATION_ACTIONS = {"talkTo"}


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
        minecraft_metrics = path / "metrics.json"
        craft_summary = path / "normalized" / "summary.json"
        if matrix_summary.exists():
            summary = _read_json(matrix_summary)
            if summary.get("benchmark") == "minecraft":
                return summarize_minecraft_matrix(matrix_summary, summary=summary)
            return summarize_cwah_matrix(matrix_summary)
        if normalized_summary.exists() and minecraft_metrics.exists():
            summary = _read_json(normalized_summary)
            if _looks_like_minecraft_summary(summary):
                return [summarize_minecraft_run(path, summary=summary)]
        if normalized_summary.exists():
            return [summarize_cwah_summary(normalized_summary)]
        if craft_summary.exists():
            return [summarize_craft_run(path)]
    if path.name == "matrix_summary.json":
        summary = _read_json(path)
        if summary.get("benchmark") == "minecraft":
            return summarize_minecraft_matrix(path, summary=summary)
        return summarize_cwah_matrix(path)
    if path.name == "summary.json":
        summary = _read_json(path)
        if summary.get("benchmark") == "cwah":
            return [summarize_cwah_summary(path, summary=summary)]
        if (path.parent / "metrics.json").exists() and _looks_like_minecraft_summary(summary):
            return [summarize_minecraft_run(path.parent, summary=summary)]
        return [summarize_craft_run(path.parent.parent)]
    raise CommonReportInputError(f"Unsupported benchmark report input: {path}")


def summarize_cwah_matrix(path: Path) -> list[dict[str, Any]]:
    summary = _read_json(path)
    rows = []
    for run in summary.get("runs", []):
        row = _base_row(benchmark="cwah", run_name=str(run.get("run_name") or ""))
        metrics = run.get("metrics", {}) if isinstance(run.get("metrics"), dict) else {}
        action_log_available = isinstance(run.get("action_counts"), dict)
        action_counts = run.get("action_counts", {}) if action_log_available else {}
        event_counts = run.get("event_counts", {}) if isinstance(run.get("event_counts"), dict) else {}
        diagnostics = run.get("diagnostics", {}) if isinstance(run.get("diagnostics"), dict) else {}
        policy_steps = int(event_counts.get("policy_steps") or 0)
        policy_overrides = int(event_counts.get("policy_overrides") or 0)
        success_available = metrics.get("task_success") is not None
        row.update({
            "attempt_id": run.get("attempt_id", ""),
            "status": "completed" if run.get("passed") else "failed",
            "task_id": run.get("task_id", ""),
            "seed": run.get("seed", ""),
            "evaluation_unit": "episode",
            "episodes": 1,
            "successes": (1 if metrics.get("task_success") else 0) if success_available else None,
            "success_rate": (1.0 if metrics.get("task_success") else 0.0) if success_available else None,
            "task_count": None,
            "completed_task_count": None,
            "task_completion_rate": None,
            "mean_progress": _as_optional_float(metrics.get("normalized_progress")),
            "progress_available": metrics.get("normalized_progress") is not None,
            "mean_steps": _as_optional_float(metrics.get("episode_steps")),
            "steps_available": metrics.get("episode_steps") is not None,
            "failed_runs": 0 if run.get("passed") else 1,
            "action_log_available": action_log_available,
            "physical_action_count": _physical_action_count(action_counts) if action_log_available else None,
            "communication_action_count": int(action_counts.get("send_message", 0) or 0) if action_log_available else None,
            "action_counts": _json_counts(action_counts) if action_log_available else None,
            "policy_override_count": policy_overrides,
            "policy_override_rate": policy_overrides / policy_steps if policy_steps else 0.0,
            "failed_action_record_count": int(diagnostics.get("failed_action_record_count") or 0),
            "open_failure_record_count": int(diagnostics.get("open_failure_record_count") or 0),
            "navigation_loop_count": int(diagnostics.get("navigation_loop_count") or 0),
            "result_failure_count": int(diagnostics.get("result_failure_count") or 0),
            "failed_action_counts": _json_counts(diagnostics.get("failed_action_counts", {}) if isinstance(diagnostics.get("failed_action_counts"), dict) else {}),
            "failure_reason_counts": _json_counts(diagnostics.get("failure_reason_counts", {}) if isinstance(diagnostics.get("failure_reason_counts"), dict) else {}),
            "open_failure_reason_counts": _json_counts(diagnostics.get("open_failure_reason_counts", {}) if isinstance(diagnostics.get("open_failure_reason_counts"), dict) else {}),
            "policy_override_reason_counts": _json_counts(diagnostics.get("policy_override_reason_counts", {}) if isinstance(diagnostics.get("policy_override_reason_counts"), dict) else {}),
        })
        rows.append(row)
    return rows


def summarize_cwah_summary(path: Path, *, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = summary or _read_json(path)
    run_config = summary.get("run_config", {}) if isinstance(summary.get("run_config"), dict) else {}
    metrics = summary.get("metrics", {}) if isinstance(summary.get("metrics"), dict) else {}
    action_log_available = isinstance(summary.get("action_counts"), dict)
    action_counts = summary.get("action_counts", {}) if action_log_available else {}
    event_counts = summary.get("event_counts", {}) if isinstance(summary.get("event_counts"), dict) else {}
    diagnostics = summary.get("diagnostics", {}) if isinstance(summary.get("diagnostics"), dict) else {}
    policy_steps = int(event_counts.get("policy_steps") or 0)
    policy_overrides = int(event_counts.get("policy_overrides") or 0)
    run_name = run_config.get("episode_id") or path.parent.parent.name or path.parent.name
    success_available = metrics.get("task_success") is not None
    success = bool(metrics.get("task_success"))
    row = _base_row(benchmark="cwah", run_name=str(run_name))
    row.update({
        "attempt_id": summary.get("attempt_id") or run_config.get("attempt_id", ""),
        "status": "completed",
        "task_id": run_config.get("task_id", ""),
        "seed": run_config.get("seed", ""),
        "evaluation_unit": "episode",
        "episodes": 1,
        "successes": (1 if success else 0) if success_available else None,
        "success_rate": (1.0 if success else 0.0) if success_available else None,
        "task_count": None,
        "completed_task_count": None,
        "task_completion_rate": None,
        "mean_progress": _as_optional_float(metrics.get("normalized_progress")),
        "progress_available": metrics.get("normalized_progress") is not None,
        "mean_steps": _as_optional_float(metrics.get("episode_steps")),
        "steps_available": metrics.get("episode_steps") is not None,
        "action_log_available": action_log_available,
        "physical_action_count": _physical_action_count(action_counts) if action_log_available else None,
        "communication_action_count": int(action_counts.get("send_message", 0) or 0) if action_log_available else None,
        "action_counts": _json_counts(action_counts) if action_log_available else None,
        "policy_override_count": policy_overrides,
        "policy_override_rate": policy_overrides / policy_steps if policy_steps else 0.0,
        "failed_action_record_count": int(diagnostics.get("failed_action_record_count") or 0),
        "open_failure_record_count": int(diagnostics.get("open_failure_record_count") or 0),
        "navigation_loop_count": int(diagnostics.get("navigation_loop_count") or 0),
        "result_failure_count": int(diagnostics.get("result_failure_count") or 0),
        "failed_action_counts": _json_counts(diagnostics.get("failed_action_counts", {}) if isinstance(diagnostics.get("failed_action_counts"), dict) else {}),
        "failure_reason_counts": _json_counts(diagnostics.get("failure_reason_counts", {}) if isinstance(diagnostics.get("failure_reason_counts"), dict) else {}),
        "open_failure_reason_counts": _json_counts(diagnostics.get("open_failure_reason_counts", {}) if isinstance(diagnostics.get("open_failure_reason_counts"), dict) else {}),
        "policy_override_reason_counts": _json_counts(diagnostics.get("policy_override_reason_counts", {}) if isinstance(diagnostics.get("policy_override_reason_counts"), dict) else {}),
    })
    return row


def summarize_craft_run(run_dir: Path) -> dict[str, Any]:
    from benchmarks.craft.report import load_run_summary

    row = load_run_summary(run_dir.name, result_root=run_dir.parent)
    normalized_summary = _read_json(run_dir / "normalized" / "summary.json")
    status = row.get("status", "completed")
    episodes = int(row.get("num_games") or 0)
    completion_rate = _as_optional_float(row.get("completion_rate"))
    action_log_available = _craft_action_metrics_available(run_dir)
    observed_turn_count = _jsonl_record_count(run_dir / "normalized" / "turns.jsonl")
    mean_steps = observed_turn_count / episodes if observed_turn_count is not None and episodes else None
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
        "attempt_id": normalized_summary.get("attempt_id", ""),
        "status": status,
        "seed": row.get("seed", ""),
        "evaluation_unit": "game",
        "episodes": episodes,
        "successes": int(round(completion_rate * episodes)) if episodes and completion_rate is not None else None,
        "success_rate": completion_rate,
        "task_count": None,
        "completed_task_count": None,
        "task_completion_rate": None,
        "mean_progress": _as_optional_float(row.get("mean_final_progress")),
        "progress_available": row.get("mean_final_progress") not in (None, ""),
        "mean_steps": mean_steps,
        "steps_available": mean_steps is not None,
        "failed_runs": 0 if status == "completed" else 1,
        "action_log_available": action_log_available,
        "physical_action_count": int(row.get("physical_action_count") or 0) if action_log_available else None,
        "communication_action_count": int(row.get("clarify_count") or 0) if action_log_available else None,
        "action_counts": _json_counts(action_counts) if action_log_available else None,
        "policy_override_count": 0,
        "policy_override_rate": 0.0,
        "failed_action_record_count": 0,
        "open_failure_record_count": 0,
        "navigation_loop_count": 0,
        "result_failure_count": 0,
        "failed_action_counts": _json_counts({}),
        "failure_reason_counts": _json_counts({}),
        "open_failure_reason_counts": _json_counts({}),
        "policy_override_reason_counts": _json_counts({}),
        "error_type": row.get("error_type", ""),
        "error_message": row.get("error_message", ""),
    })
    return common


def summarize_minecraft_run(run_dir: Path, *, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = summary or _read_json(run_dir / "summary.json")
    metrics = _read_json(run_dir / "metrics.json")
    action_log_path = run_dir / "action_log.json"
    action_log_available = bool(summary.get("action_log_available", action_log_path.exists()))
    action_log = _read_optional_json(action_log_path)
    action_counts = _minecraft_action_counts(action_log)
    failed_action_counts = _minecraft_failed_action_counts(action_log)
    status = "failed" if summary.get("error") or metrics.get("error") else "completed"
    task_count = int(metrics.get("task_count") or 0)
    completed_tasks = int(metrics.get("completed_task_count") or 0)
    task_completion_rate = _as_optional_float(metrics.get("task_completion_rate")) if task_count else None
    run_success = task_count > 0 and completed_tasks == task_count
    error_message = str(summary.get("error") or metrics.get("error") or "")
    row = _base_row(benchmark="minecraft", run_name=str(summary.get("run_name") or metrics.get("run_name") or run_dir.name))
    row.update({
        "attempt_id": summary.get("attempt_id", ""),
        "status": status,
        "task_id": _minecraft_task_id(summary),
        "seed": summary.get("seed", ""),
        "evaluation_unit": "run",
        "episodes": 1,
        "successes": (1 if run_success else 0) if task_count else None,
        "success_rate": (1.0 if run_success else 0.0) if task_count else None,
        "task_count": task_count,
        "completed_task_count": completed_tasks,
        "task_completion_rate": task_completion_rate,
        "mean_progress": _as_optional_float(metrics.get("progress")),
        "progress_available": metrics.get("progress") is not None,
        "mean_steps": _as_optional_float(metrics.get("action_count")) if action_log_available else None,
        "steps_available": action_log_available and metrics.get("action_count") is not None,
        "failed_runs": 1 if status != "completed" else 0,
        "action_log_available": action_log_available,
        "physical_action_count": _minecraft_physical_action_count(action_counts) if action_log_available else None,
        "communication_action_count": _minecraft_communication_action_count(action_counts) if action_log_available else None,
        "action_counts": _json_counts(action_counts) if action_log_available else None,
        "policy_override_count": 0,
        "policy_override_rate": 0.0,
        "failed_action_record_count": int(metrics.get("failed_action_count") or 0) if action_log_available else None,
        "open_failure_record_count": 0,
        "navigation_loop_count": 0,
        "result_failure_count": int(metrics.get("failed_action_count") or 0) if action_log_available else None,
        "failed_action_counts": _json_counts(failed_action_counts) if action_log_available else None,
        "failure_reason_counts": _json_counts({}),
        "open_failure_reason_counts": _json_counts({}),
        "policy_override_reason_counts": _json_counts({}),
        "error_type": summary.get("error_type") or ("runtime_error" if error_message else ""),
        "error_message": error_message,
    })
    return row


def summarize_minecraft_matrix(path: Path, *, summary: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    summary = summary or _read_json(path)
    rows = []
    for run in summary.get("runs", []):
        if not isinstance(run, dict):
            continue
        common_row = run.get("common_report")
        if isinstance(common_row, dict):
            rows.append(_minecraft_report_row_from_mapping(common_row, run=run))
            continue
        run_dir = run.get("run_dir")
        if run_dir:
            rows.append(summarize_minecraft_run(Path(run_dir)))
    return rows


def aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    benchmark_names = sorted({str(row.get("benchmark") or "unknown") for row in rows})
    if len(benchmark_names) > 1:
        return {
            "runs": len(rows),
            "failed_runs": sum(int(row.get("failed_runs") or 0) for row in rows),
            "episodes": None,
            "successes": None,
            "success_rate": None,
            "mean_progress": None,
            "mean_steps": None,
            "progress_available_episodes": None,
            "steps_available_episodes": None,
            "action_log_available_runs": None,
            "physical_action_count": None,
            "communication_action_count": None,
            "action_counts": None,
            "by_benchmark": {
                benchmark: aggregate_rows([
                    row for row in rows if str(row.get("benchmark") or "unknown") == benchmark
                ])
                for benchmark in benchmark_names
            },
        }

    episodes = sum(int(row.get("episodes") or 0) for row in rows)
    success_rows = [row for row in rows if row.get("successes") is not None]
    success_episodes = sum(int(row.get("episodes") or 0) for row in success_rows)
    successes = sum(int(row.get("successes") or 0) for row in success_rows)
    action_counts = _aggregate_action_counts(rows)
    failed_action_counts = _aggregate_json_count_field(rows, "failed_action_counts")
    failure_reason_counts = _aggregate_json_count_field(rows, "failure_reason_counts")
    open_failure_reason_counts = _aggregate_json_count_field(rows, "open_failure_reason_counts")
    policy_override_reason_counts = _aggregate_json_count_field(rows, "policy_override_reason_counts")
    return {
        "runs": len(rows),
        "episodes": episodes,
        "successes": successes,
        "success_evaluable_episodes": success_episodes,
        "success_rate": successes / success_episodes if success_episodes else None,
        "task_count": _sum_optional_int(rows, "task_count"),
        "completed_task_count": _sum_optional_int(rows, "completed_task_count"),
        "task_completion_rate": _aggregate_task_completion_rate(rows),
        "mean_progress": _weighted_mean(rows, "mean_progress"),
        "progress_available_episodes": _available_episode_count(rows, "mean_progress"),
        "mean_steps": _weighted_mean(rows, "mean_steps"),
        "steps_available_episodes": _available_episode_count(rows, "mean_steps"),
        "failed_runs": sum(int(row.get("failed_runs") or 0) for row in rows),
        "action_log_available_runs": sum(1 for row in rows if row.get("action_log_available") is True),
        "physical_action_count": _sum_optional_int(rows, "physical_action_count"),
        "communication_action_count": _sum_optional_int(rows, "communication_action_count"),
        "action_counts": action_counts,
        "policy_override_count": sum(int(row.get("policy_override_count") or 0) for row in rows),
        "failed_action_record_count": _sum_optional_int(rows, "failed_action_record_count"),
        "open_failure_record_count": sum(int(row.get("open_failure_record_count") or 0) for row in rows),
        "navigation_loop_count": sum(int(row.get("navigation_loop_count") or 0) for row in rows),
        "result_failure_count": _sum_optional_int(rows, "result_failure_count"),
        "failed_action_counts": failed_action_counts,
        "failure_reason_counts": failure_reason_counts,
        "open_failure_reason_counts": open_failure_reason_counts,
        "policy_override_reason_counts": policy_override_reason_counts,
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
    payload = {"schema_version": 2, "aggregate": aggregate_rows(rows), "runs": rows}
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


def _report_row_from_mapping(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field, "") for field in REPORT_FIELDS}


def _minecraft_report_row_from_mapping(row: dict[str, Any], *, run: dict[str, Any]) -> dict[str, Any]:
    mapped = _report_row_from_mapping(row)
    if mapped.get("evaluation_unit"):
        return mapped

    metrics = run.get("metrics", {}) if isinstance(run.get("metrics"), dict) else {}
    task_count = int(metrics.get("task_count") or 0)
    completed_task_count = int(metrics.get("completed_task_count") or 0)
    run_success = task_count > 0 and completed_task_count == task_count
    action_log_available = mapped.get("action_counts") not in (None, "")
    mapped.update({
        "evaluation_unit": "run",
        "episodes": 1,
        "successes": (1 if run_success else 0) if task_count else None,
        "success_rate": (1.0 if run_success else 0.0) if task_count else None,
        "task_count": task_count,
        "completed_task_count": completed_task_count,
        "task_completion_rate": _as_optional_float(metrics.get("task_completion_rate")) if task_count else None,
        "mean_progress": _as_optional_float(metrics.get("progress")),
        "progress_available": metrics.get("progress") is not None,
        "mean_steps": _as_optional_float(metrics.get("action_count")) if action_log_available else None,
        "steps_available": action_log_available and metrics.get("action_count") is not None,
        "action_log_available": action_log_available,
    })
    return mapped


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CommonReportInputError(f"Missing benchmark report input: {path}")
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise CommonReportInputError(f"Expected JSON object in benchmark report input: {path}")
    return payload


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return _read_json(path)


def _looks_like_minecraft_summary(summary: dict[str, Any]) -> bool:
    return summary.get("mode") in {"dry_run", "execute"} and (
        "artifact_summary" in summary or "execute_real_environment" in summary
    )


def _minecraft_task_id(summary: dict[str, Any]) -> Any:
    if summary.get("task_idx") is not None:
        return summary.get("task_idx")
    return summary.get("selected_task_id") or summary.get("task_name", "")


def _minecraft_action_counts(action_log: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for action in _minecraft_actions(action_log):
        action_type = str(action.get("action") or "unknown")
        counts[action_type] = counts.get(action_type, 0) + 1
    return dict(sorted(counts.items()))


def _minecraft_failed_action_counts(action_log: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for action in _minecraft_actions(action_log):
        result = action.get("result")
        if not isinstance(result, dict) or result.get("status") is not False:
            continue
        action_type = str(action.get("action") or "unknown")
        counts[action_type] = counts.get(action_type, 0) + 1
    return dict(sorted(counts.items()))


def _minecraft_actions(action_log: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(action_log, dict):
        return []
    actions = []
    for entries in action_log.values():
        if not isinstance(entries, list):
            continue
        actions.extend(entry for entry in entries if isinstance(entry, dict))
    return actions


def _craft_action_metrics_available(run_dir: Path) -> bool:
    if (_jsonl_record_count(run_dir / "normalized" / "turns.jsonl") or 0) > 0:
        return True
    metrics_path = run_dir / "normalized" / "metrics.csv"
    if not metrics_path.exists():
        return False
    with metrics_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    action_fields = {
        "physical_action_count",
        "place_action_count",
        "remove_action_count",
        "clarify_count",
        "wait_count",
        "no_op_count",
        "invalid_action_count",
    }
    return any(row.get(field) not in (None, "") for row in rows for field in action_fields)


def _jsonl_record_count(path: Path) -> int | None:
    if not path.exists():
        return None
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _minecraft_physical_action_count(action_counts: dict[str, int]) -> int:
    return sum(
        count
        for action_type, count in action_counts.items()
        if action_type not in _MINECRAFT_COMMUNICATION_ACTIONS
    )


def _minecraft_communication_action_count(action_counts: dict[str, int]) -> int:
    return sum(
        count
        for action_type, count in action_counts.items()
        if action_type in _MINECRAFT_COMMUNICATION_ACTIONS
    )


def _as_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _physical_action_count(action_counts: dict[str, Any]) -> int:
    total = 0
    for action_type, count in action_counts.items():
        if action_type in {"send_message", "wait", "unknown"}:
            continue
        total += int(count or 0)
    return total


def _json_counts(counts: dict[str, Any]) -> str:
    return json.dumps({str(key): int(value or 0) for key, value in counts.items()}, sort_keys=True)


def _aggregate_action_counts(rows: list[dict[str, Any]]) -> dict[str, int] | None:
    if not any(row.get("action_log_available") for row in rows):
        return None
    return _aggregate_json_count_field(rows, "action_counts")


def _sum_optional_int(rows: list[dict[str, Any]], field: str) -> int | None:
    values = [row.get(field) for row in rows if row.get(field) is not None]
    if not values:
        return None
    return sum(int(value or 0) for value in values)


def _weighted_mean(rows: list[dict[str, Any]], field: str) -> float | None:
    weighted_total = 0.0
    total_weight = 0
    for row in rows:
        value = _as_optional_float(row.get(field))
        if value is None:
            continue
        weight = int(row.get("episodes") or 0)
        if weight <= 0:
            continue
        weighted_total += value * weight
        total_weight += weight
    return weighted_total / total_weight if total_weight else None


def _available_episode_count(rows: list[dict[str, Any]], field: str) -> int:
    return sum(
        int(row.get("episodes") or 0)
        for row in rows
        if _as_optional_float(row.get(field)) is not None
    )


def _aggregate_task_completion_rate(rows: list[dict[str, Any]]) -> float | None:
    task_count = _sum_optional_int(rows, "task_count")
    completed_task_count = _sum_optional_int(rows, "completed_task_count")
    if not task_count or completed_task_count is None:
        return None
    return completed_task_count / task_count


def _aggregate_json_count_field(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    totals: dict[str, int] = {}
    for row in rows:
        raw_counts = row.get(field)
        if not raw_counts:
            continue
        try:
            counts = json.loads(raw_counts) if isinstance(raw_counts, str) else raw_counts
        except json.JSONDecodeError:
            continue
        if not isinstance(counts, dict):
            continue
        for action_type, count in counts.items():
            try:
                totals[str(action_type)] = totals.get(str(action_type), 0) + int(count or 0)
            except (TypeError, ValueError):
                continue
    return dict(sorted(totals.items()))


if __name__ == "__main__":
    main()
