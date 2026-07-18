import json
from pathlib import Path

from fastapi.testclient import TestClient

from villageragent_visualizer import create_app
from villageragent_visualizer.replay import reconstruct_replay_state


def event(seq: int, event_type: str, *, entity_id: str | None = None, payload: dict | None = None) -> dict:
    return {"schema_version": "1.0.0", "run_id": "run", "seq": seq, "event_id": f"run:{seq}", "event_type": event_type, "occurred_at": None, "entity_id": entity_id, "source": "test", "payload": payload or {}}


def journal_events() -> list[dict]:
    return [
        event(1, "task_graph_snapshot", payload={"graph": {"nodes": [{"node_id": "task-1", "lifecycle": {"status": "unknown"}}], "edges": []}}),
        event(2, "task_assigned", entity_id="task-1", payload={"agents": ["Alice", "Bob"]}),
        event(3, "task_status_changed", entity_id="task-1", payload={"status": "running"}),
        event(5, "future_event", entity_id="task-1"),
        event(6, "action_recorded", entity_id="minecraft:action:Alice:0", payload={"tool": "MineBlock"}),
    ]


def write_run(root: Path, *, malformed_tail: bool = False) -> None:
    run = root / "run"
    run.mkdir()
    (run / "summary.json").write_text(json.dumps({"run_name": "run", "error": None}), encoding="utf-8")
    content = "".join(json.dumps(item) + "\n" for item in journal_events())
    if malformed_tail:
        content += '{"seq":7'
    (run / "events.jsonl").write_text(content, encoding="utf-8")


def test_reducer_reconstructs_any_sequence_and_backward_state() -> None:
    forward = reconstruct_replay_state(journal_events(), 3, max_seq=6)
    backward = reconstruct_replay_state(journal_events(), 1, max_seq=6)

    assert forward["graph"]["nodes"][0]["lifecycle"]["status"] == "running"
    assert forward["assignments"] == {"task-1": ["Alice", "Bob"]}
    assert backward["graph"]["nodes"][0]["lifecycle"]["status"] == "unknown"
    assert backward["assignments"] == {}
    assert backward["timeline"] == []


def test_reducer_warns_but_continues_for_gap_unknown_and_missing_task() -> None:
    events = journal_events() + [event(7, "task_status_changed", entity_id="missing", payload={"status": "failure"})]
    state = reconstruct_replay_state(events, 7, max_seq=7)

    codes = {warning["code"] for warning in state["warnings"]}
    assert {"sequence_gap", "unknown_event", "missing_task"} <= codes
    assert [item["entity_id"] for item in state["timeline"]] == ["minecraft:action:Alice:0"]


def test_events_api_paginates_and_replay_api_sanitizes_payloads(tmp_path: Path) -> None:
    write_run(tmp_path, malformed_tail=True)
    lines = (tmp_path / "run" / "events.jsonl").read_text(encoding="utf-8")
    (tmp_path / "run" / "events.jsonl").write_text(lines.replace('"tool": "MineBlock"', '"tool": "MineBlock", "api_key": "hidden"'), encoding="utf-8")
    client = TestClient(create_app(result_root=tmp_path))

    page = client.get("/api/v1/runs/run/events", params={"start_seq": 2, "limit": 2})
    replay = client.get("/api/v1/runs/run/replay-state", params={"seq": 6})

    assert page.status_code == 200
    assert [item["seq"] for item in page.json()["events"]] == [2, 3]
    assert page.json()["next_seq"] == 4
    assert replay.status_code == 200
    assert replay.json()["authority"] == "recorded_event_replay"
    assert replay.json()["seq"] == 6
    assert "hidden" not in replay.text
    assert any(warning["code"] == "incomplete_event" for warning in replay.json()["warnings"])


def test_missing_journal_disables_only_replay_endpoints(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "summary.json").write_text('{"run_name":"run","error":null}', encoding="utf-8")
    client = TestClient(create_app(result_root=tmp_path))

    assert client.get("/api/v1/runs/run").status_code == 200
    assert client.get("/api/v1/runs/run/events").status_code == 404
    assert client.get("/api/v1/runs/run/replay-state").status_code == 404
