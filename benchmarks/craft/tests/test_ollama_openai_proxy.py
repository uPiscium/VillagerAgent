import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from benchmarks.craft.ollama_openai_proxy import OllamaOpenAIProxy, OllamaProxyError


class _NativeOllamaHandler(BaseHTTPRequestHandler):
    requests = []
    response_message = {"role": "assistant", "content": "VISIBLE RESPONSE"}

    def do_GET(self):
        assert self.path == "/api/tags"
        self._write(200, {"models": [{"name": "gemma4:12b"}]})

    def do_POST(self):
        assert self.path == "/api/chat"
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        self.requests.append(payload)
        self._write(200, {
            "model": payload["model"],
            "message": self.response_message,
            "done_reason": "stop",
            "prompt_eval_count": 11,
            "eval_count": 3,
        })

    def log_message(self, format, *args):
        return

    def _write(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def native_ollama():
    _NativeOllamaHandler.requests = []
    _NativeOllamaHandler.response_message = {"role": "assistant", "content": "VISIBLE RESPONSE"}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _NativeOllamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_proxy_translates_openai_chat_to_visible_native_content(native_ollama):
    with OllamaOpenAIProxy(
        upstream_base_url=native_ollama,
        auth_token="test-token",
        request_timeout_seconds=2,
        generation_overrides={"director": {"temperature": 0.4, "max_tokens": 456}},
    ) as proxy:
        response = requests.post(
            f"{proxy.openai_base_url}/chat/completions",
            headers={"Authorization": "Bearer test-token"},
            json={
                "model": "gemma4:12b",
                "messages": [
                    {"role": "system", "content": "You are a Director."},
                    {"role": "user", "content": "hello"},
                ],
                "temperature": 0.2,
                "top_p": 0.8,
                "max_tokens": 123,
                "seed": 7,
                "stop": ["END"],
            },
            timeout=2,
        )
        response.raise_for_status()
        payload = response.json()

        assert payload["choices"][0]["message"] == {
            "role": "assistant",
            "content": "VISIBLE RESPONSE",
        }
        assert payload["usage"] == {
            "prompt_tokens": 11,
            "completion_tokens": 3,
            "total_tokens": 14,
        }
        forwarded = _NativeOllamaHandler.requests[0]
        assert forwarded["think"] is False
        assert forwarded["stream"] is False
        assert forwarded["options"] == {
            "temperature": 0.4,
            "top_p": 0.8,
            "num_predict": 456,
            "seed": 7,
            "stop": ["END"],
        }
        assert proxy.metadata()["request_count"] == 1
        assert proxy.metadata()["models"] == ["gemma4:12b"]
        assert proxy.metadata()["role_request_counts"] == {"director": 1}
        assert proxy.metadata()["effective_generation_settings"] == {
            "director": {"temperature": 0.4, "max_tokens": 456},
        }


def test_proxy_supports_openai_models_endpoint(native_ollama):
    with OllamaOpenAIProxy(upstream_base_url=native_ollama, auth_token="test-token") as proxy:
        response = requests.get(
            f"{proxy.openai_base_url}/models",
            headers={"Authorization": "Bearer test-token"},
            timeout=2,
        )

    assert response.json()["data"] == [{
        "id": "gemma4:12b",
        "object": "model",
        "owned_by": "ollama",
    }]


@pytest.mark.parametrize("unsafe_url", [
    "http://user:secret@localhost:11434",
    "http://localhost:11434?token=secret",
    "http://localhost:11434/bearer-secret",
    "http://localhost:11434#secret",
])
def test_proxy_rejects_upstream_url_credentials(unsafe_url):
    with pytest.raises(OllamaProxyError, match="must not contain credentials"):
        OllamaOpenAIProxy(
            upstream_base_url=unsafe_url,
            auth_token="test-token",
        )


def test_proxy_rejects_empty_native_content(native_ollama, monkeypatch):
    class EmptyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"model": "gemma4:12b", "message": {"content": ""}}

    monkeypatch.setattr("benchmarks.craft.ollama_openai_proxy.requests.post", lambda *args, **kwargs: EmptyResponse())
    with OllamaOpenAIProxy(upstream_base_url=native_ollama, auth_token="test-token") as proxy:
        response = requests.sessions.Session().post(
            f"{proxy.openai_base_url}/chat/completions",
            headers={"Authorization": "Bearer test-token"},
            json={"model": "gemma4:12b", "messages": []},
            timeout=2,
        )

    assert response.status_code == 400
    assert "visible message content" in response.json()["error"]["message"]


def test_proxy_requires_per_run_bearer_token(native_ollama):
    with OllamaOpenAIProxy(upstream_base_url=native_ollama, auth_token="random-run-token") as proxy:
        missing = requests.post(
            f"{proxy.openai_base_url}/chat/completions",
            json={"model": "gemma4:12b", "messages": []},
            timeout=2,
        )
        wrong = requests.get(
            f"{proxy.openai_base_url}/models",
            headers={"Authorization": "Bearer wrong"},
            timeout=2,
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_proxy_normalizes_contentless_native_tool_calls(native_ollama):
    _NativeOllamaHandler.response_message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "function": {
                "name": "simulate_move",
                "arguments": {"move": {"action": "place", "layer": 0}},
            },
        }],
    }
    with OllamaOpenAIProxy(upstream_base_url=native_ollama, auth_token="test-token") as proxy:
        response = requests.post(
            f"{proxy.openai_base_url}/chat/completions",
            headers={"Authorization": "Bearer test-token"},
            json={
                "model": "gemma4:12b",
                "messages": [
                    {"role": "system", "content": "You are a Builder."},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call_previous",
                            "type": "function",
                            "function": {
                                "name": "simulate_move",
                                "arguments": '{"move":{"action":"place"}}',
                            },
                        }],
                    },
                ],
                "tools": [{"type": "function", "function": {"name": "simulate_move"}}],
            },
            timeout=2,
        )
        response.raise_for_status()

    choice = response.json()["choices"][0]
    call = choice["message"]["tool_calls"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] is None
    assert call["id"].startswith("call_")
    assert call["type"] == "function"
    assert json.loads(call["function"]["arguments"])["move"]["action"] == "place"
    forwarded_call = _NativeOllamaHandler.requests[0]["messages"][1]["tool_calls"][0]
    assert forwarded_call["function"]["arguments"] == {"move": {"action": "place"}}
