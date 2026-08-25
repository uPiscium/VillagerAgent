"""Core trace data structures for the K11 natural-exposure study.

Runtime instrumentation lives in ``benchmarks.minecraft.k11_instrumentation``.
This module intentionally contains no filesystem writes on ``record()`` and no
synchronization primitive that could widen the EAC prepare/evidence/execute seam.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from itertools import count
from pathlib import Path
from typing import Any, Mapping

from benchmarks.common.eac.canonical import canonical_bytes, thaw_json
from benchmarks.common.eac.model import ExactRequest, Proposition


TRACE_SCHEMA_VERSION = "minecraft-k11-trace/1"
PRIMARY_EFFECT_ACTIONS = frozenset({
    "MineBlock",
    "placeBlock",
    "navigateTo",
    "attackTarget",
    "handoverBlock",
})
K11_EVENT_TYPES = frozenset({
    "k11.agent_step_started",
    "k11.agent_step_completed",
    "k11.model_call_started",
    "k11.model_call_completed",
    "k11.model_call_failed",
    "k11.tool_call_entered",
    "k11.tool_call_exited",
    "k11.eac_action_prepared",
    "k11.eac_evidence_ingested",
    "k11.eac_execution_decision_attempted",
    "k11.eac_native_effect_entered",
    "k11.eac_native_effect_completed",
    "k11.eac_action_terminal",
})


@dataclass(frozen=True, slots=True)
class K11TraceScope:
    run_id: str
    task_id: str | None = None
    actor_id: str | None = None
    agent_step_id: str | None = None
    tool_call_id: str | None = None


_SCOPE: ContextVar[K11TraceScope | None] = ContextVar("k11_trace_scope", default=None)


def current_scope() -> K11TraceScope | None:
    return _SCOPE.get()


@contextmanager
def use_scope(scope: K11TraceScope):
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


def proposition_payload(proposition: Proposition) -> dict[str, Any]:
    key = proposition.key
    return {
        "namespace": key.namespace,
        "predicate": key.predicate,
        "arguments": plain_value(key.arguments),
        "temporal_scope": key.temporal_scope,
        "polarity": proposition.polarity,
    }


def request_payload(request: ExactRequest) -> dict[str, Any]:
    return {
        "candidate_id": request.candidate_id,
        "attempt_id": request.attempt_id,
        "action": {
            "identity": request.action.identity,
            "version": request.action.version,
            "digest": request.action.digest,
        },
        "arguments": {name: plain_value(value) for name, value in request.arguments},
        "target": plain_value(request.target),
    }


def plain_value(value: Any) -> Any:
    """Convert captured immutable values only when exporting the trace.

    ExactRequest and Proposition have explicit projections because the generic
    dataclass representation does not match the frozen K11 trace schema.
    """
    if isinstance(value, ExactRequest):
        return request_payload(value)
    if isinstance(value, Proposition):
        return proposition_payload(value)
    try:
        from benchmarks.common.eac.canonical import FrozenJSONArray, FrozenJSONObject
        if isinstance(value, (FrozenJSONArray, FrozenJSONObject)):
            return thaw_json(value)
    except ImportError:
        pass
    if is_dataclass(value):
        return {field.name: plain_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): plain_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def exact_request_digest(request_value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(dict(request_value))).hexdigest()


class K11TraceRecorder:
    """Append-only process-local recorder with no explicit lock.

    K11 currently executes on CPython. ``next(itertools.count)`` and
    ``list.append`` are serialized by the interpreter lock; exported events are
    sorted by the explicit sequence. EAC semantic ordering is additionally
    linearized by the pre-existing ``MinecraftEACRuntime`` RLock at hook sites.
    P0 must reject the instrumentation if these assumptions do not hold.
    """

    def __init__(self, run_id: str):
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("K11 trace run_id must be a non-empty string")
        self.run_id = run_id
        self._sequence = count(1)
        self._identity_sequence = count(1)
        self.events: list[dict[str, Any]] = []
        self.instrumentation_errors: list[str] = []

    def new_identity(self, kind: str, *, actor_id: str | None = None) -> str:
        ordinal = next(self._identity_sequence)
        return f"{self.run_id}:{actor_id or 'none'}:{kind}:{ordinal}"

    def record(
        self,
        event_type: str,
        *,
        source: str,
        payload: Mapping[str, Any] | None = None,
        scope: K11TraceScope | None = None,
    ) -> dict[str, Any] | None:
        """Append one small observed fact; tracing failures are non-authoritative."""
        try:
            sequence = next(self._sequence)
            selected_scope = scope if scope is not None else current_scope()
            event = {
                "schema_version": TRACE_SCHEMA_VERSION,
                "run_id": self.run_id,
                "seq": sequence,
                "event_id": f"{self.run_id}:k11:{sequence}",
                "event_type": event_type,
                "source": source,
                "task_id": selected_scope.task_id if selected_scope else None,
                "actor_id": selected_scope.actor_id if selected_scope else None,
                "agent_step_id": selected_scope.agent_step_id if selected_scope else None,
                "tool_call_id": selected_scope.tool_call_id if selected_scope else None,
                "payload": dict(payload or {}),
                "monotonic_ns": time.monotonic_ns(),
                "thread_id": threading.get_ident(),
            }
            self.events.append(event)
            return event
        except BaseException as exc:
            try:
                self.instrumentation_errors.append(type(exc).__name__)
            except BaseException:
                pass
            return None

    def artifact(self) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        for raw in sorted(self.events, key=lambda item: item.get("seq", 0)):
            event = plain_value(raw)
            request = event.get("payload", {}).get("exact_request")
            if isinstance(request, Mapping):
                event["payload"]["exact_request_digest"] = exact_request_digest(request)
            events.append(event)
        return {
            "schema_version": TRACE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "event_count": len(events),
            "instrumentation_errors": list(self.instrumentation_errors),
            "events": events,
        }

    def write_json(self, path: str | Path) -> None:
        """Persist only after the measured runtime section has completed."""
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(self.artifact(), ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def validate_trace(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Validate P0 trace completeness without making prevalence claims."""
    events = artifact.get("events")
    errors: list[str] = []
    warnings: list[str] = []
    if artifact.get("schema_version") != TRACE_SCHEMA_VERSION or not isinstance(events, list):
        return {"valid": False, "errors": ["invalid K11 trace artifact"], "warnings": []}

    sequences = [event.get("seq") for event in events]
    if any(type(value) is not int or value <= 0 for value in sequences):
        errors.append("trace sequence is malformed")
    elif len(set(sequences)) != len(sequences):
        errors.append("trace sequence contains duplicates")
    elif sequences != sorted(sequences):
        errors.append("exported trace sequence is not sorted")

    prepared: dict[str, list[Mapping[str, Any]]] = {}
    decisions: dict[str, list[Mapping[str, Any]]] = {}
    native_entries: dict[str, list[Mapping[str, Any]]] = {}
    native_completions: dict[str, list[Mapping[str, Any]]] = {}
    terminals: dict[str, list[Mapping[str, Any]]] = {}
    evidence_count = 0

    for event in events:
        event_type = event.get("event_type")
        if event_type not in K11_EVENT_TYPES:
            warnings.append(f"unknown event type: {event_type}")
            continue
        payload = event.get("payload", {})
        if event_type == "k11.eac_evidence_ingested":
            evidence_count += 1
        request = payload.get("exact_request") if isinstance(payload, Mapping) else None
        candidate_id = request.get("candidate_id") if isinstance(request, Mapping) else None
        if candidate_id is None:
            continue
        table = None
        if event_type == "k11.eac_action_prepared":
            table = prepared
        elif event_type == "k11.eac_execution_decision_attempted":
            table = decisions
        elif event_type == "k11.eac_native_effect_entered":
            table = native_entries
        elif event_type == "k11.eac_native_effect_completed":
            table = native_completions
        elif event_type == "k11.eac_action_terminal":
            table = terminals
        if table is not None:
            table.setdefault(candidate_id, []).append(event)

    for candidate_id, rows in prepared.items():
        if len(rows) != 1:
            errors.append(f"candidate {candidate_id} has {len(rows)} prepare events")
        decision_rows = decisions.get(candidate_id, [])
        terminal_rows = terminals.get(candidate_id, [])
        if len(decision_rows) != 1:
            errors.append(f"candidate {candidate_id} has {len(decision_rows)} execution-decision events")
        if len(terminal_rows) != 1:
            errors.append(f"candidate {candidate_id} has {len(terminal_rows)} terminal events")
        if decision_rows:
            prepare_ns = rows[0].get("monotonic_ns")
            decision_ns = decision_rows[0].get("monotonic_ns")
            if not isinstance(prepare_ns, int) or not isinstance(decision_ns, int) or decision_ns <= prepare_ns:
                warnings.append(f"candidate {candidate_id} has non-positive prepare-to-decision interval")
            prepare_digest = rows[0].get("payload", {}).get("exact_request_digest")
            decision_digest = decision_rows[0].get("payload", {}).get("exact_request_digest")
            if prepare_digest != decision_digest:
                errors.append(f"candidate {candidate_id} exact request changed before decision")

    for candidate_id, rows in native_entries.items():
        if candidate_id not in decisions:
            errors.append(f"candidate {candidate_id} reached native effect without decision marker")
        completions = native_completions.get(candidate_id, [])
        if len(completions) != len(rows):
            errors.append(f"candidate {candidate_id} native entry/completion count differs")

    if artifact.get("instrumentation_errors"):
        errors.append("instrumentation recorder reported internal errors")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "events": len(events),
            "prepared": sum(len(rows) for rows in prepared.values()),
            "execution_decisions": sum(len(rows) for rows in decisions.values()),
            "native_entries": sum(len(rows) for rows in native_entries.values()),
            "native_completions": sum(len(rows) for rows in native_completions.values()),
            "terminals": sum(len(rows) for rows in terminals.values()),
            "evidence_ingestions": evidence_count,
        },
    }
