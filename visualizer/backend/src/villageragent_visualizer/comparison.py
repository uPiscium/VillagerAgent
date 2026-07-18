from __future__ import annotations

from datetime import datetime
from pathlib import Path

from villageragent_visualizer.artifacts import ArtifactRepository
from villageragent_visualizer.runs import RunRepository


class ComparisonService:
    def __init__(self, *, artifacts: ArtifactRepository, runs: RunRepository) -> None:
        self.artifacts = artifacts
        self.runs = runs

    def compare(self, run_ids: list[str]) -> dict:
        rows = [row for run_id in dict.fromkeys(run_ids) if (row := self._row(run_id)) is not None]
        warnings = []
        task_keys = {(row["task_name"], row["task_type"]) for row in rows}
        if len(task_keys) > 1:
            warnings.append({"code": "different_tasks", "message": "Selected runs use different tasks; values are descriptive and not a controlled comparison."})
        if len(rows) < 2:
            warnings.append({"code": "insufficient_runs", "message": "Select at least two available runs for comparison."})
        return {"runs": rows, "warnings": warnings, "semantics": {"missing_values": "null", "inference": "descriptive_only"}}

    def _row(self, run_id: str) -> dict | None:
        manifest = self.runs.get_run(run_id)
        if manifest is None:
            return None
        summary = self._json(run_id, "summary.json")
        metrics = self._json(run_id, "metrics.json")
        action_log = self._json(run_id, "action_log.json")
        events = self._events(run_id) if manifest.artifacts.get("events") else []
        return {
            "run_id": run_id,
            "name": manifest.name,
            "task_name": manifest.task.name or None,
            "task_type": manifest.task.task_type or None,
            "state": manifest.state.value,
            "mode": manifest.mode or None,
            "policy": manifest.policy or None,
            "task_state_source": manifest.source.task_state or None,
            "snapshot_source": manifest.source.snapshot or None,
            "progress": manifest.progress,
            "score": summary.get("final_score"),
            "task_count": metrics.get("task_count"),
            "completed_task_count": metrics.get("completed_task_count"),
            "failed_task_count": metrics.get("failed_task_count"),
            "action_count": metrics.get("action_count"),
            "failed_action_count": metrics.get("failed_action_count"),
            "duration_seconds": metrics.get("time_to_completion"),
            "recommendation_adopted_count": metrics.get("recommendation_adopted_count"),
            "runtime_selected_task_ids": summary.get("runtime_selected_task_ids"),
            "posthoc_ranked_task_order": summary.get("posthoc_ranked_task_order"),
            "agent_action_counts": _agent_action_counts(action_log),
            "agent_idle_seconds": _agent_idle_seconds(events) if events else None,
            "error": manifest.error,
        }

    def _json(self, run_id: str, filename: str) -> dict:
        result = self.artifacts.load_json(Path(run_id) / filename)
        if result.artifact is None or not isinstance(result.artifact.data, dict):
            return {}
        return result.artifact.data

    def _events(self, run_id: str) -> list[dict]:
        resolved = self.artifacts.resolve_path(Path(run_id) / "events.jsonl")
        if not isinstance(resolved, Path):
            return []
        try:
            lines = resolved.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return []
        import json
        events = []
        for line in lines:
            try:
                value = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(value, dict):
                events.append(value)
        return events


def _agent_action_counts(action_log: dict) -> dict[str, int] | None:
    if not action_log:
        return None
    return {str(agent): len([item for item in records if isinstance(item, dict)]) for agent, records in action_log.items() if isinstance(records, list)}


def _agent_idle_seconds(events: list[dict]) -> dict[str, float] | None:
    by_agent: dict[str, list[tuple[datetime, float]]] = {}
    for event in events:
        if event.get("event_type") != "action_recorded" or not isinstance(event.get("occurred_at"), str):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        agent = payload.get("agent")
        if not isinstance(agent, str):
            continue
        try:
            start = datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
        except ValueError:
            continue
        duration = payload.get("duration")
        by_agent.setdefault(agent, []).append((start, float(duration) if isinstance(duration, int | float) and duration >= 0 else 0.0))
    if not by_agent:
        return None
    idle = {}
    for agent, actions in by_agent.items():
        actions.sort(key=lambda item: item[0])
        idle[agent] = sum(max(0.0, (current[0] - previous[0]).total_seconds() - previous[1]) for previous, current in zip(actions, actions[1:]))
    return idle
