"""Process-local observability hooks for K11.

The normal runtime source path is unchanged when this context manager is not
active.  Critical EAC markers are appended in memory only.  No sleep, network
request, semantic reevaluation, filesystem write, or new EAC lock is introduced.
"""
from __future__ import annotations

import math
import threading
import time
from functools import wraps
from types import MethodType
from typing import Any, Mapping

from benchmarks.minecraft.k11_trace import (
    K11TraceRecorder,
    PROSPECTIVE_TRACE_SCHEMA_VERSION,
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


def _controlled_shutdown_is_complete(controller, exc: BaseException) -> bool:
    """Accept only the controller's own horizon-interruption failure and proof."""
    # The controller-owned context is authoritative; an exception attribute is
    # only diagnostic and must not independently authorize suppression.
    context = getattr(controller, "shutdown_context", None)
    failure = getattr(controller, "_first_failure", None)
    if (not isinstance(failure, tuple) or len(failure) != 3
            or failure[0] is not exc or not isinstance(failure[2], Mapping)
            or failure[2].get("thread") != "run"
            or not str(failure[2].get("error", "")).startswith("Controller shutdown incomplete")
            or failure[2].get("checkpoint_error")):
        return False
    if not isinstance(context, Mapping) or context.get("shutdown_complete") is not True:
        return False
    return bool(context.get("interrupted_task_ids")) and all(
        not context.get(key) for key in (
        "live_threads",
        "undrained_queues",
        "active_task_ids",
        "incomplete_submission_task_ids",
        )
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


class _ThreadingObservationWaiter:
    """The production waiter; tests can replace it with a deterministic seam."""

    def __call__(self, delay: float, callback):
        timer = threading.Timer(delay, callback)
        timer.daemon = True
        timer.start()
        return timer


class _ObservationWindow:
    def __init__(self, trace, horizon_seconds, waiter, clock):
        self.trace = trace
        self.horizon_seconds = horizon_seconds
        self.waiter = waiter
        self.clock = clock
        self._lock = threading.Lock()
        self._closed = False
        self._horizon_reached = False
        self._timer = None
        self._armed = False
        self.opened_at = None
        self.closed_at = None
        self.horizon_at = None

    @property
    def horizon_reached(self):
        with self._lock:
            return self._horizon_reached

    def open(self, *, arm=True):
        self.opened_at = self.clock()
        self.horizon_at = self.opened_at + round(self.horizon_seconds * 1_000_000_000)
        self.trace.record(
            "k11.observation_window_opened",
            source="GlobalController.run",
            payload={
                "configured_horizon_seconds": self.horizon_seconds,
                "horizon_monotonic_ns": self.horizon_at,
            },
            monotonic_ns=self.opened_at,
        )
        if arm:
            self.arm(now=self.opened_at)

    def arm(self, *, now=None):
        with self._lock:
            if self._armed or self._closed:
                return False
            self._armed = True
        current = self.clock() if now is None else now
        delay = max(0.0, (self.horizon_at - current) / 1_000_000_000)
        self._timer = self.waiter(delay, self._on_horizon)
        return True

    def _close(self, reason: str, *, close_ns: int, shutdown_requested: bool):
        with self._lock:
            if self._closed:
                return False
            self._closed = True
            self._horizon_reached = reason == "fixed_observation_horizon"
            self.closed_at = close_ns
        payload = {
            "reason": reason,
            "configured_horizon_seconds": self.horizon_seconds,
            "window_close_monotonic_ns": self.closed_at,
            "shutdown_requested": shutdown_requested,
        }
        if self.trace.schema_version != PROSPECTIVE_TRACE_SCHEMA_VERSION:
            self.trace.record("k11.observation_window_closed", source="GlobalController.run",
                              payload=payload, monotonic_ns=self.closed_at)
            return True
        freeze = getattr(getattr(self, "controller", None), "with_execution_lock_nonblocking", None)
        if not callable(freeze):
            self.trace.record_and_cut("k11.observation_window_closed", source="GlobalController.run",
                                      payload=payload, monotonic_ns=self.closed_at,
                                      reason=reason, window_open_monotonic_ns=self.opened_at,
                                      window_close_monotonic_ns=self.closed_at,
                                      snapshot_errors=["execution snapshot API unavailable"])
            return True
        pending_cut = []
        def begin_cut(snapshot):
            errors = snapshot.get("errors", []) if isinstance(snapshot, Mapping) else ["malformed execution snapshot"]
            active = snapshot if isinstance(snapshot, Mapping) else None
            pending_cut.append(self.trace.begin_record_and_cut(
                "k11.observation_window_closed",
                source="GlobalController.run",
                payload=payload, monotonic_ns=self.closed_at, reason=reason,
                window_open_monotonic_ns=self.opened_at,
                window_close_monotonic_ns=self.closed_at,
                active_executions=active, snapshot_errors=errors,
            ))
        freeze(
            begin_cut,
            cutoff_monotonic_ns=self.closed_at,
            seal_admission=True,
        )
        if not pending_cut:
            raise RuntimeError("execution snapshot callback did not create measurement cut")
        self.trace.finalize_record_and_cut(pending_cut[0])
        return True

    def _on_horizon(self):
        if self._close(
            "fixed_observation_horizon",
            close_ns=self.horizon_at,
            shutdown_requested=True,
        ):
            # The close marker is intentionally linearized before shutdown is
            # requested, making the end of the measured window authoritative.
            self.controller._request_shutdown()

    def natural_close(self):
        self._cancel()
        with self._lock:
            if self._closed:
                return False
        now = self.clock()
        if now >= self.horizon_at:
            self._on_horizon()
            return self.horizon_reached
        return self._close(
            "natural_runtime_terminal",
            close_ns=now,
            shutdown_requested=False,
        )

    def _cancel(self):
        timer = self._timer
        if timer is not None:
            cancel = getattr(timer, "cancel", None)
            if callable(cancel):
                cancel()

    def dispose(self):
        self._cancel()
        timer = self._timer
        join = getattr(timer, "join", None)
        if callable(join) and timer is not threading.current_thread():
            join()

    def attach_controller(self, controller):
        self.controller = controller


class K11ProcessInstrumentation:
    """Install K11-only process hooks and restore every patched symbol on exit."""

    def __init__(self, trace: K11TraceRecorder, *, observation_horizon_seconds=None,
                 waiter=None, clock=None, observation_waiter=None,
                 monotonic_clock=None):
        if observation_horizon_seconds is not None and (
                isinstance(observation_horizon_seconds, bool)
                or not isinstance(observation_horizon_seconds, (int, float))
                or not math.isfinite(observation_horizon_seconds)
                or observation_horizon_seconds <= 0
        ):
            raise ValueError("observation_horizon_seconds must be positive and finite")
        if waiter is not None and observation_waiter is not None:
            raise TypeError("specify only one observation waiter")
        if clock is not None and monotonic_clock is not None:
            raise TypeError("specify only one monotonic clock")
        self.trace = trace
        self.observation_horizon_seconds = observation_horizon_seconds
        self._observation_waiter = waiter or observation_waiter or _ThreadingObservationWaiter()
        self._observation_clock = clock or monotonic_clock or time.monotonic_ns
        self._observation_run_lock = threading.Lock()
        self._observation_run_started = False
        self._restores: list[tuple[Any, str, Any]] = []

    def _patch(self, owner, name: str, replacement) -> None:
        original = getattr(owner, name)
        self._restores.append((owner, name, original))
        setattr(owner, name, replacement)

    def __enter__(self):
        try:
            return self._install()
        except BaseException:
            self._restore()
            raise

    def _install(self):
        from benchmarks.common.eac.gateway import EffectGateway
        from benchmarks.minecraft import eac_runtime as eac_runtime_module
        from env.env import VillagerBench
        from env.minecraft_client import LLMHandler
        from model.openai_models import OpenAILanguageModel
        from pipeline.agent import BaseAgent

        trace = self.trace

        if self.observation_horizon_seconds is not None:
            from pipeline.controller_tiny import ControllerShutdownError, GlobalController

            original_controller_run = GlobalController.run

            @wraps(original_controller_run)
            def controller_run(controller_self, *args, **kwargs):
                with self._observation_run_lock:
                    if self._observation_run_started:
                        raise RuntimeError(
                            "K11 observation instrumentation supports one controller run per process"
                        )
                    self._observation_run_started = True
                window = _ObservationWindow(
                    trace,
                    self.observation_horizon_seconds,
                    self._observation_waiter,
                    self._observation_clock,
                )
                window.attach_controller(controller_self)
                window.open(arm=False)
                shutdown_event = controller_self.shutdown_event
                original_clear = shutdown_event.clear

                def clear_and_arm():
                    original_clear()
                    window.arm()

                shutdown_event.clear = clear_and_arm
                try:
                    result = original_controller_run(controller_self, *args, **kwargs)
                except ControllerShutdownError as exc:
                    if (window.horizon_reached
                            and _controlled_shutdown_is_complete(controller_self, exc)):
                        return None
                    window.natural_close()
                    raise
                except BaseException:
                    window.natural_close()
                    raise
                else:
                    if not window.horizon_reached:
                        window.natural_close()
                    return result
                finally:
                    shutdown_event.clear = original_clear
                    window.dispose()

            self._patch(GlobalController, "run", controller_run)

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
            model_call_id = trace.new_identity(
                "model-call", actor_id=scope.actor_id if scope is not None else None,
            )
            run_id = kwargs.get("run_id")
            if run_id is not None:
                pending = getattr(handler_self, "_k11_model_calls_by_run_id", None)
                if pending is None:
                    pending = {}
                    handler_self._k11_model_calls_by_run_id = pending
                pending[str(run_id)] = (model_call_id, scope)
            else:
                stack = getattr(handler_self, "_k11_model_call_stack", None)
                if stack is None:
                    stack = []
                    handler_self._k11_model_call_stack = stack
                stack.append((model_call_id, scope))
            trace.record(
                "k11.model_call_started",
                source="LLMHandler.on_llm_start",
                scope=scope,
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
            run_id = kwargs.get("run_id")
            if run_id is not None:
                pending = getattr(handler_self, "_k11_model_calls_by_run_id", {})
                model_call_id, scope = pending.pop(str(run_id), (None, current_scope()))
            else:
                stack = getattr(handler_self, "_k11_model_call_stack", [])
                model_call_id, scope = stack.pop() if stack else (None, current_scope())
            trace.record(
                "k11.model_call_completed",
                source="LLMHandler.on_llm_end",
                scope=scope,
                payload={"model_call_id": model_call_id},
            )
            return result
        self._patch(LLMHandler, "on_llm_end", llm_end)

        original_llm_error = LLMHandler.on_llm_error
        @wraps(original_llm_error)
        def llm_error(handler_self, error, **kwargs):
            result = original_llm_error(handler_self, error, **kwargs)
            run_id = kwargs.get("run_id")
            if run_id is not None:
                pending = getattr(handler_self, "_k11_model_calls_by_run_id", {})
                model_call_id, scope = pending.pop(str(run_id), (None, current_scope()))
            else:
                stack = getattr(handler_self, "_k11_model_call_stack", [])
                model_call_id, scope = stack.pop() if stack else (None, current_scope())
            trace.record(
                "k11.model_call_failed",
                source="LLMHandler.on_llm_error",
                scope=scope,
                payload={"model_call_id": model_call_id, "error_type": type(error).__name__},
            )
            return result
        self._patch(LLMHandler, "on_llm_error", llm_error)

        def instrument_openai_provider(name: str) -> None:
            original_provider_call = getattr(OpenAILanguageModel, name)
            @wraps(original_provider_call)
            def provider_call(model_self, *args, **kwargs):
                scope = current_scope()
                requested_model = kwargs.get("model")
                if requested_model is None and len(args) >= 2:
                    requested_model = args[1]
                model_call_id = trace.new_identity(
                    "model-call", actor_id=scope.actor_id if scope is not None else None,
                )
                source = f"OpenAILanguageModel.{name}"
                trace.record(
                    "k11.model_call_started",
                    source=source,
                    scope=scope,
                    payload={
                        "model_call_id": model_call_id,
                        "model_name": requested_model or getattr(model_self, "api_model", None),
                    },
                )
                try:
                    result = original_provider_call(model_self, *args, **kwargs)
                except BaseException as exc:
                    trace.record(
                        "k11.model_call_failed",
                        source=source,
                        scope=scope,
                        payload={"model_call_id": model_call_id, "error_type": type(exc).__name__},
                    )
                    raise
                else:
                    trace.record(
                        "k11.model_call_completed",
                        source=source,
                        scope=scope,
                        payload={"model_call_id": model_call_id},
                    )
                    return result
            self._patch(OpenAILanguageModel, name, provider_call)

        instrument_openai_provider("gpt_api")
        instrument_openai_provider("gpt_api_stream")

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

    def _restore(self) -> None:
        for owner, name, original in reversed(self._restores):
            setattr(owner, name, original)
        self._restores.clear()

    def __exit__(self, exc_type, exc, tb):
        self._restore()
        return False
