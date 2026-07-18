import json
from pathlib import Path

from benchmarks.minecraft.events import build_normalized_events
from benchmarks.minecraft.experiment import run_minecraft_experiment
from pipeline.runtime_events import JsonlRuntimeEventRecorder


def test_normalized_events_merge_task_action_observation_and_claim_semantics(tmp_path: Path) -> None:
    journal = tmp_path / "runtime.jsonl"
    recorder = JsonlRuntimeEventRecorder(journal, run_id="run", durable=False)
    recorder.emit("task_graph_snapshot", entity_id="runtime:task:1", source="TaskManager", payload={"graph": {"nodes": []}})
    recorder.emit("task_status_changed", entity_id="runtime:task:1", source="TaskManager", payload={"status": "running", "password": "hidden"})
    with journal.open("a", encoding="utf-8") as stream:
        stream.write('{"incomplete"')
    artifact = {
        "nodes": [
            {"node_id": "minecraft:observation:Alice:0", "node_type": "minecraft_observation", "content": {"result": "oak"}},
            {"node_id": "minecraft:claim:Alice:0", "node_type": "minecraft_claim", "content": {"message": "oak found"}},
            {"node_id": "minecraft:claim:Alice:0", "node_type": "minecraft_claim", "content": {"message": "duplicate"}},
        ],
        "edges": [
            {"source_id": "minecraft:action:Alice:0", "target_id": "minecraft:observation:Alice:0"},
            {"source_id": "minecraft:action:Alice:0", "target_id": "minecraft:claim:Alice:0"},
        ],
    }

    result = build_normalized_events(
        run_id="run",
        runtime_journal=journal,
        action_log={"Alice": [{"action": "MineBlock", "start_time": "2026-07-18T10:00:00Z", "kwargs": {"api_key": "hidden"}, "result": {"status": True}}]},
        analysis_artifact=artifact,
    )

    types = [event["event_type"] for event in result.events]
    assert types == ["action_recorded", "task_graph_snapshot", "task_status_changed", "observation_recorded", "claim_recorded"]
    assert "action_started" not in types and "action_completed" not in types
    assert [event["seq"] for event in result.events] == list(range(1, 6))
    assert result.events[2]["provenance"]["runtime_seq"] == 2
    assert "hidden" not in json.dumps(result.events)
    assert result.warnings == ("incomplete final event line ignored",)


def test_experiment_writes_optional_public_events_and_metadata(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    output = tmp_path / "result"
    journal = output / "run" / ".runtime" / "runtime_events.jsonl"
    sink = JsonlRuntimeEventRecorder(journal, run_id="run", durable=False)

    summary = run_minecraft_experiment(config_path=config, output_root=output, run_name="run", event_sink=sink)

    events = [json.loads(line) for line in (output / "run" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert summary["events_available"] is True
    assert summary["event_count"] == len(events) == 2
    assert [event["event_type"] for event in events] == ["run_started", "run_completed"]
    assert [event["provenance"]["runtime_seq"] for event in events] == [1, 2]


def test_event_producer_failure_does_not_block_existing_artifacts(tmp_path: Path, monkeypatch) -> None:
    config = write_config(tmp_path)
    monkeypatch.setattr("benchmarks.minecraft.experiment.build_normalized_events", lambda **kwargs: (_ for _ in ()).throw(OSError("disk")))

    summary = run_minecraft_experiment(config_path=config, output_root=tmp_path / "result", run_name="failure")

    run = tmp_path / "result" / "failure"
    assert summary["events_available"] is False
    assert summary["event_artifact_error"] == "OSError"
    assert (run / "summary.json").exists()
    assert (run / "runtime_dual_dag_snapshot.json").exists()
    assert not (run / "events.jsonl").exists()


def write_config(root: Path) -> Path:
    path = root / "config.json"
    path.write_text(json.dumps({
        "task_type": "meta",
        "task_idx": 0,
        "agent_num": 1,
        "task_goal": "Find the bell",
        "host": "127.0.0.1",
        "port": 25565,
        "task_name": "events",
    }), encoding="utf-8")
    return path
