import socket

import pytest
import requests

from benchmarks.craft.adapters.ollama_preflight import (
    OllamaPreflightError,
    OllamaPreflightTimeoutError,
    preflight_ollama_model,
)
from benchmarks.craft.run import _model_identities, _preflight_ollama_models


def test_ollama_preflight_accepts_available_model_and_strips_v1(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "benchmarks.craft.adapters.ollama_preflight.socket.getaddrinfo",
        lambda host, port, type: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )

    def fake_get(url, *, timeout):
        calls.append({"url": url, "timeout": timeout})
        return _FakeResponse({"models": [{
            "name": "qwen3.5:9b",
            "digest": "sha256:immutable",
            "size": 123,
            "modified_at": "2026-07-15T00:00:00Z",
        }]})

    monkeypatch.setattr("benchmarks.craft.adapters.ollama_preflight.requests.get", fake_get)

    result = preflight_ollama_model(
        base_url="https://ollama.example.test/v1",
        model="qwen3.5:9b",
        timeout=2.0,
    )

    assert calls == [{"url": "https://ollama.example.test/api/tags", "timeout": 2.0}]
    assert result == {
        "provider": "ollama",
        "base_url": "https://ollama.example.test",
        "model": "qwen3.5:9b",
        "available_model_count": 1,
        "digest": "sha256:immutable",
        "size": 123,
        "modified_at": "2026-07-15T00:00:00Z",
    }


def test_ollama_preflight_reports_dns_failure(monkeypatch):
    def fake_getaddrinfo(host, port, type):
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr("benchmarks.craft.adapters.ollama_preflight.socket.getaddrinfo", fake_getaddrinfo)

    with pytest.raises(OllamaPreflightError, match="DNS failure"):
        preflight_ollama_model(base_url="https://bad.example.test", model="qwen3.5:9b")


def test_ollama_preflight_reports_connectivity_failure(monkeypatch):
    monkeypatch.setattr(
        "benchmarks.craft.adapters.ollama_preflight.socket.getaddrinfo",
        lambda host, port, type: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )

    def fake_get(url, *, timeout):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr("benchmarks.craft.adapters.ollama_preflight.requests.get", fake_get)

    with pytest.raises(OllamaPreflightError, match="connectivity failure"):
        preflight_ollama_model(base_url="https://ollama.example.test", model="qwen3.5:9b")


def test_ollama_preflight_preserves_timeout_classification(monkeypatch):
    monkeypatch.setattr(
        "benchmarks.craft.adapters.ollama_preflight.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, None)],
    )
    monkeypatch.setattr(
        "benchmarks.craft.adapters.ollama_preflight.requests.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.exceptions.Timeout("slow")),
    )

    with pytest.raises(OllamaPreflightTimeoutError) as exc_info:
        preflight_ollama_model(base_url="https://ollama.example.test", model="qwen3.5:9b")

    assert isinstance(exc_info.value, TimeoutError)


def test_ollama_preflight_reports_missing_model(monkeypatch):
    monkeypatch.setattr(
        "benchmarks.craft.adapters.ollama_preflight.socket.getaddrinfo",
        lambda host, port, type: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    monkeypatch.setattr(
        "benchmarks.craft.adapters.ollama_preflight.requests.get",
        lambda url, *, timeout: _FakeResponse({"models": [{"name": "gemma4:e4b"}]}),
    )

    with pytest.raises(OllamaPreflightError, match="model unavailable"):
        preflight_ollama_model(base_url="https://ollama.example.test", model="qwen3.5:9b")


def test_preflight_ollama_models_deduplicates_and_includes_ollama_urls(monkeypatch):
    calls = []

    def fake_preflight(*, base_url, model):
        calls.append((base_url, model))
        return {"base_url": base_url, "model": model}

    monkeypatch.setattr("benchmarks.craft.run.preflight_ollama_model", fake_preflight)

    results = _preflight_ollama_models({
        "models": {
            "director": {
                "provider": "openai_compatible",
                "base_url": "https://ollama.example.test/v1",
                "model": "qwen3.5:9b",
            },
            "builder": {
                "provider": "ollama_native",
                "base_url": "https://ollama.example.test/v1",
                "model": "qwen3.5:9b",
            },
        },
    })

    assert calls == [("https://ollama.example.test/v1", "qwen3.5:9b")]
    assert results == [{"base_url": "https://ollama.example.test/v1", "model": "qwen3.5:9b"}]


def test_model_identities_match_digest_by_base_url_and_model():
    config = {
        "models": {
            "director": {
                "provider": "ollama",
                "base_url": "https://director.example.test/v1",
                "model": "shared:latest",
            },
            "builder": {
                "provider": "ollama",
                "base_url": "https://builder.example.test/v1",
                "model": "shared:latest",
            },
        },
    }
    metadata = [
        {"base_url": "https://director.example.test", "model": "shared:latest", "digest": "director-digest"},
        {"base_url": "https://builder.example.test", "model": "shared:latest", "digest": "builder-digest"},
    ]

    identities = _model_identities(config, condition="villageragent_directors", metadata=metadata)

    assert identities[0]["digest"] == "director-digest"
    assert identities[1]["digest"] == "builder-digest"


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload
