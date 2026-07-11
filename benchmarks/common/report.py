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
        action_counts = run.get("action_counts", {}) if isinstance(run.get("action_counts"), dict) else {}
        event_counts = run.get("event_counts", {}) if isinstance(run.get("event_counts"), dict) else {}
        diagnostics = run.get("diagnostics", {}) if isinstance(run.get("diagnostics"), dict) else {}
        policy_steps = int(event_counts.get("policy_steps") or 0)
        policy_overrides = int(event_counts.get("policy_overrides") or 0)
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
    action_counts = summary.get("action_counts", {}) if isinstance(summary.get("action_counts"), dict) else {}
    event_counts = summary.get("event_counts", {}) if isinstance(summary.get("event_counts"), dict) else {}
    diagnostics = summary.get("diagnostics", {}) if isinstance(summary.get("diagnostics"), dict) else {}
    policy_steps = int(event_counts.get("policy_steps") or 0)
    policy_overrides = int(event_counts.get("policy_overrides") or 0)
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
    action_log = _read_optional_json(run_dir / "action_log.json")
    action_counts = _minecraft_action_counts(action_log)
    failed_action_counts = _minecraft_failed_action_counts(action_log)
    status = "failed" if summary.get("error") or metrics.get("error") else "completed"
    task_count = int(metrics.get("task_count") or 0)
    completed_tasks = int(metrics.get("completed_task_count") or 0)
    success_rate = _as_float(metrics.get("task_completion_rate")) if task_count else 0.0
    error_message = str(summary.get("error") or metrics.get("error") or "")
    row = _base_row(benchmark="minecraft", run_name=str(summary.get("run_name") or metrics.get("run_name") or run_dir.name))
    row.update({
        "status": status,
        "task_id": _minecraft_task_id(summary),
        "seed": summary.get("seed", ""),
        "episodes": 1,
        "successes": completed_tasks,
        "success_rate": success_rate,
        "mean_progress": _as_float(metrics.get("progress")),
        "mean_steps": _as_float(metrics.get("action_count")),
        "failed_runs": 1 if status != "completed" else 0,
        "physical_action_count": _minecraft_physical_action_count(action_counts),
        "communication_action_count": _minecraft_communication_action_count(action_counts),
        "action_counts": _json_counts(action_counts),
        "policy_override_count": 0,
        "policy_override_rate": 0.0,
        "failed_action_record_count": int(metrics.get("failed_action_count") or 0),
        "open_failure_record_count": 0,
        "navigation_loop_count": 0,
        "result_failure_count": int(metrics.get("failed_action_count") or 0),
        "failed_action_counts": _json_counts(failed_action_counts),
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
            rows.append(_report_row_from_mapping(common_row))
            continue
        run_dir = run.get("run_dir")
        if run_dir:
            rows.append(summarize_minecraft_run(Path(run_dir)))
    return rows


def aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    episodes = sum(int(row.get("episodes") or 0) for row in rows)
    successes = sum(int(row.get("successes") or 0) for row in rows)
    progress_values = [_as_float(row.get("mean_progress")) for row in rows]
    step_values = [_as_float(row.get("mean_steps")) for row in rows]
    action_counts = _aggregate_action_counts(rows)
    failed_action_counts = _aggregate_json_count_field(rows, "failed_action_counts")
    failure_reason_counts = _aggregate_json_count_field(rows, "failure_reason_counts")
    open_failure_reason_counts = _aggregate_json_count_field(rows, "open_failure_reason_counts")
    policy_override_reason_counts = _aggregate_json_count_field(rows, "policy_override_reason_counts")
    return {
        "runs": len(rows),
        "episodes": episodes,
        "successes": successes,
        "success_rate": successes / episodes if episodes else 0.0,
        "mean_progress": sum(progress_values) / len(progress_values) if progress_values else 0.0,
        "mean_steps": sum(step_values) / len(step_values) if step_values else 0.0,
        "failed_runs": sum(int(row.get("failed_runs") or 0) for row in rows),
        "physical_action_count": sum(int(row.get("physical_action_count") or 0) for row in rows),
        "communication_action_count": sum(int(row.get("communication_action_count") or 0) for row in rows),
        "action_counts": action_counts,
        "policy_override_count": sum(int(row.get("policy_override_count") or 0) for row in rows),
        "failed_action_record_count": sum(int(row.get("failed_action_record_count") or 0) for row in rows),
        "open_failure_record_count": sum(int(row.get("open_failure_record_count") or 0) for row in rows),
        "navigation_loop_count": sum(int(row.get("navigation_loop_count") or 0) for row in rows),
        "result_failure_count": sum(int(row.get("result_failure_count") or 0) for row in rows),
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


def _report_row_from_mapping(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field, "") for field in REPORT_FIELDS}


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


def _aggregate_action_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return _aggregate_json_count_field(rows, "action_counts")


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
