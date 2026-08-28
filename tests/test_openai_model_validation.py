import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from env.runtime_paths import RuntimePaths
from model.init_model import init_language_model
from model.openai_models import ModelOutputContractError, OpenAILanguageModel, _contains_tag


def test_required_tag_accepts_json_underscore_variant():
    content = '{"assigned_agents": ["Alice"]}'

    assert _contains_tag(content, "assigned agents")


def test_required_tag_accepts_case_and_hyphen_variants():
    content = '{"Required-Subtasks": []}'

    assert _contains_tag(content, "required subtasks")


def test_required_tag_rejects_missing_key():
    content = '{"candidate_agents": ["Alice"]}'

    assert not _contains_tag(content, "assigned agents")


def test_openai_initialization_isolated_from_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runtime_root = tmp_path / "runtime"
    monkeypatch.setattr("model.openai_models.OpenAI", lambda **_: object())

    model = OpenAILanguageModel(api_key="test-key", runtime_paths=RuntimePaths.isolated(runtime_root))

    assert model.runtime_paths == RuntimePaths.isolated(runtime_root)
    assert model.runtime_paths.tokens.exists()
    assert model.runtime_paths.openai_log.exists()
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / ".cache").exists()


def test_openai_cache_isolated_between_runtime_roots(tmp_path, monkeypatch):
    monkeypatch.setattr("model.openai_models.OpenAI", lambda **_: object())
    first = OpenAILanguageModel(
        api_key="test-key", runtime_paths=RuntimePaths.isolated(tmp_path / "first")
    )
    second = OpenAILanguageModel(
        api_key="test-key", runtime_paths=RuntimePaths.isolated(tmp_path / "second")
    )

    first.save_cache("prompt", "first-response")

    assert first.cache_api_call_handler("prompt", 1, 0) == "first-response"
    assert second.cache_api_call_handler("prompt", 1, 0) is None
    assert json.loads(first.runtime_paths.openai_cache.read_text()) == {"prompt": "first-response"}
    assert not (tmp_path / ".cache" / "openai.cache").exists()


def test_openai_first_cache_write_consistently_retains_response(tmp_path, monkeypatch):
    monkeypatch.setattr("model.openai_models.OpenAI", lambda **_: object())
    monkeypatch.chdir(tmp_path)
    legacy = OpenAILanguageModel(api_key="test-key", runtime_paths=RuntimePaths.legacy())
    isolated = OpenAILanguageModel(
        api_key="test-key", runtime_paths=RuntimePaths.isolated(tmp_path / "runtime")
    )

    assert legacy.cache_api_call_handler("missing", 1, 0) is None
    assert isolated.cache_api_call_handler("missing", 1, 0) is None
    assert not legacy.runtime_paths.openai_cache.exists()
    assert not isolated.runtime_paths.openai_cache.exists()

    legacy.save_cache("prompt", "legacy-response")
    isolated.save_cache("prompt", "isolated-response")

    assert json.loads(legacy.runtime_paths.openai_cache.read_text()) == {"prompt": "legacy-response"}
    assert json.loads(isolated.runtime_paths.openai_cache.read_text()) == {"prompt": "isolated-response"}


def test_openai_uses_environment_runtime_paths(monkeypatch, tmp_path):
    monkeypatch.setattr("model.openai_models.OpenAI", lambda **_: object())
    paths = RuntimePaths.isolated(tmp_path / "from-env")
    monkeypatch.setenv("VILLAGER_RUNTIME_ROOT", str(paths.root))
    monkeypatch.setenv("VILLAGER_RUNTIME_LAYOUT", "isolated")

    model = OpenAILanguageModel(api_key="test-key")

    assert model.runtime_paths == paths


def test_openai_legacy_layout_keeps_relative_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("model.openai_models.OpenAI", lambda **_: object())

    OpenAILanguageModel(api_key="test-key", runtime_paths=RuntimePaths.legacy())

    assert (tmp_path / "data" / "tokens.json").exists()
    assert (tmp_path / "data" / "openai.logs").exists()
    assert not (tmp_path / "cache" / "openai.cache").exists()


def test_openai_factory_forwards_explicit_runtime_paths(tmp_path, monkeypatch):
    monkeypatch.setattr("model.openai_models.OpenAI", lambda **_: object())
    paths = RuntimePaths.isolated(tmp_path / "factory")

    model = init_language_model({
        "provider": "ollama", "api_model": "test-model",
        "api_base": "http://127.0.0.1:11434/v1", "runtime_paths": paths,
    })

    assert model.runtime_paths == paths
    assert paths.tokens.exists()


def test_openai_concurrent_cache_updates_are_not_lost(tmp_path, monkeypatch):
    monkeypatch.setattr("model.openai_models.OpenAI", lambda **_: object())
    model = OpenAILanguageModel(
        api_key="test-key", runtime_paths=RuntimePaths.isolated(tmp_path / "shared")
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda index: model.save_cache(f"prompt-{index}", index), range(32)))

    cache = json.loads(model.runtime_paths.openai_cache.read_text())
    assert cache == {f"prompt-{index}": index for index in range(32)}


def test_openai_concurrent_token_updates_are_not_lost(tmp_path, monkeypatch):
    monkeypatch.setattr("model.openai_models.OpenAI", lambda **_: object())
    model = OpenAILanguageModel(
        api_key="test-key", api_model="test-model",
        runtime_paths=RuntimePaths.isolated(tmp_path / "shared-tokens"),
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: model.update_token_usage(1, 2), range(32)))

    tokens = json.loads(model.runtime_paths.tokens.read_text())
    assert tokens["successful_requests"] == 32
    assert tokens["tokens_used"] == 96


def test_openai_client_disables_sdk_retries_and_bounds_timeout(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr("model.openai_models.OpenAI", lambda **kwargs: captured.append(kwargs) or object())

    OpenAILanguageModel(
        api_key="test-key",
        runtime_paths=RuntimePaths.isolated(tmp_path / "runtime"),
        request_timeout_seconds=7,
    )

    assert captured[0]["max_retries"] == 0
    assert captured[0]["timeout"].read == 7
    assert captured[0]["timeout"].connect == 5


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("request_timeout_seconds", float("inf")),
        ("request_timeout_seconds", float("nan")),
        ("retry_delay_seconds", float("inf")),
        ("retry_delay_seconds", float("nan")),
    ],
)
def test_openai_rejects_nonfinite_transport_bounds(tmp_path, monkeypatch, argument, value):
    monkeypatch.setattr("model.openai_models.OpenAI", lambda **_: object())

    with pytest.raises(ValueError):
        OpenAILanguageModel(
            api_key="test-key",
            runtime_paths=RuntimePaths.isolated(tmp_path / argument),
            **{argument: value},
        )


def test_openai_transport_retries_are_bounded_and_metadata_only(tmp_path, monkeypatch):
    monkeypatch.setattr("model.openai_models.OpenAI", lambda **_: object())
    paths = RuntimePaths.isolated(tmp_path / "runtime")
    model = OpenAILanguageModel(
        api_key="test-key",
        api_base="http://provider.invalid/v1",
        runtime_paths=paths,
        model_call_attempts=3,
        retry_delay_seconds=0,
    )
    calls = []

    def fail(*_args, **_kwargs):
        calls.append(1)
        raise RuntimeError("transport marker")

    monkeypatch.setattr(model, "gpt_api_stream", fail)
    with pytest.raises(RuntimeError, match="transport marker"):
        model.few_shot_generate_thoughts(
            "secret-system-prompt", "secret-user-prompt", cache_enabled=False,
        )

    diagnostics_text = paths.openai_diagnostics.read_text(encoding="utf-8")
    diagnostics = [json.loads(line) for line in diagnostics_text.splitlines()]
    assert len(calls) == 3
    assert [row["attempt"] for row in diagnostics] == [1, 2, 3]
    assert all(row["outcome"] == "transport_failure" for row in diagnostics)
    assert "secret-system-prompt" not in diagnostics_text
    assert "secret-user-prompt" not in diagnostics_text
    assert "transport marker" not in diagnostics_text


def test_openai_stream_enforces_wall_clock_budget(tmp_path, monkeypatch):
    monkeypatch.setattr("model.openai_models.OpenAI", lambda **_: object())
    model = OpenAILanguageModel(
        api_key="test-key",
        runtime_paths=RuntimePaths.isolated(tmp_path / "deadline"),
        request_timeout_seconds=0.001,
        model_call_attempts=1,
    )

    def delayed_stream():
        time.sleep(0.01)
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="late"), finish_reason=None)]
        )

    model.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: delayed_stream()))
    )

    with pytest.raises(TimeoutError, match="wall-clock budget"):
        model.gpt_api_stream([], "test-model", 0)


@pytest.mark.parametrize(
    ("content", "finish_reason", "category"),
    [
        ("", "stop", "empty_public_content"),
        ("", "length", "truncated_public_content"),
        ("unstructured response", "stop", "missing_required_tags"),
    ],
)
def test_openai_contract_failures_are_classified_without_response_capture(
    tmp_path, monkeypatch, content, finish_reason, category,
):
    monkeypatch.setattr("model.openai_models.OpenAI", lambda **_: object())
    paths = RuntimePaths.isolated(tmp_path / category)
    model = OpenAILanguageModel(
        api_key="test-key",
        runtime_paths=paths,
        model_call_attempts=1,
        retry_delay_seconds=0,
    )

    def respond(*_args, **_kwargs):
        model._response_metadata.value = {
            "chunk_count": 1,
            "public_content_chunks": int(bool(content)),
            "public_content_chars": len(content),
            "reasoning_chunks": 0,
            "reasoning_chars": 0,
            "finish_reason": finish_reason,
        }
        return content

    monkeypatch.setattr(model, "gpt_api_stream", respond)
    secret_tag = "secret-required-tag-value"
    with pytest.raises(ModelOutputContractError) as raised:
        model.few_shot_generate_thoughts(
            "system", "user", cache_enabled=False, check_tags=[secret_tag],
        )

    assert raised.value.category == category
    diagnostics_text = paths.openai_diagnostics.read_text(encoding="utf-8")
    diagnostic = json.loads(diagnostics_text)
    assert diagnostic["outcome"] == "model_contract_failure"
    assert diagnostic["validation_category"] == category
    assert diagnostic["missing_tag_count"] == 1
    assert secret_tag not in diagnostics_text
    if content:
        assert content not in diagnostics_text
