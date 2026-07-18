import asyncio
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from villageragent_visualizer import create_app
from villageragent_visualizer.stream import LatestEventQueue, StreamEnvelope


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _snapshot(status: str = "running", *, secret: bool = False) -> dict[str, object]:
    content: dict[str, object] = {"description": f"Task is {status}"}
    if secret:
        content["api_key"] = "must-not-stream"
    return {
        "schema_version": "1.0.0",
        "source_of_truth": "runtime_task_dag",
        "nodes": [{
            "node_id": "runtime:task:1",
            "node_type": "runtime_task",
            "content": content,
            "lifecycle": {"status": status},
            "derived": {"dependency_ready": True},
            "provenance": {},
        }],
        "edges": [],
    }


def _live_run(root: Path, run_id: str = "live", *, secret: bool = False) -> Path:
    run = root / run_id
    _write_json(run / "attempt.json", {
        "schema_version": 1,
        "attempt_id": f"attempt-{run_id}",
        "producer": "test",
        "status": "running",
    })
    _write_json(run / ".runtime" / "runtime_result.json", {
        "runtime_task_dag_snapshot": _snapshot(secret=secret),
        "client_secret": "must-not-stream",
    })
    return run


def _replace_checkpoint(run: Path, payload: object) -> None:
    temporary = run / ".runtime" / "runtime_result.json.tmp"
    _write_json(temporary, payload)
    temporary.replace(run / ".runtime" / "runtime_result.json")


def _app(root: Path):
    return create_app(result_root=root, stream_poll_interval=0.01, stream_heartbeat_interval=0.05)


def test_stream_sends_sanitized_full_snapshot_then_atomic_replacement(tmp_path: Path) -> None:
    run = _live_run(tmp_path, secret=True)
    app = _app(tmp_path)

    with TestClient(app).websocket_connect("/api/v1/runs/live/stream") as websocket:
        initial = websocket.receive_json()
        assert initial["version"] == "1.0"
        assert initial["type"] == "snapshot"
        assert initial["run_id"] == "live"
        assert initial["revision"] == 1
        assert initial["emitted_at"].endswith("Z")
        assert "must-not-stream" not in json.dumps(initial)

        _replace_checkpoint(run, {"runtime_task_dag_snapshot": _snapshot("success", secret=True)})
        updated = websocket.receive_json()
        assert updated["type"] == "snapshot"
        assert updated["revision"] > initial["revision"]
        assert updated["payload"]["nodes"][0]["lifecycle"]["status"] == "success"
        assert "must-not-stream" not in json.dumps(updated)


def test_tmp_write_is_ignored_and_client_commands_do_not_mutate_runtime(tmp_path: Path) -> None:
    run = _live_run(tmp_path)
    original = (run / ".runtime" / "runtime_result.json").read_bytes()

    with TestClient(_app(tmp_path)).websocket_connect("/api/v1/runs/live/stream") as websocket:
        assert websocket.receive_json()["type"] == "snapshot"
        _write_json(run / ".runtime" / "runtime_result.json.tmp", {"runtime_task_dag_snapshot": _snapshot("failure")})
        websocket.send_json({"command": "delete_task", "task_id": "runtime:task:1"})
        assert websocket.receive_json()["type"] == "heartbeat"

    assert (run / ".runtime" / "runtime_result.json").read_bytes() == original


def test_malformed_checkpoint_emits_error_and_later_snapshot_survives(tmp_path: Path) -> None:
    run = _live_run(tmp_path)

    with TestClient(_app(tmp_path)).websocket_connect("/api/v1/runs/live/stream") as websocket:
        assert websocket.receive_json()["type"] == "snapshot"
        temporary = run / ".runtime" / "runtime_result.json.tmp"
        temporary.write_text("{broken", encoding="utf-8")
        temporary.replace(run / ".runtime" / "runtime_result.json")
        error = websocket.receive_json()
        assert error["type"] == "error"
        assert error["payload"]["code"] == "runtime_snapshot_invalid"

        _replace_checkpoint(run, {"runtime_task_dag_snapshot": _snapshot("success")})
        assert websocket.receive_json()["type"] == "snapshot"


def test_unavailable_run_closes_cleanly_and_last_disconnect_stops_watcher(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    with client.websocket_connect("/api/v1/runs/missing/stream") as websocket:
        assert websocket.receive_json()["type"] == "run_unavailable"
    assert app.state.streams.active_watcher_count == 0

    _live_run(tmp_path)
    with client.websocket_connect("/api/v1/runs/live/stream") as websocket:
        assert websocket.receive_json()["type"] == "snapshot"
        assert app.state.streams.active_watcher_count == 1
    time.sleep(0.1)
    assert app.state.streams.active_watcher_count == 0


def test_terminal_attempt_emits_run_completed_without_runtime_control(tmp_path: Path) -> None:
    run = _live_run(tmp_path)

    with TestClient(_app(tmp_path)).websocket_connect("/api/v1/runs/live/stream") as websocket:
        assert websocket.receive_json()["type"] == "snapshot"
        _write_json(run / "summary.json", {
            "attempt_id": "attempt-live",
            "run_name": "live",
            "error": None,
            "timed_out": False,
        })
        _write_json(run / "artifact_manifest.json", {
            "schema_version": 1,
            "attempt_id": "attempt-live",
            "producer": "test",
            "status": "completed",
            "artifacts": [],
        })
        _write_json(run / "attempt.json", {
            "schema_version": 1,
            "attempt_id": "attempt-live",
            "producer": "test",
            "status": "completed",
        })
        (run / "_COMPLETED").write_text("attempt-live\n", encoding="utf-8")

        completed = websocket.receive_json()
        assert completed["type"] == "run_completed"
        assert completed["payload"] == {"state": "completed"}

def test_bounded_queue_preserves_pending_snapshot_over_heartbeat_and_coalesces_updates() -> None:
    queue = LatestEventQueue()
    first: StreamEnvelope = {"type": "snapshot", "revision": 1}
    heartbeat: StreamEnvelope = {"type": "heartbeat", "revision": 2}
    latest: StreamEnvelope = {"type": "snapshot", "revision": 3}

    queue.offer(first)
    queue.offer(heartbeat)
    assert asyncio.run(queue.get()) == first
    queue.offer(first)
    queue.offer(latest)
    assert asyncio.run(queue.get()) == latest

    completed: StreamEnvelope = {"type": "run_completed", "revision": 4}
    queue.offer(first)
    queue.offer(completed)
    assert asyncio.run(queue.get()) == first
    assert asyncio.run(queue.get()) == completed
