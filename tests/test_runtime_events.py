import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pipeline.runtime_events import (
    InMemoryRuntimeEventRecorder,
    JsonlRuntimeEventRecorder,
    NoOpRuntimeEventSink,
    RUNTIME_EVENT_TYPES,
    read_runtime_events,
    safe_emit_runtime_event,
)


def test_jsonl_recorder_is_thread_safe_monotonic_and_sanitized(tmp_path: Path) -> None:
    path = tmp_path / "runtime_events.jsonl"
    recorder = JsonlRuntimeEventRecorder(path, run_id="run-1", durable=False)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda index: recorder.emit(
            "task_status_changed",
            entity_id=f"task-{index}",
            source="test",
            payload={"status": "running", "api_key": "secret-value", "nested": {"token": "private"}},
        ), range(100)))

    lines = path.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    assert len(events) == 100
    assert sorted(event["seq"] for event in events) == list(range(1, 101))
    assert len({event["event_id"] for event in events}) == 100
    assert "secret-value" not in path.read_text(encoding="utf-8")
    assert "private" not in path.read_text(encoding="utf-8")


def test_recorder_resumes_sequence_and_reader_ignores_incomplete_final_line(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    first = JsonlRuntimeEventRecorder(path, run_id="run", durable=False)
    assert first.emit("run_started", source="test")["seq"] == 1
    second = JsonlRuntimeEventRecorder(path, run_id="run", durable=False)
    assert second.emit("run_completed", source="test")["seq"] == 2
    with path.open("a", encoding="utf-8") as stream:
        stream.write('{"seq":3')

    result = read_runtime_events(path)

    assert [event["seq"] for event in result.events] == [1, 2]
    assert result.warnings == ("incomplete final event line ignored",)


def test_existing_recorders_refresh_sequence_after_another_process_writes(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    parent = JsonlRuntimeEventRecorder(path, run_id="run", durable=False)
    assert parent.emit("run_started", source="parent")["seq"] == 1
    child = JsonlRuntimeEventRecorder(path, run_id="run", durable=False)
    assert child.emit("task_selected", source="child")["seq"] == 2

    assert parent.emit("run_completed", source="parent")["seq"] == 3


def test_noop_and_failing_sink_never_change_caller_control_flow() -> None:
    assert safe_emit_runtime_event(NoOpRuntimeEventSink(), "run_started", source="test") is None

    class FailingSink:
        def emit(self, *args, **kwargs):
            raise RuntimeError("disk failed")

    assert safe_emit_runtime_event(FailingSink(), "run_failed", source="test") is None


def test_in_memory_recorder_uses_shared_schema_and_registry() -> None:
    recorder = InMemoryRuntimeEventRecorder("run")
    event = recorder.emit("task_selected", entity_id="task-1", source="controller", occurred_at=None, payload={"rank": 1})

    assert event["schema_version"] == "1.0.0"
    assert event["event_id"] == "run:1"
    assert event["occurred_at"] is None
    assert event["payload"] == {"rank": 1}
    assert {"run_started", "run_completed", "run_failed", "run_timed_out", "task_graph_snapshot", "task_candidates_ranked", "task_selected", "task_assigned", "task_status_changed"} == RUNTIME_EVENT_TYPES
