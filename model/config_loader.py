import json
import os
from pathlib import Path


def load_llm_config(path: str) -> dict:
    config_path = Path(path)

    if not config_path.exists():
        raise FileNotFoundError(f"LLM config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    api_key_env = config.pop("api_key_env", None)
    if api_key_env:
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(f"Environment variable {api_key_env} is not set")
        config["api_key"] = api_key
        config["api_key_list"] = [api_key]

    provider = config.get("provider", "").lower()

    if provider == "ollama":
        config.setdefault("api_key", "ollama")
        config.setdefault("api_key_list", ["ollama"])

    return config
