import pytest
import threading
from types import SimpleNamespace

from benchmarks.common.eac.gateway import EffectGateway
from benchmarks.minecraft.k11_instrumentation import (
    K11ProcessInstrumentation,
    _ObservationWindow,
    _controlled_shutdown_is_complete,
)
from benchmarks.minecraft.k11_trace import K11TraceRecorder, K11TraceScope, use_scope
from env.runtime_paths import RuntimePaths
from model.openai_models import OpenAILanguageModel, ProviderCallCancellationError
from env.minecraft_client import LLMHandler


class _FakeWaiter:
    def __init__(self):
        self.delay = None
        self.callback = None
        self.cancelled = False

    def __call__(self, delay, callback):
        self.delay, self.callback = delay, callback
        return self

    def cancel(self):
        self.cancelled = True


class _FakeController:
    def __init__(self):
        self.shutdown_requests = 0

    def _request_shutdown(self):
        self.shutdown_requests += 1


def test_k11_observation_window_fixed_close_is_authoritative_and_idempotent():
    trace = K11TraceRecorder("k11-window-fixed")
    waiter = _FakeWaiter()
    clock_values = iter((100,))
    window = _ObservationWindow(trace, 5, waiter, lambda: next(clock_values))
    controller = _FakeController()
    window.attach_controller(controller)

    window.open()
    assert waiter.delay == 5
    waiter.callback()
    waiter.callback()

    events = trace.artifact()["events"]
    assert [event["event_type"] for event in events] == [
        "k11.observation_window_opened", "k11.observation_window_closed",
    ]
    assert events[1]["payload"]["reason"] == "fixed_observation_horizon"
    assert events[1]["payload"]["window_close_monotonic_ns"] - events[0]["monotonic_ns"] == 5_000_000_000
    assert controller.shutdown_requests == 1


def test_k11_observation_window_natural_close_cancels_waiter():
    trace = K11TraceRecorder("k11-window-natural")
    waiter = _FakeWaiter()
    clock_values = iter((10, 20))
    window = _ObservationWindow(trace, 2, waiter, lambda: next(clock_values))
    window.attach_controller(_FakeController())
    window.open()
    window.natural_close()
    window.natural_close()

    assert waiter.cancelled
    assert trace.artifact()["events"][1]["payload"]["reason"] == "natural_runtime_terminal"


@pytest.mark.parametrize("horizon", [0, -1, float("inf"), float("nan"), True])
def test_k11_observation_horizon_must_be_positive_finite(horizon):
    with pytest.raises(ValueError, match="positive and finite"):
        K11ProcessInstrumentation(K11TraceRecorder("k11-invalid-window"), observation_horizon_seconds=horizon)


def test_k11_only_suppresses_controller_owned_horizon_interruption():
    expected = RuntimeError("Controller shutdown incomplete; interrupted tasks")
    controller = SimpleNamespace(
        _first_failure=(expected, None, {
            "thread": "run", "error": "Controller shutdown incomplete",
        }),
        shutdown_context={
            "shutdown_complete": True,
            "interrupted_task_ids": ["task-1"],
            "live_threads": [],
            "undrained_queues": [],
            "active_task_ids": [],
            "incomplete_submission_task_ids": [],
        },
    )

    assert _controlled_shutdown_is_complete(controller, expected) is True
    assert _controlled_shutdown_is_complete(
        controller, RuntimeError("unrelated failure"),
    ) is False


def test_k11_does_not_suppress_worker_failure_after_horizon():
    failure = RuntimeError("model transport failed")
    controller = SimpleNamespace(
        _first_failure=(failure, None, {"thread": "worker", "error": str(failure)}),
        shutdown_context={
            "shutdown_complete": True,
            "interrupted_task_ids": ["task-1"],
            "live_threads": [],
            "undrained_queues": [],
            "active_task_ids": [],
            "incomplete_submission_task_ids": [],
        },
    )

    assert _controlled_shutdown_is_complete(controller, failure) is False


def test_k11_does_not_suppress_checkpoint_failure_during_controlled_shutdown():
    failure = RuntimeError("Controller shutdown incomplete")
    controller = SimpleNamespace(
        _first_failure=(failure, None, {
            "thread": "run",
            "error": str(failure),
            "checkpoint_error": {"error_type": "OSError", "error": "disk full"},
        }),
        shutdown_context={
            "shutdown_complete": True,
            "interrupted_task_ids": ["task-1"],
            "live_threads": [],
            "undrained_queues": [],
            "active_task_ids": [],
            "incomplete_submission_task_ids": [],
        },
    )

    assert _controlled_shutdown_is_complete(controller, failure) is False


def test_k11_observation_window_can_arm_after_controller_clear_without_shifting_end():
    trace = K11TraceRecorder("k11-window-delayed-arm")
    waiter = _FakeWaiter()
    clock_values = iter((100, 2_000_000_100))
    window = _ObservationWindow(trace, 5, waiter, lambda: next(clock_values))
    window.attach_controller(_FakeController())

    window.open(arm=False)
    assert waiter.callback is None
    window.arm()

    assert waiter.delay == 3.0
    opened = trace.artifact()["events"][0]
    assert opened["payload"]["horizon_monotonic_ns"] == 5_000_000_100


def test_k11_wrapped_controller_arms_after_clear_and_cannot_lose_immediate_horizon(
    monkeypatch,
):
    from pipeline.controller_tiny import GlobalController

    trace = K11TraceRecorder("k11-window-controller-clear")
    waiter = _FakeWaiter()
    controller = SimpleNamespace(shutdown_event=threading.Event())
    controller._request_shutdown = controller.shutdown_event.set

    def run(fake_controller):
        fake_controller.shutdown_event.clear()
        waiter.callback()
        assert fake_controller.shutdown_event.is_set()
        return "stopped"

    monkeypatch.setattr(GlobalController, "run", run)
    with K11ProcessInstrumentation(
        trace,
        observation_horizon_seconds=5,
        observation_waiter=waiter,
        monotonic_clock=lambda: 100,
    ):
        assert GlobalController.run(controller) == "stopped"
        with pytest.raises(RuntimeError, match="one controller run"):
            GlobalController.run(controller)

    events = trace.artifact()["events"]
    assert [event["event_type"] for event in events] == [
        "k11.observation_window_opened", "k11.observation_window_closed",
    ]
    assert events[-1]["payload"]["reason"] == "fixed_observation_horizon"


def _model():
    model = object.__new__(OpenAILanguageModel)
    model.api_model = "gemma4:12b"
    return model


def test_k11_instruments_direct_openai_compatible_call_without_actor_scope(monkeypatch) -> None:
    def provider_call(unused_self, messages, model, temperature):
        return "ok"

    monkeypatch.setattr(OpenAILanguageModel, "gpt_api", provider_call)
    trace = K11TraceRecorder("k11-direct-openai")
    with K11ProcessInstrumentation(trace):
        assert _model().gpt_api([{"content": "secret-user"}], "override-model", 0) == "ok"

    events = trace.artifact()["events"]
    starts = [event for event in events if event["event_type"] == "k11.model_call_started"]
    completed = [event for event in events if event["event_type"] == "k11.model_call_completed"]
    assert len(starts) == len(completed) == 1
    assert starts[0]["payload"] == {
        "model_call_id": completed[0]["payload"]["model_call_id"],
        "model_name": "override-model",
    }
    assert "secret-user" not in str(events)


def test_k11_instruments_direct_openai_failure_in_actor_scope(monkeypatch) -> None:
    def provider_call(unused_self, messages, model, temperature):
        raise RuntimeError("provider failed")

    monkeypatch.setattr(OpenAILanguageModel, "gpt_api_stream", provider_call)
    trace = K11TraceRecorder("k11-direct-openai-failure")
    scope = K11TraceScope(
        trace.run_id, task_id="task-1", actor_id="Alice", agent_step_id="step-1",
    )
    with K11ProcessInstrumentation(trace), use_scope(scope):
        with pytest.raises(RuntimeError, match="provider failed"):
            _model().gpt_api_stream([{"content": "secret"}], "gemma4:12b", 0)

    events = trace.artifact()["events"]
    starts = [event for event in events if event["event_type"] == "k11.model_call_started"]
    failed = [event for event in events if event["event_type"] == "k11.model_call_failed"]
    assert len(starts) == len(failed) == 1
    assert starts[0]["actor_id"] == failed[0]["actor_id"] == "Alice"
    assert starts[0]["payload"]["model_call_id"] == failed[0]["payload"]["model_call_id"]
    assert failed[0]["payload"]["error_type"] == "RuntimeError"
    assert "provider failed" not in str(events)


def test_k11_instruments_provider_cancellation_as_one_terminal_failure(monkeypatch) -> None:
    def provider_call(unused_self, messages, model, temperature, **kwargs):
        raise ProviderCallCancellationError(
            "cancelled", provider_termination_confirmed=False,
            close_failure_diagnostics={"phase": "provider"},
        )

    monkeypatch.setattr(OpenAILanguageModel, "gpt_api_stream", provider_call)
    trace = K11TraceRecorder("k11-cancelled-openai")
    scope = K11TraceScope(
        trace.run_id, task_id="task-1", actor_id="Alice", agent_step_id="step-1",
    )
    with K11ProcessInstrumentation(trace), use_scope(scope):
        with pytest.raises(ProviderCallCancellationError):
            _model().gpt_api_stream([], "gemma4:12b", 0, cancellation_event=threading.Event())

    events = trace.artifact()["events"]
    starts = [event for event in events if event["event_type"] == "k11.model_call_started"]
    terminals = [event for event in events if event["event_type"] == "k11.model_call_failed"]
    assert len(starts) == len(terminals) == 1
    assert starts[0]["payload"]["model_call_id"] == terminals[0]["payload"]["model_call_id"]
    assert terminals[0]["payload"]["error_type"] == "ProviderCallCancellationError"


def test_k11_does_not_count_openai_cache_hit_as_provider_call(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("model.openai_models.OpenAI", lambda **unused: object())
    model = OpenAILanguageModel(
        api_key="test-key",
        runtime_paths=RuntimePaths.isolated(tmp_path),
    )
    model.save_cache("system\nuser", "cached")
    trace = K11TraceRecorder("k11-direct-openai-cache")

    with K11ProcessInstrumentation(trace):
        assert model.few_shot_generate_thoughts("system", "user", cache_enabled=True) == "cached"

    assert not [
        event for event in trace.artifact()["events"]
        if event["event_type"].startswith("k11.model_call_")
    ]


def test_k11_langchain_callbacks_pair_by_run_id_when_interleaved() -> None:
    trace = K11TraceRecorder("k11-langchain-interleaved")
    handler = LLMHandler()
    first_scope = K11TraceScope(trace.run_id, task_id="task-1", actor_id="Alice", agent_step_id="step-1")
    second_scope = K11TraceScope(trace.run_id, task_id="task-2", actor_id="Bob", agent_step_id="step-2")

    with K11ProcessInstrumentation(trace):
        with use_scope(first_scope):
            handler.on_llm_start({"name": "first"}, ["secret"], run_id="run-1")
        with use_scope(second_scope):
            handler.on_llm_start({"name": "second"}, ["secret"], run_id="run-2")
        handler.on_llm_end(SimpleNamespace(llm_output=None), run_id="run-1")
        handler.on_llm_end(SimpleNamespace(llm_output=None), run_id="run-2")

    events = trace.artifact()["events"]
    starts = {event["actor_id"]: event for event in events if event["event_type"] == "k11.model_call_started"}
    terminals = {event["actor_id"]: event for event in events if event["event_type"] == "k11.model_call_completed"}
    assert set(starts) == set(terminals) == {"Alice", "Bob"}
    assert all(
        starts[actor]["payload"]["model_call_id"] == terminals[actor]["payload"]["model_call_id"]
        for actor in starts
    )


def test_k11_partial_install_failure_restores_already_patched_symbols(monkeypatch) -> None:
    trace = K11TraceRecorder("k11-install-failure")
    instrumentation = K11ProcessInstrumentation(trace)
    original_gateway_init = EffectGateway.__init__
    original_patch = instrumentation._patch
    calls = 0

    def fail_second_patch(owner, name, replacement):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("install failed")
        original_patch(owner, name, replacement)

    monkeypatch.setattr(instrumentation, "_patch", fail_second_patch)

    with pytest.raises(RuntimeError, match="install failed"):
        instrumentation.__enter__()

    assert EffectGateway.__init__ is original_gateway_init
    assert instrumentation._restores == []
