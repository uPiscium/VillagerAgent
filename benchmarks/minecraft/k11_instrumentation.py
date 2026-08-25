"""Process-local observability hooks for K11.

The normal runtime source path is unchanged when this context manager is not
active.  Critical EAC markers are appended in memory only.  No sleep, network
request, semantic reevaluation, filesystem write, or new EAC lock is introduced.
"""
from __future__ import annotations

from functools import wraps
from types import MethodType
from typing import Any, Mapping

from benchmarks.minecraft.k11_trace import (
    K11TraceRecorder,
    K11TraceScope,
    current_scope,
    use_scope,
)


def _native_wrapper(trace: K11TraceRecorder, native_effect):
    if getattr(native_effect, "_k11_native_wrapper", False):
        return native_effect

    @wraps(native_effect)
    def traced_native(request):
        trace.record(
            "k11.eac_native_effect_entered",
            source="EffectGateway.native_effect",
            payload={"exact_request": request},
        )
        try:
            result = native_effect(request)
        except BaseException as exc:
            trace.record(
                "k11.eac_native_effect_completed",
                source="EffectGateway.native_effect",
                payload={
                    "exact_request": request,
                    "outcome": "effect_unknown",
                    "error_type": type(exc).__name__,
                },
            )
            raise
        else:
            trace.record(
                "k11.eac_native_effect_completed",
                source="EffectGateway.native_effect",
                payload={
                    "exact_request": request,
                    "outcome": getattr(result, "outcome", "succeeded"),
                },
            )
            return result

    traced_native._k11_native_wrapper = True
    return traced_native


def _terminal(trace: K11TraceRecorder, prepared, *, outcome: str,
              error: BaseException | None = None) -> None:
    payload: dict[str, Any] = {
        "tool_name": prepared.tool_name,
        "exact_request": prepared.request,
        "outcome": outcome,
    }
    if error is not None:
        payload.update({"error_type": type(error).__name__, "reason": str(error)[:512]})
    trace.record(
        "k11.eac_action_terminal",
        source="benchmarks.minecraft.k11_instrumentation.instrument_runtime",
        payload=payload,
    )


def instrument_runtime(runtime, trace: K11TraceRecorder):
    """Attach exact EAC lifecycle markers to one existing runtime instance."""
    if getattr(runtime, "_k11_trace_instrumented", False):
        return runtime
    runtime._k11_trace_instrumented = True
    runtime._k11_trace_recorder = trace

    original_prepare = runtime.prepare_tool
    original_execute = runtime.execute_prepared
    original_ingest = runtime.authority.ingest_record

    def prepare_tool(self, tool_name, function, args, kwargs):
        # The outer acquisition is the same pre-existing RLock.  It holds the
        # original prepare linearization through exactly one in-memory marker.
        with self._lock:
            prepared = original_prepare(tool_name, function, args, kwargs)
            # Real K11 runs have already wrapped EffectGateway.__init__.  Direct
            # unit fixtures are supported by wrapping the instance before the
            # preparation marker, so wrapper setup is outside the measured
            # prepare->decision interval.
            gateway = prepared.gateway
            native_name = "_EffectGateway__native"
            native_effect = getattr(gateway, native_name)
            if not getattr(native_effect, "_k11_native_wrapper", False):
                setattr(gateway, native_name, _native_wrapper(trace, native_effect))
            candidate = self.authority._candidates[prepared.request.candidate_id]
            manifest = candidate.manifest
            trace.record(
                "k11.eac_action_prepared",
                source="MinecraftEACRuntime.prepare_tool",
                payload={
                    "tool_name": tool_name,
                    "exact_request": prepared.request,
                    "epre": candidate.epre,
                    "actor_scope": candidate.actor,
                    "mode": self.mode,
                    "classification_identity": self.classification_identity,
                    "manifest_fingerprint": getattr(manifest, "fingerprint", None),
                    "runtime_sequence": self._sequence,
                    "fluent_revision": self._fluent_revision,
                },
            )
            return prepared

    def execute_prepared(self, prepared):
        try:
            with self._lock:
                trace.record(
                    "k11.eac_execution_decision_attempted",
                    source="MinecraftEACRuntime.execute_prepared",
                    payload={
                        "tool_name": prepared.tool_name,
                        "exact_request": prepared.request,
                        "runtime_sequence": self._sequence,
                        "fluent_revision": self._fluent_revision,
                    },
                )
                result = original_execute(prepared)
        except BaseException as exc:
            # Terminal bookkeeping is deliberately outside the EAC lock extension.
            _terminal(trace, prepared, outcome="raised", error=exc)
            raise
        else:
            outcome = (
                "native_failed"
                if isinstance(result, Mapping) and result.get("status") is not True
                else "native_completed"
            )
            _terminal(trace, prepared, outcome=outcome)
            return result

    def ingest_record(authority_self, record, *, proposition, root_id, revision,
                      provenance_id, supersedes=()):
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
                "proposition": proposition,
                "revision": revision,
                "supersedes": tuple(supersedes),
                "source": record.get("source"),
                "provenance_id": provenance_id,
                "visible_to": root.visible_to,
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
    """Install K11-only process hooks and restore every patched symbol on exit."""

    def __init__(self, trace: K11TraceRecorder):
        self.trace = trace
        self._restores: list[tuple[Any, str, Any]] = []

    def _patch(self, owner, name: str, replacement) -> None:
        original = getattr(owner, name)
        self._restores.append((owner, name, original))
        setattr(owner, name, replacement)

    def __enter__(self):
        from benchmarks.common.eac.gateway import EffectGateway
        from benchmarks.minecraft import eac_runtime as eac_runtime_module
        from env.env import VillagerBench
        from env.minecraft_client import LLMHandler
        from pipeline.agent import BaseAgent

        trace = self.trace

        # Wrap native callbacks at gateway construction time.  Consequently the
        # per-action prepare marker can be the final instrumentation operation
        # before the pre-existing runtime lock is released.
        original_gateway_init = EffectGateway.__init__
        @wraps(original_gateway_init)
        def gateway_init(gateway_self, authority, native_effect, *, env_pre=None, sec_pre=None):
            return original_gateway_init(
                gateway_self,
                authority,
                _native_wrapper(trace, native_effect),
                env_pre=env_pre,
                sec_pre=sec_pre,
            )
        self._patch(EffectGateway, "__init__", gateway_init)

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
                trace.record(
                    "k11.model_call_started",
                    source="LLMHandler.on_llm_start",
                    payload={
                        "model_call_id": model_call_id,
                        "model_name": serialized.get("name") if isinstance(serialized, Mapping) else None,
                    },
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

        original_llm_error = LLMHandler.on_llm_error
        @wraps(original_llm_error)
        def llm_error(handler_self, error, **kwargs):
            result = original_llm_error(handler_self, error, **kwargs)
            scope = current_scope()
            if scope is not None:
                stack = getattr(handler_self, "_k11_model_call_stack", [])
                model_call_id = stack.pop() if stack else None
                trace.record(
                    "k11.model_call_failed",
                    source="LLMHandler.on_llm_error",
                    payload={"model_call_id": model_call_id, "error_type": type(error).__name__},
                )
            return result
        self._patch(LLMHandler, "on_llm_error", llm_error)

        original_install = eac_runtime_module.install_minecraft_eac
        @wraps(original_install)
        def install_eac(*args, **kwargs):
            return instrument_runtime(original_install(*args, **kwargs), trace)
        self._patch(eac_runtime_module, "install_minecraft_eac", install_eac)

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
                    payload={"model_call_id": model_call_id, "temperature": kwargs.get("temperature")},
                )
                try:
                    result = original_generate(model_self, *args, **kwargs)
                except BaseException as exc:
                    trace.record(
                        "k11.model_call_failed",
                        source="VLLMLanguageModel.few_shot_generate_thoughts",
                        payload={"model_call_id": model_call_id, "error_type": type(exc).__name__},
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
