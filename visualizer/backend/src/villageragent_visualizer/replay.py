from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from villageragent_visualizer.artifacts import ArtifactRepository
from villageragent_visualizer.dto import ArtifactLoadError, RunWarning
from villageragent_visualizer.runs import RunRepository
from villageragent_visualizer.sanitization import sanitize_public_value


KNOWN_REPLAY_EVENTS = {
    "run_started", "run_completed", "run_failed", "run_timed_out",
    "task_graph_snapshot", "task_candidates_ranked", "task_selected",
    "task_assigned", "task_status_changed", "action_recorded",
    "observation_recorded", "claim_recorded",
}


class ReplayService:
    def __init__(self, *, artifacts: ArtifactRepository, runs: RunRepository) -> None:
        self.artifacts = artifacts
        self.runs = runs

    def events(self, run_id: str, *, start_seq: int = 1, limit: int = 200) -> dict | None:
        loaded = self._load(run_id)
        if loaded is None:
            return None
        events, warnings = loaded
        selected = [event for event in events if isinstance(event.get("seq"), int) and event["seq"] >= start_seq][:limit]
        next_seq = selected[-1]["seq"] + 1 if len(selected) == limit else None
        max_seq = max((event.get("seq", 0) for event in events if isinstance(event.get("seq"), int)), default=0)
        return {"events": selected, "total": len(events), "max_seq": max_seq, "start_seq": start_seq, "next_seq": next_seq, "warnings": [asdict(warning) for warning in warnings]}

    def state(self, run_id: str, seq: int | None = None) -> dict | None:
        loaded = self._load(run_id)
        if loaded is None:
            return None
        events, load_warnings = loaded
        max_seq = max((event.get("seq", 0) for event in events if isinstance(event.get("seq"), int)), default=0)
        target = max_seq if seq is None else max(0, min(seq, max_seq))
        return reconstruct_replay_state(events, target, max_seq=max_seq, warnings=load_warnings)

    def _load(self, run_id: str) -> tuple[list[dict], tuple[RunWarning, ...]] | None:
        if self.runs.get_run(run_id) is None:
            return None
        resolved = self.artifacts.resolve_path(Path(run_id) / "events.jsonl")
        if isinstance(resolved, ArtifactLoadError):
            return None
        try:
            raw = resolved.read_bytes()
        except (OSError, ValueError):
            return None
        if len(raw) > self.artifacts.max_bytes:
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
        events: list[dict] = []
        warnings: list[RunWarning] = []
        lines = text.splitlines()
        incomplete = bool(text) and not text.endswith("\n")
        for index, line in enumerate(lines):
            try:
                value = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                warnings.append(RunWarning(code="incomplete_event" if incomplete and index == len(lines) - 1 else "malformed_event", message=f"Event line {index + 1} was ignored.", artifact="events"))
                continue
            if isinstance(value, dict):
                sanitized = sanitize_public_value(value)
                if isinstance(sanitized, dict):
                    events.append(sanitized)
        events.sort(key=lambda event: event.get("seq", 0) if isinstance(event.get("seq"), int) else 0)
        return events, tuple(warnings)


def reconstruct_replay_state(events: list[dict], seq: int, *, max_seq: int | None = None, warnings: tuple[RunWarning, ...] = ()) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    assignments: dict[str, list[str]] = {}
    timeline: list[dict] = []
    replay_warnings = [asdict(warning) for warning in warnings]
    current_event = None
    previous_seq = 0
    for event in events:
        event_seq = event.get("seq")
        if not isinstance(event_seq, int) or event_seq > seq:
            continue
        if previous_seq and event_seq != previous_seq + 1:
            replay_warnings.append({"code": "sequence_gap", "message": f"Expected seq {previous_seq + 1}, received {event_seq}.", "artifact": "events"})
        previous_seq = event_seq
        current_event = event
        event_type = event.get("event_type")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        entity_id = event.get("entity_id")
        if event_type == "task_graph_snapshot":
            graph = payload.get("graph") if isinstance(payload.get("graph"), dict) else {}
            nodes = {node.get("node_id"): dict(node) for node in graph.get("nodes", []) if isinstance(node, dict) and isinstance(node.get("node_id"), str)}
            edges = [dict(edge) for edge in graph.get("edges", []) if isinstance(edge, dict)]
        elif event_type == "task_status_changed" and isinstance(entity_id, str):
            node = nodes.get(entity_id)
            if node is None:
                replay_warnings.append({"code": "missing_task", "message": f"Status event references missing task {entity_id}.", "artifact": "events"})
            else:
                lifecycle = dict(node.get("lifecycle", {})) if isinstance(node.get("lifecycle"), dict) else {}
                lifecycle["status"] = payload.get("status", "unknown")
                node["lifecycle"] = lifecycle
        elif event_type == "task_assigned" and isinstance(entity_id, str):
            assignments[entity_id] = [agent for agent in payload.get("agents", []) if isinstance(agent, str)] if isinstance(payload.get("agents"), list) else []
        elif event_type == "action_recorded":
            timeline.append(event)
        elif event_type not in KNOWN_REPLAY_EVENTS:
            replay_warnings.append({"code": "unknown_event", "message": f"Unknown event type {event_type} was retained but not reduced.", "artifact": "events"})
    return {
        "seq": seq,
        "max_seq": max_seq if max_seq is not None else max((event.get("seq", 0) for event in events), default=0),
        "graph": {"nodes": list(nodes.values()), "edges": edges},
        "assignments": assignments,
        "timeline": timeline,
        "current_event": current_event,
        "warnings": replay_warnings,
        "authority": "recorded_event_replay",
    }
