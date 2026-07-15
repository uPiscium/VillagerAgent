from __future__ import annotations

import json
import hmac
import threading
import time
import uuid
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

from benchmarks.craft.config import OLLAMA_UPSTREAM_PATHS, InvalidConfigError, validate_safe_http_endpoint


class OllamaProxyError(RuntimeError):
    """Raised when the local Ollama compatibility proxy cannot start safely."""


class OllamaOpenAIProxy(AbstractContextManager):
    """Loopback OpenAI chat facade backed by Ollama native chat with thinking disabled."""

    def __init__(
        self,
        *,
        upstream_base_url: str,
        auth_token: str,
        request_timeout_seconds: float = 300.0,
        generation_overrides: dict | None = None,
    ):
        try:
            upstream_base_url = validate_safe_http_endpoint(
                upstream_base_url,
                field="Ollama proxy upstream_base_url",
                allowed_paths=OLLAMA_UPSTREAM_PATHS,
            )
        except InvalidConfigError as exc:
            raise OllamaProxyError(str(exc)) from exc
        if request_timeout_seconds <= 0:
            raise OllamaProxyError("Ollama proxy request_timeout_seconds must be positive.")
        if not auth_token:
            raise OllamaProxyError("Ollama proxy auth_token must not be empty.")
        self.upstream_base_url = upstream_base_url.rstrip("/")
        self._auth_token = auth_token
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.generation_overrides = generation_overrides or {}
        self.request_count = 0
        self.models: set[str] = set()
        self.role_request_counts: dict[str, int] = {}
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def openai_base_url(self) -> str:
        if self._server is None:
            raise OllamaProxyError("Ollama proxy is not running.")
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    @property
    def auth_token(self) -> str:
        return self._auth_token

    def __enter__(self) -> OllamaOpenAIProxy:
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                proxy._handle_get(self)

            def do_POST(self) -> None:
                proxy._handle_post(self)

            def log_message(self, format: str, *args) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._server = None
        self._thread = None

    def metadata(self) -> dict:
        with self._lock:
            return {
                "kind": "ollama_native_openai_chat_proxy",
                "bind_host": "127.0.0.1",
                "upstream_base_url": self.upstream_base_url,
                "think": False,
                "request_timeout_seconds": self.request_timeout_seconds,
                "request_count": self.request_count,
                "models": sorted(self.models),
                "role_request_counts": dict(sorted(self.role_request_counts.items())),
                "effective_generation_settings": self.generation_overrides,
            }

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        if not self._authenticate(handler):
            return
        if handler.path.rstrip("/") == "/v1/models":
            try:
                response = requests.get(
                    self._native_url("tags"),
                    timeout=self.request_timeout_seconds,
                )
                response.raise_for_status()
                models = response.json().get("models", [])
                data = [
                    {
                        "id": item.get("name") or item.get("model"),
                        "object": "model",
                        "owned_by": "ollama",
                    }
                    for item in models
                    if isinstance(item, dict) and (item.get("name") or item.get("model"))
                ]
                self._write_json(handler, 200, {"object": "list", "data": data})
            except (requests.RequestException, ValueError) as exc:
                self._write_error(handler, 502, f"Ollama model lookup failed: {exc}")
            return
        self._write_error(handler, 404, "Unsupported proxy endpoint")

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        if not self._authenticate(handler):
            return
        if handler.path.rstrip("/") != "/v1/chat/completions":
            self._write_error(handler, 404, "Unsupported proxy endpoint")
            return
        try:
            length = int(handler.headers.get("Content-Length", "0"))
            if length <= 0 or length > 10 * 1024 * 1024:
                raise ValueError("Request body size is invalid")
            request = json.loads(handler.rfile.read(length))
            model = str(request.get("model", "")).strip()
            messages = request.get("messages")
            if not model or not isinstance(messages, list):
                raise ValueError("model and messages are required")
            native_request = {
                "model": model,
                "messages": self._native_messages(messages),
                "stream": False,
                "think": False,
            }
            role = self._request_role(messages)
            options = self._generation_options(request, self.generation_overrides.get(role, {}))
            if options:
                native_request["options"] = options
            if isinstance(request.get("tools"), list):
                native_request["tools"] = request["tools"]
            response = requests.post(
                self._native_url("chat"),
                json=native_request,
                timeout=self.request_timeout_seconds,
            )
            response.raise_for_status()
            native = response.json()
            message = native.get("message") or {}
            content = message.get("content")
            tool_calls = self._openai_tool_calls(message.get("tool_calls") or [])
            if (not isinstance(content, str) or not content.strip()) and not tool_calls:
                raise ValueError("Ollama native response did not contain visible message content")
            with self._lock:
                self.request_count += 1
                self.models.add(model)
                self.role_request_counts[role] = self.role_request_counts.get(role, 0) + 1
            openai_message = {"role": "assistant", "content": content or None}
            if tool_calls:
                openai_message["tool_calls"] = tool_calls
            payload = {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": native.get("model") or model,
                "choices": [{
                    "index": 0,
                    "message": openai_message,
                    "finish_reason": "tool_calls" if tool_calls else native.get("done_reason") or "stop",
                }],
                "usage": {
                    "prompt_tokens": int(native.get("prompt_eval_count", 0) or 0),
                    "completion_tokens": int(native.get("eval_count", 0) or 0),
                    "total_tokens": int(native.get("prompt_eval_count", 0) or 0)
                    + int(native.get("eval_count", 0) or 0),
                },
            }
            self._write_json(handler, 200, payload)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self._write_error(handler, 400, str(exc))
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 502
            self._write_error(handler, 502, f"Ollama native HTTP {status}")
        except requests.RequestException as exc:
            self._write_error(handler, 502, f"Ollama native request failed: {exc}")

    @staticmethod
    def _generation_options(request: dict, overrides: dict) -> dict:
        mappings = {
            "temperature": "temperature",
            "top_p": "top_p",
            "max_tokens": "num_predict",
            "seed": "seed",
            "stop": "stop",
            "frequency_penalty": "frequency_penalty",
            "presence_penalty": "presence_penalty",
        }
        options = {
            native_key: request[openai_key]
            for openai_key, native_key in mappings.items()
            if request.get(openai_key) is not None
        }
        for openai_key, value in overrides.items():
            native_key = mappings.get(openai_key)
            if native_key and value is not None:
                options[native_key] = value
        return options

    @staticmethod
    def _request_role(messages: list[dict]) -> str:
        system = " ".join(
            str(message.get("content") or "")
            for message in messages
            if isinstance(message, dict) and message.get("role") == "system"
        ).lower()
        if "builder" in system:
            return "builder"
        if "director" in system:
            return "director"
        return "default"

    @staticmethod
    def _native_messages(messages: list[dict]) -> list[dict]:
        normalized = []
        for message in messages:
            if not isinstance(message, dict):
                normalized.append(message)
                continue
            item = dict(message)
            if isinstance(item.get("tool_calls"), list):
                tool_calls = []
                for call in item["tool_calls"]:
                    call = dict(call)
                    function = dict(call.get("function") or {})
                    arguments = function.get("arguments")
                    if isinstance(arguments, str):
                        try:
                            function["arguments"] = json.loads(arguments)
                        except json.JSONDecodeError:
                            pass
                    call["function"] = function
                    tool_calls.append(call)
                item["tool_calls"] = tool_calls
            normalized.append(item)
        return normalized

    @staticmethod
    def _openai_tool_calls(tool_calls: list[dict]) -> list[dict]:
        normalized = []
        for call in tool_calls:
            function = dict((call or {}).get("function") or {})
            arguments = function.get("arguments", {})
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, separators=(",", ":"), sort_keys=True)
            normalized.append({
                "id": (call or {}).get("id") or f"call_{uuid.uuid4().hex}",
                "type": "function",
                "function": {
                    "name": function.get("name", ""),
                    "arguments": arguments,
                },
            })
        return normalized

    def _authenticate(self, handler: BaseHTTPRequestHandler) -> bool:
        supplied = handler.headers.get("Authorization", "")
        expected = f"Bearer {self._auth_token}"
        if not hmac.compare_digest(supplied, expected):
            self._write_error(handler, 401, "Unauthorized")
            return False
        return True

    def _native_url(self, endpoint: str) -> str:
        prefix = self.upstream_base_url if self.upstream_base_url.endswith("/api") else f"{self.upstream_base_url}/api"
        return f"{prefix}/{endpoint}"

    @staticmethod
    def _write_error(handler: BaseHTTPRequestHandler, status: int, message: str) -> None:
        OllamaOpenAIProxy._write_json(
            handler,
            status,
            {"error": {"message": message, "type": "ollama_proxy_error"}},
        )

    @staticmethod
    def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
