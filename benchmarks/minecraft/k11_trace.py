"""Observability-only tracing for the K11 natural-exposure study.

K11 tracing is intentionally isolated from the normal runtime event journal.  In
particular, critical EAC events never use the durable JSONL sink and never add a
new synchronization lock to the prepare/evidence/execute seam.
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
from functools import wraps
from itertools import count
from pathlib import Path
from types import MethodType
from typing import Any, Mapping

from benchmarks.common.eac.canonical import canonical_bytes, thaw_json


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


def _plain(value: Any) -> Any:
    """Convert frozen EAC values to JSON-compatible data after measurement."""
    try:
        from benchmarks.common.eac.canonical import FrozenJSONArray, FrozenJSONObject
        if isinstance(value, (FrozenJSONArray, FrozenJSONObject)):
            return thaw_json(value)
    except ImportError:
        pass
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _proposition_payload(proposition) -> dict[str, Any]:
    key = proposition.key
    return {
        "namespace": key.namespace,
        "predicate": key.predicate,
        "arguments": _plain(key.arguments),
        "temporal_scope": key.temporal_scope,
        "polarity": proposition.polarity,
    }


def _request_payload(request) -> dict[str, Any]:
    return {
        "candidate_id": request.candidate_id,
        "attempt_id": request.attempt_id,
        "action": {
            "identity": request.action.identity,
            "version": request.action.version,
            "digest": request.action.digest,
        },
        "arguments": {name: _plain(value) for name, value in request.arguments},
        "target": _plain(request.target),
    }


def exact_request_digest(request_payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(dict(request_payload))).hexdigest()


class K11TraceRecorder:
    """Append-only in-memory recorder with no explicit synchronization lock.

    CPython serializes ``next(itertools.count)`` and ``list.append`` under the GIL.
    Events retain their explicit sequence and are sorted at export, so list insertion
    order is not treated as the cross-thread logical order.  EAC calls are additionally
    serialized by the pre-existing MinecraftEACRuntime RLock.
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
        actor = actor_id or "none"
        return f"{self.run_id}:{actor}:{kind}:{ordinal}"

    def record(
        self,
        event_type: str,
        *,
        source: str,
        payload: Mapping[str, Any] | None = None,
        scope: K11TraceScope | None = None,
    ) -> dict[str, Any] | None:
        """Record a small observed fact; tracing failures never alter runtime behavior."""
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
                "monotonic_ns": time.monotonic_ns(),
                "thread_id": threading.get_ident(),
                "task_id": selected_scope.task_id if selected_scope else None,
                "actor_id": selected_scope.actor_id if selected_scope else None,
                "agent_step_id": selected_scope.agent_step_id if selected_scope else None,
                "tool_call_id": selected_scope.tool_call_id if selected_scope else None,
                "payload": dict(payload or {}),
            }
            self.events.append(event)
            return event
        except BaseException as exc:  # tracing must not change the measured execution
            try:
                self.instrumentation_errors.append(type(exc).__name__)
            except BaseException:
                pass
            return None

    def artifact(self) -> dict[str, Any]:
        events = []
        for raw in sorted(self.events, key=lambda item: item.get("seq", 0)):
            event = _plain(raw)
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
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(self.artifact(), ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _record_terminal(trace: K11TraceRecorder, prepared, *, outcome: str,
                     error: BaseException | None = None) -> None:
    payload = {
        "tool_name": prepared.tool_name,
        "exact_request": _request_payload(prepared.request),
        "outcome": outcome,
    }
    if error is not None:
        payload.update({
            "error_type": type(error).__name__,
            "reason": str(error)[:512],
        })
    trace.record(
        "k11.eac_action_terminal",
        source="benchmarks.minecraft.k11_trace.instrument_runtime",
        payload=payload,
    )


def instrument_runtime(runtime, trace: K11TraceRecorder):
    """Instrument one MinecraftEACRuntime instance without changing EAC semantics."""
    if getattr(runtime, "_k11_trace_instrumented", False):
        return runtime
    runtime._k11_trace_instrumented = True
    runtime._k11_trace_recorder = trace

    original_prepare = runtime.prepare_tool
    original_execute = runtime.execute_prepared
    original_ingest = runtime.authority.ingest_record

    def prepare_tool(self, tool_name, function, args, kwargs):
        # Use the existing runtime lock as the linearization boundary.  The outer
        # acquisition is re-entrant and only extends the existing critical section
        # through the in-memory marker append.
        with self._lock:
            prepared = original_prepare(tool_name, function, args, kwargs)
            candidate = self.authority._candidates[prepared.request.candidate_id]
            manifest = candidate.manifest
            trace.record(
                "k11.eac_action_prepared",
                source="MinecraftEACRuntime.prepare_tool",
                payload={
                    "tool_name": tool_name,
                    "exact_request": _request_payload(prepared.request),
                    "epre": [_proposition_payload(item) for item in candidate.epre],
                    "actor_scope": _plain(candidate.actor),
                    "mode": self.mode,
                    "classification_identity": self.classification_identity,
                    "manifest_fingerprint": getattr(manifest, "fingerprint", None),
                    "runtime_sequence": self._sequence,
                    "fluent_revision": self._fluent_revision,
                },
            )

            gateway = prepared.gateway
            private_native_name = "_EffectGateway__native"
            native_effect = getattr(gateway, private_native_name)
            if not getattr(native_effect, "_k11_native_wrapper", False):
                @wraps(native_effect)
                def traced_native(request):
                    request_value = _request_payload(request)
                    trace.record(
                        "k11.eac_native_effect_entered",
                        source="EffectGateway.native_effect",
                        payload={"tool_name": tool_name, "exact_request": request_value},
                    )
                    try:
                        result = native_effect(request)
                    except BaseException as exc:
                        trace.record(
                            "k11.eac_native_effect_completed",
                            source="EffectGateway.native_effect",
                            payload={
                                "tool_name": tool_name,
                                "exact_request": request_value,
                                "outcome": "effect_unknown",
                                "error_type": type(exc).__name__,
                            },
                        )
                        raise
                    else:
                        outcome = getattr(result, "outcome", "succeeded")
                        trace.record(
                            "k11.eac_native_effect_completed",
                            source="EffectGateway.native_effect",
                            payload={
                                "tool_name": tool_name,
                                "exact_request": request_value,
                                "outcome": outcome,
                            },
                        )
                        return result

                traced_native._k11_native_wrapper = True
                setattr(gateway, private_native_name, traced_native)
            return prepared

    def execute_prepared(self, prepared):
        with self._lock:
            trace.record(
                "k11.eac_execution_decision_attempted",
                source="MinecraftEACRuntime.execute_prepared",
                payload={
                    "tool_name": prepared.tool_name,
                    "exact_request": _request_payload(prepared.request),
                    "runtime_sequence": self._sequence,
                    "fluent_revision": self._fluent_revision,
                },
            )
            try:
                result = original_execute(prepared)
            except BaseException as exc:
                _record_terminal(trace, prepared, outcome="raised", error=exc)
                raise
            else:
                if isinstance(result, Mapping) and result.get("status") is not True:
                    outcome = "native_failed"
                else:
                    outcome = "native_completed"
                _record_terminal(trace, prepared, outcome=outcome)
                return result

    def ingest_record(authority_self, record, *, proposition, root_id, revision,
                      provenance_id, supersedes=()):
        # MinecraftEACRuntime.ingest_actor_record calls this while holding the
        # pre-existing runtime lock, so the marker remains inside that boundary.
        root = original_ingest(
            record,
            proposition=proposition,
            root_id=root_id,
            revision=revision,
            provenance_id=provenance_id,
            supersedes=supersedes,
        )
        visible = record.get("visible_to")
        actor_id = visible[0] if isinstance(visible, (list, tuple)) and len(visible) == 1 else None
        scope = current_scope()
        if scope is None or scope.actor_id != actor_id:
            scope = K11TraceScope(trace.run_id, actor_id=actor_id)
        trace.record(
            "k11.eac_evidence_ingested",
            source="RuntimeAuthority.ingest_record",
            scope=scope,
            payload={
                "root_id": root.root_id,
                "record_type": record.get("type"),
                "proposition": _proposition_payload(proposition),
                "revision": revision,
                "supersedes": list(supersedes),
                "source": record.get("source"),
                "provenance_id": provenance_id,
                "visible_to": _plain(root.visible_to),
                "source_stream_id": root.source_stream_id,
                "source_stream_revision": root.source_stream_revision,
                "runtime_sequence": runtime._sequence,
            },
        )
        return root

    runtime.prepare_tool = MethodType(prepare_tool, runtime)
    runtime.execute_prepared = MethodType(execute_prepared, runtime)
    runtime.authority.ingest_record = MethodType(ingest_record, runtime.authority)
    return runtime


class K11ProcessInstrumentation:
    """Process-local wrappers for task/agent/model/tool correlation.

    The wrappers are installed only around K11 pilot/final runs and restored on
    exit.  They do not alter planner decisions, tool inputs, model prompts, or
    scheduling controls.
    """

    def __init__(self, trace: K11TraceRecorder):
        self.trace = trace
        self._restores: list[tuple[Any, str, Any]] = []

    def _patch(self, owner, name: str, replacement) -> None:
        original = getattr(owner, name)
        self._restores.append((owner, name, original))
        setattr(owner, name, replacement)

    def __enter__(self):
        from benchmarks.minecraft import eac_runtime as eac_runtime_module
        from env.env import VillagerBench
        from env.minecraft_client import LLMHandler
        from pipeline.agent import BaseAgent

        trace = self.trace

        original_env_init = VillagerBench.__init__
        @wraps(original_env_init)
        def env_init(env_self, *args, **kwargs):
            original_env_init(env_self, *args, **kwargs)
            env_self._k11_trace_recorder = trace
        self._patch(VillagerBench, "__init__", env_init)

        original_step = BaseAgent.step
        @wraps(original_step)
        def agent_step(agent_self, task, *args, **kwargs):
            recorder = getattr(agent_self.env, "_k11_trace_recorder", None)
            if recorder is None:
                return original_step(agent_self, task, *args, **kwargs)
            task_id = str(getattr(task, "id", "")) or None
            step_id = recorder.new_identity("agent-step", actor_id=agent_self.name)
            scope = K11TraceScope(
                recorder.run_id,
                task_id=task_id,
                actor_id=agent_self.name,
                agent_step_id=step_id,
            )
            with use_scope(scope):
                recorder.record(
                    "k11.agent_step_started",
                    source="BaseAgent.step",
                    payload={"task_id": task_id},
                )
                try:
                    result = original_step(agent_self, task, *args, **kwargs)
                except BaseException as exc:
                    recorder.record(
                        "k11.agent_step_completed",
                        source="BaseAgent.step",
                        payload={"outcome": "raised", "error_type": type(exc).__name__},
                    )
                    raise
                else:
                    recorder.record(
                        "k11.agent_step_completed",
                        source="BaseAgent.step",
                        payload={"outcome": "returned"},
                    )
                    return result
        self._patch(BaseAgent, "step", agent_step)

        original_guard = VillagerBench._guard_tool_action
        @wraps(original_guard)
        def guard_tool_action(env_self, tool, *, actor_name=None):
            guarded_tool = original_guard(env_self, tool, actor_name=actor_name)
            original_func = getattr(guarded_tool, "func", None)
            recorder = getattr(env_self, "_k11_trace_recorder", None)
            if recorder is None or not callable(original_func):
                return guarded_tool

            @wraps(original_func)
            def traced_tool(*args, **kwargs):
                parent = current_scope()
                call_id = recorder.new_identity("tool-call", actor_id=actor_name)
                scope = K11TraceScope(
                    recorder.run_id,
                    task_id=parent.task_id if parent else None,
                    actor_id=actor_name or (parent.actor_id if parent else None),
                    agent_step_id=parent.agent_step_id if parent else None,
                    tool_call_id=call_id,
                )
                tool_name = getattr(tool, "name", None) or getattr(original_func, "__name__", "")
                with use_scope(scope):
                    recorder.record(
                        "k11.tool_call_entered",
                        source="VillagerBench._guard_tool_action",
                        payload={"tool_name": tool_name},
                    )
                    try:
                        result = original_func(*args, **kwargs)
                    except BaseException as exc:
                        recorder.record(
                            "k11.tool_call_exited",
                            source="VillagerBench._guard_tool_action",
                            payload={
                                "tool_name": tool_name,
                                "outcome": "raised",
                                "error_type": type(exc).__name__,
                            },
                        )
                        raise
                    else:
                        recorder.record(
                            "k11.tool_call_exited",
                            source="VillagerBench._guard_tool_action",
                            payload={
                                "tool_name": tool_name,
                                "outcome": "returned",
                                "status": result.get("status") if isinstance(result, Mapping) else None,
                            },
                        )
                        return result

            guarded_tool.func = traced_tool
            return guarded_tool
        self._patch(VillagerBench, "_guard_tool_action", guard_tool_action)

        original_llm_start = LLMHandler.on_llm_start
        @wraps(original_llm_start)
        def llm_start(handler_self, serialized, prompts, **kwargs):
            result = original_llm_start(handler_self, serialized, prompts, **kwargs)
            scope = current_scope()
            if scope is not None:
                model_call_id = trace.new_identity("model-call", actor_id=scope.actor_id)
                stack = getattr(handler_self, "_k11_model_call_stack", None)
                if stack is None:
                    stack = []
                    handler_self._k11_model_call_stack = stack
                stack.append(model_call_id)
                model_name = serialized.get("name") if isinstance(serialized, Mapping) else None
                trace.record(
                    "k11.model_call_started",
                    source="LLMHandler.on_llm_start",
                    payload={"model_call_id": model_call_id, "model_name": model_name},
                )
            return result
        self._patch(LLMHandler, "on_llm_start", llm_start)

        original_llm_end = LLMHandler.on_llm_end
        @wraps(original_llm_end)
        def llm_end(handler_self, llm_result, **kwargs):
            result = original_llm_end(handler_self, llm_result, **kwargs)
            scope = current_scope()
            if scope is not None:
                stack = getattr(handler_self, "_k11_model_call_stack", [])
                model_call_id = stack.pop() if stack else None
                trace.record(
                    "k11.model_call_completed",
                    source="LLMHandler.on_llm_end",
                    payload={"model_call_id": model_call_id},
                )
            return result
        self._patch(LLMHandler, "on_llm_end", llm_end)

        original_install = eac_runtime_module.install_minecraft_eac
        @wraps(original_install)
        def install_eac(*args, **kwargs):
            runtime = original_install(*args, **kwargs)
            return instrument_runtime(runtime, trace)
        self._patch(eac_runtime_module, "install_minecraft_eac", install_eac)

        # Local VLLM BaseAgent calls do not use LLMHandler.  Capture those model
        # calls through the model method while a BaseAgent K11 scope is active.
        try:
            from model.vllm_model import VLLMLanguageModel
            original_generate = VLLMLanguageModel.few_shot_generate_thoughts
            @wraps(original_generate)
            def generate(model_self, *args, **kwargs):
                scope = current_scope()
                if scope is None:
                    return original_generate(model_self, *args, **kwargs)
                model_call_id = trace.new_identity("model-call", actor_id=scope.actor_id)
                trace.record(
                    "k11.model_call_started",
                    source="VLLMLanguageModel.few_shot_generate_thoughts",
                    payload={
                        "model_call_id": model_call_id,
                        "temperature": kwargs.get("temperature"),
                    },
                )
                try:
                    result = original_generate(model_self, *args, **kwargs)
                except BaseException as exc:
                    trace.record(
                        "k11.model_call_failed",
                        source="VLLMLanguageModel.few_shot_generate_thoughts",
                        payload={
                            "model_call_id": model_call_id,
                            "error_type": type(exc).__name__,
                        },
                    )
                    raise
                else:
                    trace.record(
                        "k11.model_call_completed",
                        source="VLLMLanguageModel.few_shot_generate_thoughts",
                        payload={"model_call_id": model_call_id},
                    )
                    return result
            self._patch(VLLMLanguageModel, "few_shot_generate_thoughts", generate)
        except (ImportError, AttributeError):
            pass

        return self

    def __exit__(self, exc_type, exc, tb):
        for owner, name, original in reversed(self._restores):
            setattr(owner, name, original)
        self._restores.clear()
        return False


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
    terminals: dict[str, list[Mapping[str, Any]]] = {}
    evidence_count = 0
    for event in events:
        event_type = event.get("event_type")
        if event_type not in K11_EVENT_TYPES:
            warnings.append(f"unknown event type: {event_type}")
            continue
        payload = event.get("payload", {})
        request = payload.get("exact_request") if isinstance(payload, Mapping) else None
        candidate_id = request.get("candidate_id") if isinstance(request, Mapping) else None
        if event_type == "k11.eac_evidence_ingested":
            evidence_count += 1
        if candidate_id is None:
            continue
        table = None
        if event_type == "k11.eac_action_prepared":
            table = prepared
        elif event_type == "k11.eac_execution_decision_attempted":
            table = decisions
        elif event_type == "k11.eac_native_effect_entered":
            table = native_entries
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

    for candidate_id in native_entries:
        if candidate_id not in decisions:
            errors.append(f"candidate {candidate_id} reached native effect without decision marker")

    instrumentation_errors = artifact.get("instrumentation_errors", [])
    if instrumentation_errors:
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
            "terminals": sum(len(rows) for rows in terminals.values()),
            "evidence_ingestions": evidence_count,
        },
    }
