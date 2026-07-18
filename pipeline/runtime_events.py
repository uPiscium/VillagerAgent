from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from benchmarks.common.sanitization import sanitize_artifact_value


RUNTIME_EVENT_SCHEMA_VERSION = "1.0.0"
RUNTIME_EVENT_TYPES = frozenset({
    "run_started",
    "run_completed",
    "run_failed",
    "run_timed_out",
    "task_graph_snapshot",
    "task_candidates_ranked",
    "task_selected",
    "task_assigned",
    "task_status_changed",
})


class RuntimeEventSink(Protocol):
    def emit(
        self,
        event_type: str,
        *,
        entity_id: str | None = None,
        source: str,
        occurred_at: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None: ...


class NoOpRuntimeEventSink:
    def emit(
        self,
        event_type: str,
        *,
        entity_id: str | None = None,
        source: str,
        occurred_at: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        return None


class InMemoryRuntimeEventRecorder:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.events: list[dict[str, Any]] = []
        self._sequence = 0
        self._lock = threading.Lock()

    def emit(
        self,
        event_type: str,
        *,
        entity_id: str | None = None,
        source: str,
        occurred_at: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            event = build_runtime_event(
                run_id=self.run_id,
                seq=self._sequence,
                event_type=event_type,
                entity_id=entity_id,
                source=source,
                occurred_at=occurred_at,
                payload=payload,
            )
            self.events.append(event)
            return event


class JsonlRuntimeEventRecorder:
    def __init__(self, path: str | Path, *, run_id: str, durable: bool = True) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.durable = durable
        self.last_error: str | None = None
        self._sequence = _last_sequence(self.path)
        self._lock = threading.Lock()

    def emit(
        self,
        event_type: str,
        *,
        entity_id: str | None = None,
        source: str,
        occurred_at: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            self._sequence += 1
            event = build_runtime_event(
                run_id=self.run_id,
                seq=self._sequence,
                event_type=event_type,
                entity_id=entity_id,
                source=source,
                occurred_at=occurred_at,
                payload=payload,
            )
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n")
                    stream.flush()
                    if self.durable:
                        os.fsync(stream.fileno())
            except (OSError, ValueError) as error:
                self.last_error = type(error).__name__
                return None
            self.last_error = None
            return event


@dataclass(frozen=True, slots=True)
class RuntimeEventReadResult:
    events: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...] = ()


def read_runtime_events(path: str | Path) -> RuntimeEventReadResult:
    event_path = Path(path)
    try:
        content = event_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return RuntimeEventReadResult(events=(), warnings=("event journal is missing",))
    except (OSError, UnicodeDecodeError):
        return RuntimeEventReadResult(events=(), warnings=("event journal is unreadable",))
    events: list[dict[str, Any]] = []
    warnings: list[str] = []
    lines = content.splitlines()
    incomplete_last_line = bool(content) and not content.endswith("\n")
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            if incomplete_last_line and index == len(lines) - 1:
                warnings.append("incomplete final event line ignored")
            else:
                warnings.append(f"malformed event line {index + 1} ignored")
            continue
        if isinstance(event, dict):
            events.append(event)
        else:
            warnings.append(f"non-object event line {index + 1} ignored")
    return RuntimeEventReadResult(events=tuple(events), warnings=tuple(warnings))


def safe_emit_runtime_event(
    sink: RuntimeEventSink,
    event_type: str,
    *,
    entity_id: str | None = None,
    source: str,
    occurred_at: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    try:
        return sink.emit(
            event_type,
            entity_id=entity_id,
            source=source,
            occurred_at=occurred_at,
            payload=payload,
        )
    except Exception:
        return None


def build_runtime_event(
    *,
    run_id: str,
    seq: int,
    event_type: str,
    entity_id: str | None,
    source: str,
    occurred_at: str | None,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    emitted_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": RUNTIME_EVENT_SCHEMA_VERSION,
        "run_id": run_id,
        "seq": seq,
        "event_id": f"{run_id}:{seq}",
        "event_type": event_type,
        "emitted_at": emitted_at,
        "occurred_at": occurred_at,
        "entity_id": entity_id,
        "source": source,
        "payload": sanitize_artifact_value(payload or {}),
    }


def _last_sequence(path: Path) -> int:
    result = read_runtime_events(path)
    return max((event.get("seq", 0) for event in result.events if isinstance(event.get("seq"), int)), default=0)
