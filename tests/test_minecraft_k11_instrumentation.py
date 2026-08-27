import pytest
from types import SimpleNamespace

from benchmarks.common.eac.gateway import EffectGateway
from benchmarks.minecraft.k11_instrumentation import K11ProcessInstrumentation
from benchmarks.minecraft.k11_trace import K11TraceRecorder, K11TraceScope, use_scope
from env.runtime_paths import RuntimePaths
from model.openai_models import OpenAILanguageModel
from env.minecraft_client import LLMHandler


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
