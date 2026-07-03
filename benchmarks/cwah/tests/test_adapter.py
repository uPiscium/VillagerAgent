from benchmarks.common.actions import InformationActionSpec
from benchmarks.cwah.adapter import CWAHConfig, CWAHSymbolicAdapter
from benchmarks.cwah.mock_env import mock_cwah_env_factory


def test_cwah_symbolic_adapter_keeps_agent_local_observations_separate():
    adapter = CWAHSymbolicAdapter(config=CWAHConfig(), env_factory=mock_cwah_env_factory)
    episode = adapter.reset(episode_id="mock-cwah", seed=7)

    assert episode.agent_ids == ("agent_0", "agent_1")
    agent_0_records = adapter.get_observation("agent_0")
    agent_1_records = adapter.get_observation("agent_1")
    agent_0_text = repr([record.grounding for record in agent_0_records])
    agent_1_text = repr([record.grounding for record in agent_1_records])
    assert "plate" in agent_0_text
    assert "cupcake" not in agent_0_text
    assert "cupcake" in agent_1_text
    assert "plate" not in agent_1_text


def test_cwah_symbolic_adapter_exposes_message_as_public_observation():
    adapter = CWAHSymbolicAdapter(config=CWAHConfig(), env_factory=mock_cwah_env_factory)
    adapter.reset(episode_id="mock-cwah", seed=7)

    result = adapter.execute_information_action(
        "agent_0",
        InformationActionSpec(
            action_id="send_message:agent_0",
            action_type="send_message",
            parameters={"message": "I found the plate in the kitchen."},
            information_subtype="send_message",
        ),
    )

    assert result.succeeded is True
    assert result.metrics["communication_count"] == 1
    agent_1_records = adapter.get_observation("agent_1")
    assert any(record.source_kind == "agent_message" for record in agent_1_records)
    assert any("plate" in str(record.proposition) for record in agent_1_records)
    public_records = adapter.get_public_observation()
    assert public_records[0].visibility.public is True


def test_cwah_decision_context_is_agent_facing_and_candidate_backed():
    adapter = CWAHSymbolicAdapter(config=CWAHConfig(), env_factory=mock_cwah_env_factory)
    adapter.reset(episode_id="mock-cwah", seed=7)

    context = adapter.decision_context("agent_0")

    context.validate_agent_facing()
    assert context.benchmark == "cwah"
    assert context.actor_id == "agent_0"
    assert any(candidate["action_type"] == "send_message" for candidate in context.visible_candidates)
    assert any(action.action_type == "walktowards" for action in context.legal_actions)
    assert context.remaining_budget.remaining_steps == 250


def test_cwah_adapter_tracks_progress_and_final_metrics():
    adapter = CWAHSymbolicAdapter(config=CWAHConfig(), env_factory=mock_cwah_env_factory)
    adapter.reset(episode_id="mock-cwah", seed=7)

    adapter.execute_information_action(
        "agent_0",
        InformationActionSpec(
            action_id="send_message:agent_0",
            action_type="send_message",
            parameters={"message": "first step"},
            information_subtype="send_message",
        ),
    )
    adapter.execute_information_action(
        "agent_1",
        InformationActionSpec(
            action_id="send_message:agent_1",
            action_type="send_message",
            parameters={"message": "second step"},
            information_subtype="send_message",
        ),
    )

    assert adapter.is_terminal() is True
    assert adapter.task_progress() == 1.0
    assert adapter.final_metrics() == {
        "task_success": True,
        "normalized_progress": 1.0,
        "episode_steps": 2,
    }
