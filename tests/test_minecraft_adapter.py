import json

from benchmarks.minecraft.adapter import MinecraftBenchmarkMetadataAdapter
from benchmarks.minecraft.experiment import run_minecraft_experiment


def test_minecraft_adapter_exposes_sanitized_metadata_and_capabilities():
    adapter = MinecraftBenchmarkMetadataAdapter(
        launch_config={
            "task_name": "adapter_smoke",
            "task_type": "meta",
            "task_idx": 1,
            "agent_num": 2,
            "task_goal": "Find the bell",
            "api_key": "secret",
            "base_url": "http://secret",
        },
        action_log={
            "Alice": [
                {
                    "action": "navigateTo",
                    "kwargs": {"player_name": "Alice", "api_key": "secret"},
                    "result": {
                        "status": True,
                        "message": "arrived",
                        "progress": 1.0,
                        "final_score": 1,
                    },
                }
            ],
            "Bob": [
                {
                    "action": "MineBlock",
                    "kwargs": {"player_name": "Bob", "block_name": "stone"},
                    "result": {"status": True, "message": "mined"},
                },
                {
                    "action": "talkTo",
                    "kwargs": {
                        "player_name": "Bob",
                        "entity_name": "Alice",
                        "message": "The bell is north.",
                    },
                    "result": {"status": True},
                },
            ],
        },
        summary={"run_name": "adapter_run", "progress": 1.0, "final_score": {"score": 1}},
        metrics={"progress": 1.0, "action_count": 3, "task_completion_rate": 1.0},
    )

    context = adapter.episode_context()
    assert context.benchmark == "minecraft"
    assert context.episode_id == "adapter_run"
    assert context.agent_ids == ("Alice", "Bob")
    assert "api_key" not in json.dumps(context.metadata)
    assert adapter.capabilities("Alice").action_types == ("navigateTo",)
    assert adapter.capabilities("Bob").can_communicate is True

    observations = adapter.get_observation("Alice")
    observation_text = json.dumps([observation.proposition for observation in observations])
    assert "navigateTo" in observation_text
    assert "The bell is north" in observation_text
    assert "MineBlock" not in observation_text
    assert "api_key" not in observation_text
    assert "final_score" not in observation_text
    assert "progress" not in observation_text


def test_minecraft_adapter_decision_context_is_agent_facing_only():
    adapter = MinecraftBenchmarkMetadataAdapter(
        launch_config={
            "task_name": "decision",
            "task_type": "meta",
            "task_goal": "Find the bell",
            "agent_num": 1,
            "api_key": "secret",
        },
        action_log={
            "Alice": [{
                "action": "read",
                "kwargs": {"player_name": "Alice", "_private": "drop"},
                "result": {"status": True, "message": "Go north", "score": 1},
            }]
        },
        summary={"artifact_summary": {"node_count": 10}, "timed_out": True},
    )

    decision_context = adapter.decision_context("Alice")

    decision_context.validate_agent_facing()
    payload = json.dumps({
        "nodes": decision_context.visible_epistemic_nodes,
        "candidates": decision_context.visible_candidates,
    })
    assert "api_key" not in payload
    assert "_private" not in payload
    assert "artifact_summary" not in payload
    assert "timed_out" not in payload
    assert "score" not in payload
    assert decision_context.legal_actions[0].action_type == "read"


def test_minecraft_adapter_loads_from_run_dir(tmp_path):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(
        json.dumps({
            "task_type": "meta",
            "task_idx": 0,
            "agent_num": 1,
            "task_goal": "Find the village bell",
            "host": "127.0.0.1",
            "port": 25565,
            "task_name": "adapter_artifacts",
            "api_key": "secret",
            "smoke_action_log": {
                "Alice": [{"action": "scanNearbyEntities", "result": {"status": True}}]
            },
        }),
        encoding="utf-8",
    )
    run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="adapter_artifacts",
    )

    adapter = MinecraftBenchmarkMetadataAdapter.from_run_dir(tmp_path / "result" / "adapter_artifacts")

    assert adapter.agent_ids() == ("Alice",)
    assert adapter.capabilities("Alice").information_action_types == ("scanNearbyEntities",)
    assert adapter.final_metrics()["action_count"] == 1
    assert "api_key" not in json.dumps(adapter.decision_context("Alice").visible_candidates)
