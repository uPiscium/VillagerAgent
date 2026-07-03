from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def write_normalized_artifacts(*, artifact_dir: Path, run_config: dict[str, Any], events: list[dict[str, Any]], metrics: dict[str, Any]) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    summary = build_summary(run_config=run_config, events=events, metrics=metrics)
    _write_json(artifact_dir / "summary.json", summary)
    _write_turns(artifact_dir / "turns.jsonl", events)
    _write_metrics(artifact_dir / "metrics.csv", summary)


def build_summary(*, run_config: dict[str, Any], events: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
    policy_events = [event for event in events if event.get("event") == "policy_step"]
    action_counts: dict[str, int] = {}
    override_count = 0
    for event in policy_events:
        action_type = _action_type_from_event(event)
        action_counts[action_type] = action_counts.get(action_type, 0) + 1
        if event.get("decision", {}).get("policy_override"):
            override_count += 1
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
        "physical_actions": _physical_action_count(summary["action_counts"]),
        "communication_actions": summary["action_counts"].get("send_message", 0),
    }
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)


def _physical_action_count(action_counts: dict[str, int]) -> int:
    return sum(count for action_type, count in action_counts.items() if action_type not in {"send_message", "wait", "unknown"})


def _json_default(value: Any) -> Any:
    if hasattr(value, "__dict__"):
        return value.__dict__
    if isinstance(value, (frozenset, set, tuple)):
        return list(value)
    return str(value)
