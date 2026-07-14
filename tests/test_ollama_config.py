import importlib
import json

from model import ollama_config
from model.ollama_config import OLLAMA_API_BASE, OLLAMA_API_KEY, OLLAMA_MODEL, configure_ollama_agent, load_agent_api_key_list, make_ollama_llm_config


def test_ollama_default_endpoint_is_local_openai_compatible_url():
    assert OLLAMA_API_BASE == "http://localhost:11434/v1"


def test_ollama_default_model_is_local_smoke_model():
    assert OLLAMA_MODEL == "gemma4:12b"


def test_load_agent_api_key_list_falls_back_to_ollama_key_when_file_missing(tmp_path):
    missing_path = tmp_path / "API_KEY_LIST"

    assert load_agent_api_key_list(missing_path) == [OLLAMA_API_KEY]


def test_load_agent_api_key_list_preserves_legacy_file_keys(tmp_path):
    key_path = tmp_path / "API_KEY_LIST"
    key_path.write_text(json.dumps({"AGENT_KEY": ["key-a", "key-b"]}), encoding="utf-8")

    assert load_agent_api_key_list(key_path) == ["key-a", "key-b"]


def test_load_agent_api_key_list_accepts_legacy_string_key(tmp_path):
    key_path = tmp_path / "API_KEY_LIST"
    key_path.write_text(json.dumps({"AGENT_KEY": "key-a"}), encoding="utf-8")

    assert load_agent_api_key_list(key_path) == ["key-a"]


def test_load_agent_api_key_list_falls_back_when_legacy_key_list_is_empty(tmp_path):
    key_path = tmp_path / "API_KEY_LIST"
    key_path.write_text(json.dumps({"AGENT_KEY": []}), encoding="utf-8")

    assert load_agent_api_key_list(key_path) == [OLLAMA_API_KEY]


def test_make_ollama_llm_config_uses_explicit_argument_overrides():
    config = make_ollama_llm_config(
        api_model="custom-model",
        api_base="http://ollama.example/v1",
        api_key="custom-key",
    )

    assert config["api_model"] == "custom-model"
    assert config["api_base"] == "http://ollama.example/v1"
    assert config["api_key"] == "custom-key"
    assert config["api_key_list"] == ["custom-key"]


def test_configure_ollama_agent_uses_explicit_argument_overrides():
    class Agent:
        pass

    configure_ollama_agent(
        Agent,
        api_model="custom-model",
        api_base="http://ollama.example/v1",
        api_key="custom-key",
    )

    assert Agent.provider == "ollama"
    assert Agent.model == "custom-model"
    assert Agent.base_url == "http://ollama.example/v1"
    assert Agent.api_key_list == ["custom-key"]


def test_ollama_environment_overrides(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_BASE", "http://env-ollama/v1")
    monkeypatch.setenv("OLLAMA_MODEL", "env-model")
    monkeypatch.setenv("OLLAMA_API_KEY", "env-key")

    reloaded = importlib.reload(ollama_config)
    try:
        config = reloaded.make_ollama_llm_config()
        assert config["api_base"] == "http://env-ollama/v1"
        assert config["api_model"] == "env-model"
        assert config["api_key"] == "env-key"
        assert config["api_key_list"] == ["env-key"]
    finally:
        importlib.reload(ollama_config)
