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
import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from itertools import count
from pathlib import Path
from typing import Any, Mapping

from benchmarks.common.eac.canonical import canonical_bytes, thaw_json
from benchmarks.common.eac.model import ExactRequest, Proposition


TRACE_SCHEMA_VERSION = "minecraft-k11-trace/2"
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
    "k11.observation_window_opened",
    "k11.observation_window_closed",
})

WINDOW_REASONS = frozenset({"fixed_observation_horizon", "natural_runtime_terminal"})


def observation_window(artifact: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """Return the strict observation window, or None for legacy fixtures."""
    events = artifact.get("events", [])
    opened = [e for e in events if isinstance(e, Mapping) and e.get("event_type") == "k11.observation_window_opened"]
    closed = [e for e in events if isinstance(e, Mapping) and e.get("event_type") == "k11.observation_window_closed"]
    if not opened and not closed:
        return None
    if len(opened) != 1 or len(closed) != 1:
        return (opened[0] if opened else {}, closed[0] if closed else {})
    return opened[0], closed[0]


def observation_window_bounds(artifact: Mapping[str, Any]) -> tuple[int, int, str] | None:
    pair = observation_window(artifact)
    if pair is None:
        return None
    opened, closed = pair
    closed_payload = closed.get("payload", {})
    if not isinstance(closed_payload, Mapping):
        return None
    reason = closed_payload.get("reason")
    start = opened.get("monotonic_ns")
    end = closed_payload.get("window_close_monotonic_ns")
    if isinstance(start, int) and isinstance(end, int) and isinstance(reason, str):
        return start, end, reason
    return None


def event_in_observation_window(
    event: Mapping[str, Any], bounds: tuple[int, int, str] | None,
) -> bool:
    if bounds is None:
        return True
    value = event.get("monotonic_ns")
    return isinstance(value, int) and bounds[0] <= value < bounds[1]


def _candidate_id(event: Mapping[str, Any]) -> str | None:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    request = payload.get("exact_request")
    if not isinstance(request, Mapping):
        return None
    candidate_id = request.get("candidate_id")
    return candidate_id if isinstance(candidate_id, str) else None


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


def _valid_preparation(event: Mapping[str, Any], *, require_scope: bool) -> bool:
    payload = event.get("payload")
    request = payload.get("exact_request") if isinstance(payload, Mapping) else None
    action = request.get("action") if isinstance(request, Mapping) else None
    try:
        digest_valid = (
            isinstance(request, Mapping)
            and payload.get("exact_request_digest") == exact_request_digest(request)
        )
    except (TypeError, ValueError):
        digest_valid = False
    if (not isinstance(request, Mapping)
            or not isinstance(request.get("candidate_id"), str) or not request["candidate_id"]
            or not isinstance(request.get("attempt_id"), str) or not request["attempt_id"]
            or not isinstance(action, Mapping)
            or not isinstance(action.get("identity"), str) or not action["identity"]
            or type(action.get("version")) is not int or action["version"] < 1
            or not isinstance(action.get("digest"), str) or not action["digest"]
            or not digest_valid):
        return False
    if require_scope and any(not event.get(field) for field in (
            "actor_id", "task_id", "agent_step_id", "tool_call_id")):
        return False
    return True


def derive_positive_disposition(
    artifact: Mapping[str, Any], prepared: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Derive a positive pre-decision disposition from ordinary lifecycle facts.

    Mere disappearance is never sufficient. The prepared tool call must return
    normally without a decision, followed by either a same-actor/task successor
    preparation before the agent step returns, or the normal return of that step.
    """
    events = artifact.get("events")
    if not isinstance(events, list) or not isinstance(prepared, Mapping):
        return None
    prepared_payload = prepared.get("payload")
    if not isinstance(prepared_payload, Mapping) or not _valid_preparation(prepared, require_scope=True):
        return None
    prepared_seq = prepared.get("seq")
    prepared_ns = prepared.get("monotonic_ns")
    scope = tuple(prepared.get(field) for field in (
        "actor_id", "task_id", "agent_step_id", "tool_call_id",
    ))
    if (any(not value for value in scope) or not isinstance(prepared_seq, int)
            or not isinstance(prepared_ns, int)):
        return None
    preparation_counts: dict[str, int] = {}
    for event in events:
        if not isinstance(event, Mapping):
            continue
        if event.get("event_type") != "k11.eac_action_prepared":
            continue
        payload = event.get("payload")
        request = payload.get("exact_request") if isinstance(payload, Mapping) else None
        candidate_id = request.get("candidate_id") if isinstance(request, Mapping) else None
        if isinstance(candidate_id, str):
            preparation_counts[candidate_id] = preparation_counts.get(candidate_id, 0) + 1
    prepared_request = prepared_payload.get("exact_request")
    original_candidate = prepared_request.get("candidate_id") if isinstance(prepared_request, Mapping) else None
    if not isinstance(original_candidate, str) or preparation_counts.get(original_candidate) != 1:
        return None

    tool_exits = [
        event for event in events
        if isinstance(event, Mapping)
        and event.get("event_type") == "k11.tool_call_exited"
        and tuple(event.get(field) for field in (
            "actor_id", "task_id", "agent_step_id", "tool_call_id",
        )) == scope
        and isinstance(event.get("payload"), Mapping)
        and event["payload"].get("outcome") == "returned"
        and isinstance(event.get("seq"), int) and event["seq"] > prepared_seq
        and isinstance(event.get("monotonic_ns"), int) and event["monotonic_ns"] > prepared_ns
    ]
    if len(tool_exits) != 1:
        return None
    tool_exit = tool_exits[0]

    agent_returns = [
        event for event in events
        if isinstance(event, Mapping)
        and event.get("event_type") == "k11.agent_step_completed"
        and (event.get("actor_id"), event.get("task_id"), event.get("agent_step_id")) == scope[:3]
        and isinstance(event.get("payload"), Mapping)
        and event["payload"].get("outcome") == "returned"
        and isinstance(event.get("seq"), int) and event["seq"] > tool_exit["seq"]
        and isinstance(event.get("monotonic_ns"), int)
        and event["monotonic_ns"] > tool_exit["monotonic_ns"]
    ]
    if len(agent_returns) != 1:
        return None
    agent_return = agent_returns[0]

    successor_preparations = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        payload = event.get("payload")
        request = payload.get("exact_request") if isinstance(payload, Mapping) else None
        successor_scope = tuple(event.get(field) for field in (
            "actor_id", "task_id", "agent_step_id", "tool_call_id",
        ))
        if (event.get("event_type") == "k11.eac_action_prepared"
                and isinstance(request, Mapping)
                and _valid_preparation(event, require_scope=True)
                and request.get("candidate_id") != original_candidate
                and preparation_counts.get(request.get("candidate_id")) == 1
                and successor_scope[:3] == scope[:3]
                and successor_scope[3] and successor_scope[3] != scope[3]
                and isinstance(event.get("seq"), int) and event["seq"] > tool_exit["seq"]
                and event["seq"] < agent_return["seq"]
                and isinstance(event.get("monotonic_ns"), int)
                and tool_exit["monotonic_ns"] < event["monotonic_ns"] < agent_return["monotonic_ns"]):
            successor_preparations.append(event)

    kind = "cancellation"
    marker = agent_return
    if successor_preparations:
        by_sequence = min(successor_preparations, key=lambda event: event["seq"])
        by_time = min(successor_preparations, key=lambda event: event["monotonic_ns"])
        if by_sequence is not by_time:
            return None
        kind = "replacement"
        marker = by_sequence
    successor_ids = []
    if kind == "replacement":
        successor_ids = [marker["payload"]["exact_request"]["candidate_id"]]
    return {
        "kind": kind,
        "marker": marker,
        "tool_exit": tool_exit,
        "successor_candidate_ids": successor_ids,
    }


def _event_precedes(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool | None:
    first_seq, second_seq = first.get("seq"), second.get("seq")
    first_ns, second_ns = first.get("monotonic_ns"), second.get("monotonic_ns")
    if not all(isinstance(value, int) for value in (first_seq, second_seq, first_ns, second_ns)):
        return None
    sequence_before = first_seq < second_seq
    time_before = first_ns < second_ns
    if sequence_before != time_before or first_seq == second_seq or first_ns == second_ns:
        return None
    return sequence_before


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
        monotonic_ns: int | None = None,
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
                "monotonic_ns": time.monotonic_ns() if monotonic_ns is None else monotonic_ns,
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

    pair = observation_window(artifact)
    bounds = observation_window_bounds(artifact)
    if pair is not None:
        opened, closed = pair
        opened_rows = [event for event in events if isinstance(event, Mapping)
                       and event.get("event_type") == "k11.observation_window_opened"]
        closed_rows = [event for event in events if isinstance(event, Mapping)
                       and event.get("event_type") == "k11.observation_window_closed"]
        if len(opened_rows) != 1 or len(closed_rows) != 1:
            errors.append("observation window requires exactly one open and close event")
        if bounds is None:
            errors.append("observation window timestamps or reason are malformed")
        else:
            start, end, reason = bounds
            opened_payload = opened.get("payload", {})
            closed_payload = closed.get("payload", {})
            if not isinstance(opened_payload, Mapping):
                opened_payload = {}
            if not isinstance(closed_payload, Mapping):
                closed_payload = {}
            if start >= end:
                errors.append("observation window is misordered")
            if (not isinstance(opened.get("seq"), int) or not isinstance(closed.get("seq"), int)
                    or opened["seq"] >= closed["seq"]):
                errors.append("observation window sequence is misordered")
            if reason not in WINDOW_REASONS:
                errors.append("observation window reason is invalid")
            configured = opened_payload.get("configured_horizon_seconds")
            horizon = opened_payload.get("horizon_monotonic_ns")
            if (type(configured) not in (int, float) or isinstance(configured, bool)
                    or not math.isfinite(configured) or configured <= 0
                    or type(horizon) is not int
                    or horizon != start + round(configured * 1_000_000_000)
                    or closed_payload.get("configured_horizon_seconds") != configured
                    or closed.get("monotonic_ns") != end):
                errors.append("observation window horizon metadata is invalid")
            if reason == "fixed_observation_horizon" and (
                    type(horizon) is not int or end != horizon
                    or closed_payload.get("shutdown_requested") is not True):
                errors.append("fixed observation horizon close is invalid")
            if reason == "natural_runtime_terminal" and (
                    type(horizon) is not int or end >= horizon
                    or closed_payload.get("shutdown_requested") is not False):
                errors.append("natural observation close is invalid")

    sequences = [event.get("seq") if isinstance(event, Mapping) else None for event in events]
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
        if not isinstance(event, Mapping):
            errors.append("trace event is malformed")
            continue
        event_type = event.get("event_type")
        if event_type not in K11_EVENT_TYPES:
            warnings.append(f"unknown event type: {event_type}")
            continue
        if (pair is not None and event_type.startswith("k11.eac_")
                and not event_in_observation_window(event, bounds)):
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            errors.append(f"trace {event_type} payload is malformed")
            continue
        if event_type == "k11.eac_evidence_ingested":
            evidence_count += 1
        if event_type == "k11.eac_action_prepared" and not _valid_preparation(
                event, require_scope=False):
            errors.append("trace preparation is malformed")
            continue
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

    scoped_artifact = artifact
    if bounds is not None:
        scoped_artifact = dict(artifact)
        scoped_artifact["events"] = [
            item for item in events if event_in_observation_window(item, bounds)
        ]

    def all_candidate_rows(kind: str, candidate_id: str) -> list[Mapping[str, Any]]:
        return [
            item for item in events
            if isinstance(item, Mapping) and item.get("event_type") == kind
            and _candidate_id(item) == candidate_id
        ]

    for candidate_id, rows in prepared.items():
        if len(rows) != 1:
            errors.append(f"candidate {candidate_id} has {len(rows)} prepare events")
        decision_rows = decisions.get(candidate_id, [])
        terminal_rows = all_candidate_rows("k11.eac_action_terminal", candidate_id)
        # Positive abandonment is authoritative only inside a declared window.
        positive_disposition = derive_positive_disposition(scoped_artifact, rows[0])
        decision_precedes = None
        if len(decision_rows) == 1 and positive_disposition is not None:
            decision_precedes = _event_precedes(decision_rows[0], positive_disposition["marker"])
            if decision_precedes is False:
                errors.append(f"candidate {candidate_id} reaches a decision after positive abandonment")
            elif decision_precedes is None:
                errors.append(f"candidate {candidate_id} disposition ordering is ambiguous")
        selected_positive = positive_disposition if not decision_rows else None
        censored = (bounds is not None and bounds[2] == "fixed_observation_horizon"
                    and event_in_observation_window(rows[0], bounds)
                    and (not decision_rows or any(
                        not event_in_observation_window(item, bounds) for item in decision_rows)))
        if len(decision_rows) != 1 and selected_positive is None and not censored:
            errors.append(f"candidate {candidate_id} lacks exactly one disposition event")
        if decision_rows and len(terminal_rows) != 1:
            errors.append(f"candidate {candidate_id} has {len(terminal_rows)} terminal events")
        if selected_positive is not None and terminal_rows:
            errors.append(f"candidate {candidate_id} has a terminal after positive abandonment")
        disposition_rows = decision_rows or (
            [selected_positive["marker"]] if selected_positive is not None else []
        )
        if disposition_rows:
            prepare_ns = rows[0].get("monotonic_ns")
            disposition_ns = disposition_rows[0].get("monotonic_ns")
            if (not isinstance(prepare_ns, int) or not isinstance(disposition_ns, int)
                    or disposition_ns <= prepare_ns):
                warnings.append(f"candidate {candidate_id} has non-positive prepare-to-disposition interval")
            prepare_digest = rows[0].get("payload", {}).get("exact_request_digest")
            disposition_digest = disposition_rows[0].get("payload", {}).get("exact_request_digest")
            if decision_rows and prepare_digest != disposition_digest:
                errors.append(f"candidate {candidate_id} exact request changed before disposition")

    for candidate_id, rows in native_entries.items():
        if candidate_id not in decisions:
            errors.append(f"candidate {candidate_id} reached native effect without decision marker")
        completions = all_candidate_rows("k11.eac_native_effect_completed", candidate_id)
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
            "positive_abandonments": sum(
                derive_positive_disposition(scoped_artifact, rows[0]) is not None
                for candidate_id, rows in prepared.items() if candidate_id not in decisions
            ),
            "execution_decisions": sum(len(rows) for rows in decisions.values()),
            "native_entries": sum(len(rows) for rows in native_entries.values()),
            "native_completions": sum(len(rows) for rows in native_completions.values()),
            "terminals": sum(len(rows) for rows in terminals.values()),
            "evidence_ingestions": evidence_count,
        },
    }


def validate_p0_trace(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the strict, per-run completeness gate required by K11 P0.

    ``validate_trace`` remains deliberately useful for small unit fixtures.  This
    validator is the admission gate for a pilot run: aggregate event presence is
    not sufficient, and every primary request must remain exactly correlated.
    """
    generic = validate_trace(artifact)
    errors = list(generic["errors"])
    events = artifact.get("events") if isinstance(artifact, Mapping) else None
    if not isinstance(events, list) or not events:
        return {"valid": False, "errors": errors + ["P0 trace is empty or malformed"], "warnings": generic["warnings"], "counts": generic.get("counts", {})}
    if any("malformed" in error for error in errors):
        return {
            "valid": False,
            "errors": errors,
            "warnings": generic["warnings"],
            "counts": generic.get("counts", {}),
        }
    bounds = observation_window_bounds(artifact)
    if bounds is None:
        errors.append("P0 trace requires one valid observation window")

    def rows(kind: str, *, within_window: bool = True) -> list[Mapping[str, Any]]:
        return [event for event in events if isinstance(event, Mapping) and event.get("event_type") == kind
                and (not within_window or kind in {
                    "k11.observation_window_opened", "k11.observation_window_closed"
                } or event_in_observation_window(event, bounds))]

    starts = rows("k11.agent_step_started", within_window=False)
    completed = rows("k11.agent_step_completed", within_window=False)
    def lifecycle_key(event: Mapping[str, Any], identity: Any) -> tuple[Any, ...] | None:
        if not identity:
            return None
        return (identity, event.get("agent_step_id"), event.get("actor_id"), event.get("task_id"))

    def require_lifecycles(start_rows: list[Mapping[str, Any]], terminal_rows: list[Mapping[str, Any]],
                           identity, label: str) -> None:
        starts_by_key: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
        terminals_by_key: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
        def collect(source_rows, table) -> None:
            for row in source_rows:
                key = lifecycle_key(row, identity(row))
                if key is None:
                    errors.append(f"P0 {label} lifecycle lacks identity")
                    continue
                table.setdefault(key, []).append(row)

        collect(start_rows, starts_by_key)
        collect(terminal_rows, terminals_by_key)
        if not starts_by_key:
            errors.append(f"P0 trace lacks a correlated {label} lifecycle")
            return
        if set(starts_by_key) != set(terminals_by_key):
            errors.append(f"P0 {label} lifecycle identities are incomplete")
        for key in set(starts_by_key) | set(terminals_by_key):
            start_matches = starts_by_key.get(key, [])
            terminal_matches = terminals_by_key.get(key, [])
            if len(start_matches) != 1 or len(terminal_matches) != 1:
                errors.append(f"P0 {label} lifecycle {key[0]} is not one-to-one")
            elif start_matches[0].get("seq", 0) >= terminal_matches[0].get("seq", 0):
                errors.append(f"P0 {label} lifecycle {key[0]} is misordered")

    require_lifecycles(starts, completed, lambda event: event.get("agent_step_id"), "agent")

    model_starts = rows("k11.model_call_started", within_window=False)
    model_terminals = (
        rows("k11.model_call_completed", within_window=False)
        + rows("k11.model_call_failed", within_window=False)
    )
    require_lifecycles(
        model_starts, model_terminals,
        lambda event: event.get("payload", {}).get("model_call_id"), "model",
    )

    tool_enters = rows("k11.tool_call_entered", within_window=False)
    tool_exits = rows("k11.tool_call_exited", within_window=False)
    require_lifecycles(tool_enters, tool_exits, lambda event: event.get("tool_call_id"), "tool")
    if not rows("k11.eac_evidence_ingested"):
        errors.append("P0 trace lacks evidence ingestion")

    prepared = [event for event in rows("k11.eac_action_prepared")
                if event.get("payload", {}).get("exact_request", {}).get("action", {}).get("identity") in PRIMARY_EFFECT_ACTIONS]
    if not prepared:
        errors.append("P0 trace lacks a primary preparation")

    decisions = rows("k11.eac_execution_decision_attempted")
    native_entries = rows("k11.eac_native_effect_entered")
    all_prepared_ids = {
        row.get("payload", {}).get("exact_request", {}).get("candidate_id")
        for row in rows("k11.eac_action_prepared")
    }
    def belongs_to_measured_preparation(event: Mapping[str, Any]) -> bool:
        return _candidate_id(event) in all_prepared_ids

    terminals = [
        event for event in rows("k11.eac_action_terminal", within_window=False)
        if belongs_to_measured_preparation(event)
    ]
    native_entry_candidate_ids = {
        _candidate_id(event)
        for event in native_entries
    }
    native_completions = [
        event for event in rows("k11.eac_native_effect_completed", within_window=False)
        if _candidate_id(event) in native_entry_candidate_ids
    ]
    for row in decisions + terminals + native_entries + native_completions:
        row_request = row.get("payload", {}).get("exact_request")
        row_candidate = row_request.get("candidate_id") if isinstance(row_request, Mapping) else None
        if not isinstance(row_candidate, str) or not row_candidate:
            errors.append(f"P0 {row.get('event_type')} event lacks candidate identity")
        elif row_candidate not in all_prepared_ids:
            errors.append(f"P0 {row.get('event_type')} event has no preparation")
        row_digest = row.get("payload", {}).get("exact_request_digest")
        if isinstance(row_request, Mapping) and row_digest != exact_request_digest(row_request):
            errors.append(f"P0 {row.get('event_type')} event has an invalid exact request digest")
    for event in prepared:
        request = event.get("payload", {}).get("exact_request", {})
        candidate = request.get("candidate_id") if isinstance(request, Mapping) else None
        attempt = request.get("attempt_id") if isinstance(request, Mapping) else None
        digest = event.get("payload", {}).get("exact_request_digest")
        scope = tuple(event.get(field) for field in (
            "actor_id", "task_id", "agent_step_id", "tool_call_id",
        ))
        if (not isinstance(candidate, str) or not candidate
                or not isinstance(attempt, str) or not attempt
                or not isinstance(digest, str) or not digest
                or any(not value for value in scope)):
            errors.append("P0 primary preparation lacks exact request or scoped identity")
        related_decisions = [row for row in decisions if row.get("payload", {}).get("exact_request", {}).get("candidate_id") == candidate]
        related_terminals = [row for row in terminals if row.get("payload", {}).get("exact_request", {}).get("candidate_id") == candidate]
        scoped_artifact = artifact
        if bounds is not None:
            scoped_artifact = dict(artifact)
            scoped_artifact["events"] = [
                item for item in events if event_in_observation_window(item, bounds)
            ]
        positive_disposition = derive_positive_disposition(scoped_artifact, event)
        decision_precedes = None
        if len(related_decisions) == 1 and positive_disposition is not None:
            decision_precedes = _event_precedes(related_decisions[0], positive_disposition["marker"])
            if decision_precedes is False:
                errors.append(f"primary candidate {candidate} reaches a decision after positive abandonment")
            elif decision_precedes is None:
                errors.append(f"primary candidate {candidate} disposition ordering is ambiguous")
        selected_positive = positive_disposition if not related_decisions else None
        censored = (bounds is not None and bounds[2] == "fixed_observation_horizon"
                    and event_in_observation_window(event, bounds)
                    and not related_decisions)
        if len(related_decisions) != 1 and selected_positive is None and not censored:
            errors.append(f"primary candidate {candidate} lacks exactly one disposition")
        if related_decisions and len(related_terminals) != 1:
            errors.append(f"primary candidate {candidate} lacks exactly one terminal")
        if selected_positive is not None and related_terminals:
            errors.append(f"primary candidate {candidate} has a terminal after abandonment")
        related_dispositions = related_decisions or (
            [selected_positive["marker"]] if selected_positive is not None else []
        )
        if related_dispositions:
            prepare_ns = event.get("monotonic_ns")
            disposition_ns = related_dispositions[0].get("monotonic_ns")
            if (not isinstance(prepare_ns, int) or not isinstance(disposition_ns, int)
                    or disposition_ns <= prepare_ns):
                errors.append(f"primary candidate {candidate} lacks a positive prepare-to-disposition interval")
        related_entries = [row for row in native_entries
                           if row.get("payload", {}).get("exact_request", {}).get("candidate_id") == candidate]
        related_completions = [row for row in native_completions
                               if row.get("payload", {}).get("exact_request", {}).get("candidate_id") == candidate]
        if len(related_entries) not in {0, 1} or len(related_completions) != len(related_entries):
            errors.append(f"primary candidate {candidate} native lifecycle is incomplete or duplicated")
        correlated = related_decisions + related_terminals + related_entries + related_completions
        for row in correlated:
            row_payload = row.get("payload", {})
            row_request = row_payload.get("exact_request")
            row_scope = tuple(row.get(field) for field in (
                "actor_id", "task_id", "agent_step_id", "tool_call_id",
            ))
            if (row_payload.get("exact_request_digest") != digest
                    or row_request != request or row_scope != scope):
                errors.append(f"primary candidate {candidate} exact request digest is not correlated")
        if isinstance(request, Mapping) and digest != exact_request_digest(request):
            errors.append(f"primary candidate {candidate} preparation has an invalid exact request digest")
        ordered = [event] + related_dispositions + related_entries + related_completions + related_terminals
        ordered_sequences = [row.get("seq") for row in ordered]
        if (not censored and (any(not isinstance(value, int) for value in ordered_sequences)
                or ordered_sequences != sorted(ordered_sequences)
                or len(set(ordered_sequences)) != len(ordered_sequences))):
            errors.append(f"primary candidate {candidate} EAC lifecycle is misordered")

    if artifact.get("instrumentation_errors"):
        # Keep this explicit here even though the generic validator also reports it.
        if "instrumentation recorder reported internal errors" not in errors:
            errors.append("instrumentation recorder reported internal errors")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": generic["warnings"],
        "counts": generic.get("counts", {}),
    }
