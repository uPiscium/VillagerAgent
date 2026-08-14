"""Versioned benchmark event validation and deterministic normalization."""
from __future__ import annotations
from copy import deepcopy
from typing import Any, Mapping
from benchmarks.common.eac.canonical import canonical_bytes
from .artifacts import validate_publication
from .model import InjectionPhase, Visibility
from .protocol import EVENT_TYPES


EVENT_VERSION = 1
REQUIRED_EVENT_FIELDS = frozenset({
    "schema_version", "event_id", "run_id", "scenario_id", "event_type",
    "phase", "monotonic_index", "visibility", "payload", "emission_status",
})


def normalize_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an event and return a sorted, detached JSON-compatible copy."""
    if not isinstance(event, Mapping):
        raise TypeError("benchmark event must be an object")
    missing = REQUIRED_EVENT_FIELDS - set(event)
    if missing:
        raise ValueError(f"benchmark event missing fields: {sorted(missing)}")
    if (event.get("schema_version") != EVENT_VERSION
            or not isinstance(event.get("event_id"), str)
            or not event["event_id"]):
        raise ValueError("unsupported or unidentified benchmark event")
    if event.get("event_type") not in EVENT_TYPES:
        raise ValueError("invalid benchmark event type")
    for field in ("run_id", "scenario_id"):
        if not isinstance(event.get(field), str) or not event[field]:
            raise ValueError(f"{field} must be a non-empty string")
    if type(event.get("monotonic_index")) is not int or event["monotonic_index"] < 0:
        raise ValueError("monotonic_index must be a non-negative integer")
    if not isinstance(event.get("payload"), Mapping):
        raise ValueError("event payload must be an object")
    if event.get("emission_status") not in {"RECORDED", "SANITIZED"}:
        raise ValueError("invalid emission status")
    result = deepcopy(dict(event))
    if "phase" in result:
        try: InjectionPhase(result["phase"])
        except (TypeError, ValueError) as exc: raise ValueError("invalid injection phase") from exc
    if "visibility" in result:
        try: Visibility(result["visibility"])
        except (TypeError, ValueError) as exc: raise ValueError("invalid event visibility") from exc
    canonical_bytes(result)
    if result["visibility"] in {Visibility.ACTOR_VISIBLE.value,
                                Visibility.PUBLIC_SANITIZED.value} or result["emission_status"] == "SANITIZED":
        validate_publication(result["payload"])
    return {key: result[key] for key in sorted(result)}


def validate_event_stream(events: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    normalized = tuple(normalize_event(event) for event in events)
    indexes = tuple(event["monotonic_index"] for event in normalized)
    if indexes != tuple(range(len(normalized))):
        raise ValueError("event monotonic indexes must be contiguous from zero")
    if len({event["event_id"] for event in normalized}) != len(normalized):
        raise ValueError("event identifiers must be unique")
    if normalized and (len({event["run_id"] for event in normalized}) != 1 or
                       len({event["scenario_id"] for event in normalized}) != 1):
        raise ValueError("an event stream must belong to one run and scenario")
    return normalized
