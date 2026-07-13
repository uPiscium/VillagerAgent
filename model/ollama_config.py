import os
import json
from pathlib import Path

OLLAMA_PROVIDER = "ollama"
OLLAMA_API_BASE = os.environ.get("OLLAMA_API_BASE", "http://localhost:11434/v1")
OLLAMA_API_KEY = os.environ.get("OLLAMA_API_KEY", "ollama")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:12b")


def make_ollama_llm_config(api_model: str | None = None) -> dict:
    return {
        "provider": OLLAMA_PROVIDER,
        "api_key": OLLAMA_API_KEY,
        "api_base": OLLAMA_API_BASE,
        "api_model": api_model or OLLAMA_MODEL,
        "api_key_list": [OLLAMA_API_KEY],
    }


def configure_ollama_agent(agent, api_model: str | None = None) -> None:
    agent.provider = OLLAMA_PROVIDER
    agent.base_url = OLLAMA_API_BASE
    agent.model = api_model or OLLAMA_MODEL
    agent.api_key_list = [OLLAMA_API_KEY]


def load_agent_api_key_list(path: str | Path = "API_KEY_LIST", key: str = "AGENT_KEY") -> list[str]:
    """Load legacy agent keys, or use the Ollama dummy key when absent.

    Ollama/OpenAI-compatible local deployments still require a non-empty key in
    the OpenAI client, but it does not need to be a billable OpenAI key.
    """
    key_path = Path(path)
    if not key_path.exists():
        return [OLLAMA_API_KEY]
    with key_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    keys = payload.get(key, []) if isinstance(payload, dict) else []
    if isinstance(keys, str):
        keys = [keys]
    if not keys:
        return [OLLAMA_API_KEY]
    return list(keys)
