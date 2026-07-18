import json
from pathlib import Path

from fastapi.testclient import TestClient

from villageragent_visualizer import create_app


def write_run(root: Path, name: str, *, state: str, task: str, metrics: dict | None = None, events: list[dict] | None = None) -> None:
    run = root / name
    run.mkdir()
    attempt_status = "completed" if state == "completed" else "failed"
    (run / "attempt.json").write_text(json.dumps({"schema_version": 1, "attempt_id": name, "producer": "test", "status": attempt_status}), encoding="utf-8")
    (run / "artifact_manifest.json").write_text(json.dumps({"schema_version": 1, "attempt_id": name, "producer": "test", "status": attempt_status, "artifacts": []}), encoding="utf-8")
    if state == "completed": (run / "_COMPLETED").write_text(name + "\n", encoding="utf-8")
    (run / "summary.json").write_text(json.dumps({"attempt_id": name, "run_name": name, "task_name": task, "task_type": "build", "mode": "execute", "runtime_selection_policy": "dual-dag", "task_state_source": "real_runtime", "snapshot_source": "runtime_result", "progress": None if metrics is None else 0.5, "error": "timeout" if state == "timed_out" else "boom" if state == "failed" else None, "timed_out": state == "timed_out", "runtime_selected_task_ids": ["task-1"], "posthoc_ranked_task_order": ["task-2", "task-1"], "final_score": None}), encoding="utf-8")
    if metrics is not None: (run / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    (run / "action_log.json").write_text(json.dumps({"Alice": [{"action": "move"}], "Bob": []}), encoding="utf-8")
    if events is not None: (run / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def test_comparison_preserves_missing_values_and_separates_semantics(tmp_path: Path) -> None:
    metrics = {"task_count": 2, "completed_task_count": 1, "failed_task_count": 1, "action_count": 3, "failed_action_count": 1, "time_to_completion": 4.5, "recommendation_adopted_count": 1}
    write_run(tmp_path, "complete", state="completed", task="Shelter", metrics=metrics)
    write_run(tmp_path, "failed", state="failed", task="Village", metrics=None)
    write_run(tmp_path, "timeout", state="timed_out", task="Village", metrics=None)
    client = TestClient(create_app(result_root=tmp_path))

    response = client.get("/api/v1/compare", params=[("run", "complete"), ("run", "failed"), ("run", "timeout")])

    assert response.status_code == 200
    rows = response.json()["runs"]
    assert rows[0]["state"] == "completed" and rows[1]["state"] == "failed"
    assert rows[2]["state"] == "timed_out"
    assert rows[1]["task_count"] is None and rows[1]["progress"] is None
    assert rows[0]["runtime_selected_task_ids"] == ["task-1"]
    assert rows[0]["posthoc_ranked_task_order"] == ["task-2", "task-1"]
    assert rows[0]["agent_action_counts"] == {"Alice": 1, "Bob": 0}
    assert response.json()["semantics"] == {"missing_values": "null", "inference": "descriptive_only"}
    assert response.json()["warnings"][0]["code"] == "different_tasks"


def test_idle_time_requires_event_timestamps(tmp_path: Path) -> None:
    events = [
        {"event_type": "action_recorded", "occurred_at": "2026-07-18T10:00:00Z", "payload": {"agent": "Alice", "duration": 2}},
        {"event_type": "action_recorded", "occurred_at": "2026-07-18T10:00:05Z", "payload": {"agent": "Alice", "duration": 1}},
    ]
    write_run(tmp_path, "eventful", state="completed", task="Shelter", metrics={}, events=events)
    write_run(tmp_path, "offline", state="completed", task="Shelter", metrics={})
    rows = TestClient(create_app(result_root=tmp_path)).get("/api/v1/compare", params=[("run", "eventful"), ("run", "offline")]).json()["runs"]

    assert rows[0]["agent_idle_seconds"] == {"Alice": 3.0}
    assert rows[1]["agent_idle_seconds"] is None
