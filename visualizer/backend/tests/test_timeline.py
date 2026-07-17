import json
from pathlib import Path

from fastapi.testclient import TestClient

from villageragent_visualizer import TimelineService, create_app


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _make_run(root: Path, run_id: str = "timeline") -> Path:
    run_dir = root / run_id
    _write_json(run_dir / "summary.json", {"run_name": run_dir.name, "error": None})
    return run_dir


def _analysis_artifact() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "task_state_source": "real_runtime",
        "summary": {},
        "schema": {},
        "mapping": {},
        "nodes": [
            {"node_id": "minecraft:task:task-1", "node_type": "minecraft_task", "content": {}, "provenance": {}},
            {"node_id": "minecraft:action:Bob:0", "node_type": "minecraft_action", "content": {}, "provenance": {}},
            {"node_id": "minecraft:observation:Bob:0", "node_type": "minecraft_observation", "content": {}, "provenance": {}},
            {"node_id": "minecraft:claim:Bob:0", "node_type": "minecraft_claim", "content": {}, "provenance": {}},
        ],
        "edges": [
            {
                "source_id": "minecraft:task:task-1",
                "target_id": "minecraft:action:Bob:0",
                "edge_type": "task_invokes_action",
                "metadata": {},
            },
            {
                "source_id": "minecraft:action:Bob:0",
                "target_id": "minecraft:observation:Bob:0",
                "edge_type": "produces_observation",
                "metadata": {},
            },
            {
                "source_id": "minecraft:action:Bob:0",
                "target_id": "minecraft:claim:Bob:0",
                "edge_type": "reports_claim",
                "metadata": {},
            },
        ],
    }


def test_timeline_preserves_agent_record_order_timing_and_relations(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path, "group/run-a")
    _write_json(run_dir / "action_log.json", {
        "Bob": [
            {
                "action": "openContainer",
                "start_time": "2026-07-17 10:00:00",
                "end_time": "2026-07-17 10:00:02",
                "kwargs": {"item": "chest", "api_key": "hidden"},
                "result": {"status": True},
            },
            {
                "action": "navigateTo",
                "duration": 3.5,
                "result": {"status": False},
            },
            {
                "action": "MineBlock",
                "start_time": "not-a-time",
                "duration": -1,
                "result": {},
            },
            "malformed record",
            {
                "action": "scan",
                "duration": 0,
            },
        ],
        "Alice": [{"action": "chat"}],
    })
    _write_json(run_dir / "dual_dag_artifact.json", _analysis_artifact())
    client = TestClient(create_app(result_root=tmp_path))

    response = client.get("/api/v1/runs/group/run-a/timeline")

    assert response.status_code == 200
    timeline = response.json()
    assert [lane["agent"] for lane in timeline["lanes"]] == ["Bob", "Alice"]
    bob = timeline["lanes"][0]["items"]
    assert [item["record_index"] for item in bob] == [0, 1, 2, 4]
    assert [item["action_id"] for item in bob] == [
        "minecraft:action:Bob:0",
        "minecraft:action:Bob:1",
        "minecraft:action:Bob:2",
        "minecraft:action:Bob:4",
    ]
    assert bob[0]["timing"] == "exact"
    assert bob[0]["start_time"] == "2026-07-17 10:00:00"
    assert bob[0]["end_time"] == "2026-07-17 10:00:02"
    assert bob[0]["duration_seconds"] == 2.0
    assert bob[0]["status"] == "success"
    assert bob[0]["arguments"] == {"item": "chest"}
    assert bob[0]["related_task_ids"] == ["minecraft:task:task-1"]
    assert bob[0]["observation_ids"] == ["minecraft:observation:Bob:0"]
    assert bob[0]["claim_ids"] == ["minecraft:claim:Bob:0"]
    assert bob[1]["timing"] == "duration_only"
    assert bob[1]["duration_seconds"] == 3.5
    assert bob[1]["status"] == "failure"
    assert bob[2]["timing"] == "untimed"
    assert bob[2]["duration_seconds"] is None
    assert bob[2]["status"] == "unknown"
    assert bob[3]["timing"] == "duration_only"
    assert bob[3]["duration_seconds"] == 0.0
    warning_codes = {warning["code"] for warning in timeline["warnings"]}
    assert {"invalid_duration", "invalid_timestamp", "invalid_action_record"} <= warning_codes


def test_timeline_bounds_preserve_naive_timezone_semantics(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path)
    _write_json(run_dir / "action_log.json", {
        "Alice": [
            {
                "action": "first",
                "start_time": "2026-07-17 09:00:00",
                "end_time": "2026-07-17 09:00:05",
            },
            {
                "action": "second",
                "start_time": "2026-07-17 10:00:00",
                "end_time": "2026-07-17 10:00:02",
            },
        ],
    })
    app = create_app(result_root=tmp_path)

    result = app.state.timelines.load("timeline")

    assert result.timeline is not None
    assert result.timeline.bounds is not None
    assert result.timeline.bounds.start_time == "2026-07-17 09:00:00"
    assert result.timeline.bounds.end_time == "2026-07-17 10:00:02"
    assert result.timeline.bounds.timezone_kind == "naive_local"
    assert not result.timeline.bounds.start_time.endswith("Z")


def test_timeline_omits_global_bounds_for_mixed_timezone_awareness(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path)
    _write_json(run_dir / "action_log.json", {
        "Alice": [
            {
                "action": "local",
                "start_time": "2026-07-17 09:00:00",
                "end_time": "2026-07-17 09:00:05",
            },
            {
                "action": "aware",
                "start_time": "2026-07-17T10:00:00+09:00",
                "end_time": "2026-07-17T10:00:02+09:00",
            },
        ],
    })
    app = create_app(result_root=tmp_path)

    result = app.state.timelines.load("timeline")

    assert result.timeline is not None
    assert result.timeline.bounds is None
    assert any(warning.code == "mixed_timezone_bounds" for warning in result.timeline.warnings)
    assert all(item.timing.value == "exact" for item in result.timeline.lanes[0].items)


def test_timeline_uses_duration_when_timestamps_are_incomplete(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path)
    _write_json(run_dir / "action_log.json", {
        "Alice": [{
            "action": "move",
            "start_time": "2026-07-17 09:00:00",
            "end_time": "broken",
            "duration": 1.25,
        }],
    })
    app = create_app(result_root=tmp_path)

    result = app.state.timelines.load("timeline")

    assert result.timeline is not None
    item = result.timeline.lanes[0].items[0]
    assert item.timing.value == "duration_only"
    assert item.start_time is None
    assert item.end_time is None
    assert item.duration_seconds == 1.25
    assert any(warning.code == "incomplete_timestamp" for warning in result.timeline.warnings)


def test_timeline_survives_missing_analysis_graph_without_inventing_relations(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path)
    _write_json(run_dir / "action_log.json", {"Alice": [{"action": "look"}]})
    app = create_app(result_root=tmp_path)

    result = app.state.timelines.load("timeline")

    assert result.timeline is not None
    item = result.timeline.lanes[0].items[0]
    assert item.related_task_ids == ()
    assert item.observation_ids == ()
    assert item.claim_ids == ()
    assert any(warning.code == "analysis_relations_unavailable" for warning in result.timeline.warnings)


def test_timeline_api_reports_missing_and_invalid_action_logs(tmp_path: Path) -> None:
    _make_run(tmp_path, "missing")
    invalid_dir = _make_run(tmp_path, "invalid")
    (invalid_dir / "action_log.json").write_text("{broken", encoding="utf-8")
    client = TestClient(create_app(result_root=tmp_path))

    missing = client.get("/api/v1/runs/missing/timeline")
    invalid = client.get("/api/v1/runs/invalid/timeline")

    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "action_log_missing"
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "action_log_invalid"
