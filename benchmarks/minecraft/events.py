from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks.common.sanitization import sanitize_artifact_value
from pipeline.runtime_events import RUNTIME_EVENT_SCHEMA_VERSION, read_runtime_events


@dataclass(frozen=True, slots=True)
class NormalizedEvents:
    events: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...] = ()


def build_normalized_events(
    *,
    run_id: str,
    runtime_journal: str | Path | None,
    action_log: dict,
    analysis_artifact: dict,
) -> NormalizedEvents:
    events: list[dict[str, Any]] = []
    warnings: list[str] = []
    if runtime_journal is not None:
        runtime = read_runtime_events(runtime_journal)
        warnings.extend(runtime.warnings)
        for event in runtime.events:
            occurred_at = _normalized_timestamp(event.get("occurred_at"))
            if event.get("occurred_at") is not None and occurred_at is None:
                warnings.append(f"invalid runtime event timestamp at seq {event.get('seq')} ignored")
            events.append({
                "schema_version": RUNTIME_EVENT_SCHEMA_VERSION,
                "run_id": run_id,
                "event_type": event.get("event_type", "unknown"),
                "occurred_at": occurred_at,
                "emitted_at": event.get("emitted_at"),
                "entity_id": event.get("entity_id"),
                "source": event.get("source", "runtime_event_journal"),
                "payload": event.get("payload", {}),
                "provenance": {"runtime_event_id": event.get("event_id"), "runtime_seq": event.get("seq")},
            })

    for agent, records in action_log.items():
        if not isinstance(records, list):
            warnings.append(f"action log for {agent} is not an array")
            continue
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                warnings.append(f"malformed action record {agent}:{index} ignored")
                continue
            entity_id = f"minecraft:action:{agent}:{index}"
            occurred_at = _normalized_timestamp(record.get("start_time"))
            if record.get("start_time") is not None and occurred_at is None:
                warnings.append(f"invalid action timestamp {agent}:{index} ignored")
            events.append({
                "schema_version": RUNTIME_EVENT_SCHEMA_VERSION,
                "run_id": run_id,
                "event_type": "action_recorded",
                "occurred_at": occurred_at,
                "emitted_at": None,
                "entity_id": entity_id,
                "source": "action_log",
                "payload": {
                    "agent": agent,
                    "record_index": index,
                    "tool": record.get("action", record.get("tool", "unknown")),
                    "arguments": record.get("kwargs", {}),
                    "duration": record.get("duration"),
                    "result": record.get("result"),
                    "record_semantics": "recorded action; not an observed start/completion hook",
                },
                "provenance": {"action_record_index": index},
            })

    edges = analysis_artifact.get("edges", [])
    incoming = {}
    if isinstance(edges, list):
        for edge in edges:
            if isinstance(edge, dict):
                incoming.setdefault(edge.get("target_id"), []).append(edge.get("source_id"))
    seen_projection_entities: set[tuple[str, str]] = set()
    nodes = analysis_artifact.get("nodes", [])
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_type = node.get("node_type")
            event_type = "observation_recorded" if node_type == "minecraft_observation" else "claim_recorded" if node_type == "minecraft_claim" else None
            entity_id = node.get("node_id")
            if event_type is None or not isinstance(entity_id, str) or (event_type, entity_id) in seen_projection_entities:
                continue
            seen_projection_entities.add((event_type, entity_id))
            events.append({
                "schema_version": RUNTIME_EVENT_SCHEMA_VERSION,
                "run_id": run_id,
                "event_type": event_type,
                "occurred_at": None,
                "emitted_at": None,
                "entity_id": entity_id,
                "source": "dual_dag_artifact",
                "payload": node.get("content", {}),
                "provenance": {"source_entity_ids": [value for value in incoming.get(entity_id, []) if isinstance(value, str)]},
            })

    indexed = list(enumerate(sanitize_artifact_value(events)))
    indexed.sort(key=lambda item: _event_sort_key(item[1], item[0]))
    normalized = []
    for seq, (_, event) in enumerate(indexed, start=1):
        normalized.append({**event, "seq": seq, "event_id": f"{run_id}:normalized:{seq}"})
    return NormalizedEvents(events=tuple(normalized), warnings=tuple(warnings))


def _event_sort_key(event: dict[str, Any], stable_index: int) -> tuple[int, float, int]:
    occurred_at = event.get("occurred_at")
    if isinstance(occurred_at, str):
        try:
            parsed = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            ordering_value = parsed.toordinal() * 86_400 + parsed.hour * 3_600 + parsed.minute * 60 + parsed.second + parsed.microsecond / 1_000_000
            return (0, ordering_value, stable_index)
        except ValueError:
            pass
    return (1, 0.0, stable_index)


def _normalized_timestamp(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value
