import json

from model.ollama_config import OLLAMA_API_BASE, OLLAMA_API_KEY, OLLAMA_MODEL, load_agent_api_key_list


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
