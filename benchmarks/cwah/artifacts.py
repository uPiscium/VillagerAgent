from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from benchmarks.common.sanitization import sanitize_artifact_value
from benchmarks.cwah.failure_diagnostics import failure_reason_counts_from_messages


def write_normalized_artifacts(
    *,
    artifact_dir: Path,
    run_config: dict[str, Any],
    events: list[dict[str, Any]],
    metrics: dict[str, Any],
    dual_dag_snapshot: dict[str, Any] | None = None,
    secret_values: tuple[str, ...] = (),
) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    sanitized_run_config = sanitize_artifact_value(run_config, secret_values=secret_values)
    sanitized_events = sanitize_artifact_value(events, secret_values=secret_values)
    sanitized_metrics = sanitize_artifact_value(metrics, secret_values=secret_values)
    summary = build_summary(
        run_config=sanitized_run_config,
        events=sanitized_events,
        metrics=sanitized_metrics,
    )
    _write_json(artifact_dir / "summary.json", summary)
    _write_turns(artifact_dir / "turns.jsonl", sanitized_events)
    _write_metrics(artifact_dir / "metrics.csv", summary)
    if dual_dag_snapshot is not None:
        _write_json(
            artifact_dir / "dual_dag_artifact.json",
            sanitize_artifact_value(dual_dag_snapshot, secret_values=secret_values),
        )


def build_summary(*, run_config: dict[str, Any], events: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
    policy_events = [event for event in events if event.get("event") == "policy_step"]
    action_counts: dict[str, int] = {}
    override_count = 0
    override_reason_counts: dict[str, int] = {}
    failed_action_counts: dict[str, int] = {}
    failure_messages: list[str] = []
    open_failure_messages: list[str] = []
    failed_action_record_count = 0
    open_failure_record_count = 0
    navigation_loop_count = 0
    result_failure_count = 0
    for event in policy_events:
        action_type = _action_type_from_event(event)
        action_counts[action_type] = action_counts.get(action_type, 0) + 1
        decision = event.get("decision", {}) if isinstance(event.get("decision"), dict) else {}
        policy_override = decision.get("policy_override") if isinstance(decision.get("policy_override"), dict) else {}
        if policy_override:
            override_count += 1
            reason = str(policy_override.get("reason", "unknown"))
            override_reason_counts[reason] = override_reason_counts.get(reason, 0) + 1
        failed_record = decision.get("failed_action_recorded") if isinstance(decision.get("failed_action_recorded"), dict) else {}
        has_failed_record = bool(failed_record)
        if failed_record:
            failed_action_record_count += 1
            failed_action_type = _action_type_from_id(str(failed_record.get("action_id", "")))
            failed_action_counts[failed_action_type] = failed_action_counts.get(failed_action_type, 0) + 1
            failure_messages.append(str(failed_record.get("error", "")))
        navigation_loop = decision.get("navigation_loop_recorded") if isinstance(decision.get("navigation_loop_recorded"), dict) else {}
        if navigation_loop:
            navigation_loop_count += 1
        open_failure = decision.get("open_failure_recorded") if isinstance(decision.get("open_failure_recorded"), dict) else {}
        if open_failure:
            open_failure_record_count += 1
            open_failure_messages.append(str(open_failure.get("error", "")))
        result = event.get("result", {}) if isinstance(event.get("result"), dict) else {}
        if result.get("succeeded") is False or result.get("error"):
            result_failure_count += 1
            if not has_failed_record:
                failure_messages.append(str(result.get("error", "")))
    return {
        "schema_version": 1,
        "benchmark": "cwah",
        "run_config": run_config,
        "metrics": metrics,
        "event_counts": {
            "total_events": len(events),
            "policy_steps": len(policy_events),
            "policy_overrides": override_count,
        },
        "action_counts": action_counts,
        "diagnostics": {
            "policy_override_reason_counts": dict(sorted(override_reason_counts.items())),
            "failed_action_record_count": failed_action_record_count,
            "failed_action_counts": dict(sorted(failed_action_counts.items())),
            "failure_reason_counts": failure_reason_counts_from_messages(failure_messages),
            "open_failure_record_count": open_failure_record_count,
            "open_failure_reason_counts": failure_reason_counts_from_messages(open_failure_messages),
            "navigation_loop_count": navigation_loop_count,
            "result_failure_count": result_failure_count,
        },
    }


def _action_type_from_event(event: dict[str, Any]) -> str:
    decision = event.get("decision", {})
    override = decision.get("policy_override", {}) if isinstance(decision, dict) else {}
    action_id = override.get("action_id") or decision.get("action_id") or decision.get("action_type")
    if isinstance(action_id, str) and ":" in action_id:
        return action_id.split(":", 1)[0]
    if isinstance(action_id, str) and action_id:
        return action_id
    result_metrics = event.get("result", {}).get("metrics", {})
    if result_metrics.get("communication_count"):
        return "send_message"
    return "unknown"


def _action_type_from_id(action_id: str) -> str:
    if ":" in action_id:
        return action_id.split(":", 1)[0]
    return action_id or "unknown"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, default=_json_default, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_turns(path: Path, events: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, default=_json_default, ensure_ascii=False) + "\n")


def _write_metrics(path: Path, summary: dict[str, Any]) -> None:
    row = {
        "benchmark": summary["benchmark"],
        "episode_id": summary["run_config"].get("episode_id"),
        "env": summary["run_config"].get("env"),
        "task_id": summary["run_config"].get("task_id"),
        "seed": summary["run_config"].get("seed"),
        "task_success": summary["metrics"].get("task_success"),
        "normalized_progress": summary["metrics"].get("normalized_progress"),
        "episode_steps": summary["metrics"].get("episode_steps"),
        "policy_steps": summary["event_counts"].get("policy_steps"),
        "policy_overrides": summary["event_counts"].get("policy_overrides"),
        "policy_override_rate": _rate(summary["event_counts"].get("policy_overrides"), summary["event_counts"].get("policy_steps")),
        "failed_action_records": summary.get("diagnostics", {}).get("failed_action_record_count", 0),
        "open_failure_records": summary.get("diagnostics", {}).get("open_failure_record_count", 0),
        "navigation_loop_count": summary.get("diagnostics", {}).get("navigation_loop_count", 0),
        "result_failures": summary.get("diagnostics", {}).get("result_failure_count", 0),
        "physical_actions": _physical_action_count(summary["action_counts"]),
        "communication_actions": summary["action_counts"].get("send_message", 0),
    }
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)


def _physical_action_count(action_counts: dict[str, int]) -> int:
    return sum(count for action_type, count in action_counts.items() if action_type not in {"send_message", "wait", "unknown"})


def _rate(numerator: Any, denominator: Any) -> float:
    try:
        denominator_value = float(denominator or 0)
        if denominator_value == 0:
            return 0.0
        return float(numerator or 0) / denominator_value
    except (TypeError, ValueError):
        return 0.0


def _json_default(value: Any) -> Any:
    if hasattr(value, "__dict__"):
        return value.__dict__
    if isinstance(value, (frozenset, set, tuple)):
        return list(value)
    return str(value)
