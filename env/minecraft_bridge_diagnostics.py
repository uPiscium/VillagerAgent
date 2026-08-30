"""Bounded metadata-only diagnostics for Minecraft bridge transport lifecycle."""
from __future__ import annotations

import hashlib
import json
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

try:
    from env.runtime_paths import atomic_write_json, read_json_artifact
except ImportError:
    from runtime_paths import atomic_write_json, read_json_artifact


SCHEMA_VERSION = "minecraft-bridge-diagnostics/1"
CORRELATION_HEADER = "X-Villager-Request-ID"
MAX_EVENTS = 256
_ID = re.compile(r"^[0-9a-f]{32}$")
_SAFE = re.compile(r"[^A-Za-z0-9_.:/-]")
_INTEGER_FIELDS = frozenset({
    "started_monotonic_ns", "completed_monotonic_ns", "elapsed_ns",
    "timestamp_monotonic_ns", "pid", "process_start_ticks",
    "expected_local_port", "status_code",
})
_SIGNED_INTEGER_FIELDS = frozenset({"exit_code"})
_FLOAT_FIELDS = frozenset({
    "configured_connect_timeout_s", "configured_read_timeout_s",
})
_BOOLEAN_FIELDS = frozenset({"retry_safe", "caller_correlated"})
_STRING_FIELDS = frozenset({
    "correlation_id", "actor", "method", "route", "endpoint_identity",
    "timeout_type", "outcome_certainty", "error_class", "entrypoint",
    "connection_state", "result",
})


def new_correlation_id() -> str:
    return uuid4().hex


def valid_correlation_id(value: object) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def safe_identifier(value: object, *, limit: int = 96) -> str:
    return _SAFE.sub("_", str(value))[:limit] or "unknown"


def safe_error_class(error: BaseException | object) -> str:
    return safe_identifier(type(error).__name__)


def classify_request_exception(error: BaseException) -> str:
    import requests

    if isinstance(error, requests.ConnectTimeout):
        return "connect_timeout"
    if isinstance(error, requests.ReadTimeout):
        return "read_timeout"
    if isinstance(error, requests.Timeout):
        return "timeout"
    if isinstance(error, requests.ConnectionError):
        values = list(error.args)
        while values:
            value = values.pop()
            if isinstance(value, BaseException):
                if getattr(value, "errno", None) == 111:
                    return "connection_refused"
                values.extend(value.args)
        return "connection_error"
    if isinstance(error, requests.RequestException):
        return "other_request_error"
    return "other_error"


def stable_process_start_ticks(pid: int | None) -> int | None:
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        return int(fields[21])
    except (OSError, ValueError, IndexError):
        return None


def _clean_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        if key in _STRING_FIELDS:
            clean[key] = safe_identifier(value)
        elif key in _INTEGER_FIELDS and type(value) is int and value >= 0:
            clean[key] = value
        elif key in _SIGNED_INTEGER_FIELDS and type(value) is int:
            clean[key] = value
        elif key in _FLOAT_FIELDS and type(value) in (int, float) and value >= 0:
            clean[key] = float(value)
        elif key in _BOOLEAN_FIELDS and type(value) is bool:
            clean[key] = value
    return clean


class BoundedDiagnosticRecorder:
    """Single-process snapshot writer; failures never affect runtime behavior."""

    def __init__(self, path: str | Path, *, producer: str, actor: str | None = None,
                 max_events: int = MAX_EVENTS):
        self.path = Path(path)
        self.producer = safe_identifier(producer)
        self.actor = safe_identifier(actor) if actor else None
        self.max_events = max(1, int(max_events))
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self._truncated = False
        self.collection_error: str | None = None
        self._pending: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)
        self._closed = False
        self._writer = threading.Thread(
            target=self._write_loop,
            name=f"bridge-diagnostics-{self.producer}-{self.actor or 'all'}",
            daemon=True,
        )
        self._writer.start()

    def _write_loop(self) -> None:
        while True:
            snapshot = self._pending.get()
            try:
                if snapshot is None:
                    return
                atomic_write_json(self.path, snapshot)
            except Exception as error:
                self.collection_error = safe_error_class(error)
            finally:
                self._pending.task_done()

    def _enqueue_latest(self, snapshot: dict[str, Any]) -> None:
        try:
            self._pending.put_nowait(snapshot)
            return
        except queue.Full:
            pass
        try:
            self._pending.get_nowait()
            self._pending.task_done()
        except queue.Empty:
            pass
        try:
            self._pending.put_nowait(snapshot)
        except queue.Full:
            self.collection_error = "diagnostic_queue_full"

    def record(self, event_type: str, **fields: Any) -> bool:
        event = {
            "event_type": safe_identifier(event_type),
            "timestamp_monotonic_ns": time.monotonic_ns(),
            **_clean_fields(fields),
        }
        with self._lock:
            if self._closed:
                return False
            self._events.append(event)
            if len(self._events) > self.max_events:
                self._events = self._events[-self.max_events:]
                self._truncated = True
            snapshot = self._snapshot_unlocked()
            self._enqueue_latest(snapshot)
        return True

    def record_once(self, event_type: str, **fields: Any) -> bool:
        with self._lock:
            if self._closed:
                return False
            if any(event.get("event_type") == event_type for event in self._events):
                return True
            event = {
                "event_type": safe_identifier(event_type),
                "timestamp_monotonic_ns": time.monotonic_ns(),
                **_clean_fields(fields),
            }
            self._events.append(event)
            if len(self._events) > self.max_events:
                self._events = self._events[-self.max_events:]
                self._truncated = True
            snapshot = self._snapshot_unlocked()
            self._enqueue_latest(snapshot)
        return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "producer": self.producer,
            "actor": self.actor,
            "truncated": self._truncated,
            "events": list(self._events),
            "diagnostic_collection_error": self.collection_error,
        }

    def flush(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while self._pending.unfinished_tasks:
            if time.monotonic() >= deadline:
                self.collection_error = "diagnostic_flush_timeout"
                return False
            time.sleep(0.001)
        return True

    def close(self, timeout: float = 2.0) -> bool:
        with self._lock:
            if self._closed:
                return not self._writer.is_alive()
            self._closed = True
        flushed = self.flush(timeout)
        try:
            self._pending.put_nowait(None)
        except queue.Full:
            self.collection_error = "diagnostic_close_queue_full"
            return False
        self._writer.join(timeout=max(0.0, timeout))
        if self._writer.is_alive():
            self.collection_error = "diagnostic_close_timeout"
            return False
        return flushed


def install_fastapi_request_diagnostics(app, recorder: BoundedDiagnosticRecorder,
                                        *, actor: str) -> None:
    """Install metadata-only request lifecycle middleware on one bridge app."""
    from fastapi import Request

    @app.middleware("http")
    async def bridge_request_lifecycle(request: Request, call_next):
        supplied = request.headers.get(CORRELATION_HEADER)
        correlated = valid_correlation_id(supplied)
        correlation_id = supplied if correlated else new_correlation_id()
        started = time.monotonic_ns()
        route = request.url.path
        recorder.record_once(
            "listener_request_accepted", actor=actor, endpoint_identity=f"actor:{actor}",
        )
        recorder.record(
            "request_received", correlation_id=correlation_id, actor=actor,
            method=request.method, route=route, endpoint_identity=f"actor:{actor}",
            started_monotonic_ns=started, caller_correlated=correlated,
        )
        try:
            response = await call_next(request)
        except BaseException as error:
            completed = time.monotonic_ns()
            recorder.record(
                "request_failed", correlation_id=correlation_id, actor=actor,
                method=request.method, route=route, endpoint_identity=f"actor:{actor}",
                started_monotonic_ns=started, completed_monotonic_ns=completed,
                elapsed_ns=max(0, completed - started), error_class=safe_error_class(error),
                caller_correlated=correlated,
            )
            raise
        completed = time.monotonic_ns()
        recorder.record(
            "request_completed", correlation_id=correlation_id, actor=actor,
            method=request.method, route=route, endpoint_identity=f"actor:{actor}",
            started_monotonic_ns=started, completed_monotonic_ns=completed,
            elapsed_ns=max(0, completed - started), status_code=response.status_code,
            caller_correlated=correlated,
        )
        response.headers[CORRELATION_HEADER] = correlation_id
        return response


def read_diagnostic_snapshot(path: str | Path) -> tuple[dict[str, Any] | None, str | None]:
    result = read_json_artifact(path)
    if result.state == "absent":
        return None, None
    if result.state != "valid" or not isinstance(result.value, dict):
        return None, "invalid_diagnostic_artifact"
    value = _sanitized_snapshot(result.value)
    if value is None:
        return None, "invalid_diagnostic_schema"
    return value, None


def _sanitized_snapshot(value: object) -> dict[str, Any] | None:
    if (not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION
            or not isinstance(value.get("events"), list)):
        return None
    events = []
    for raw_event in value["events"]:
        if not isinstance(raw_event, dict) or not isinstance(raw_event.get("event_type"), str):
            continue
        events.append({
            "event_type": safe_identifier(raw_event["event_type"]),
            **_clean_fields(raw_event),
        })
    collection_error = value.get("diagnostic_collection_error")
    return {
        "schema_version": SCHEMA_VERSION,
        "producer": safe_identifier(value.get("producer", "unknown")),
        "actor": safe_identifier(value["actor"]) if value.get("actor") else None,
        "truncated": value.get("truncated") is True,
        "events": events,
        "diagnostic_collection_error": (
            safe_identifier(collection_error) if isinstance(collection_error, str) else None
        ),
    }


def artifact_projection(path: str | Path, *, runtime_root: str | Path) -> dict[str, Any]:
    target = Path(path)
    try:
        encoded = target.read_bytes()
    except FileNotFoundError:
        return {"state": "absent", "error": None}
    except OSError:
        return {"state": "invalid", "error": "diagnostic_artifact_unreadable"}
    try:
        snapshot = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"state": "invalid", "error": "invalid_diagnostic_artifact"}
    snapshot = _sanitized_snapshot(snapshot)
    if snapshot is None:
        return {"state": "invalid", "error": "invalid_diagnostic_schema"}
    try:
        relative = target.relative_to(Path(runtime_root)).as_posix()
    except ValueError:
        relative = target.name
    return {
        "state": "valid",
        "path": relative,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "event_count": len(snapshot["events"]),
        "truncated": snapshot.get("truncated") is True,
        "snapshot": snapshot,
    }
