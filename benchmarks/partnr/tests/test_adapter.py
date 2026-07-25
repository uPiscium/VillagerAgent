import json

from benchmarks.partnr.adapter import PARTNRAdapter, PARTNRConfig
from benchmarks.partnr.fixture_env import fixture_partnr_env_factory
from benchmarks.partnr.smoke import run_fixture_smoke


def _adapter() -> PARTNRAdapter:
    adapter = PARTNRAdapter(
        config=PARTNRConfig(
            instruction="Move the apple into the basket.",
            scene_id="fixture_scene",
            max_steps=8,
        ),
        env_factory=fixture_partnr_env_factory,
    )
    adapter.reset(episode_id="fixture", seed=47668090)
    return adapter


def test_adapter_satisfies_common_protocol():
    adapter = _adapter()

    for method in (
        "reset", "agent_ids", "capabilities", "get_observation",
        "get_public_observation", "get_legal_actions", "decision_context",
        "execute_action", "execute_information_action", "is_terminal",
        "task_progress", "final_metrics",
    ):
        assert callable(getattr(adapter, method))
    assert adapter.agent_ids() == ("agent_0", "agent_1")
    assert adapter.capabilities("agent_0").can_communicate is True
    assert adapter.capabilities("agent_0").action_types == ("Navigate", "Pick", "Place")
    assert adapter.capabilities("agent_0").information_action_types == (
        "FindObjectTool", "FindAgentActionTool"
    )


def test_actor_context_excludes_evaluator_and_privileged_world_graph():
    adapter = _adapter()

    context = adapter.decision_context("agent_0")
    serialized = json.dumps({
        "facts": context.visible_epistemic_nodes,
        "candidates": context.visible_candidates,
        "events": [event.payload for event in context.recent_public_events],
    })

    assert "evaluation_propositions" not in serialized
    assert "task_explanation" not in serialized
    assert "full_world_graph" not in serialized
    assert "is_inside" not in serialized
    evaluator = adapter.evaluator_snapshot()
    assert evaluator["evaluation_propositions"][0]["function_name"] == "is_inside"


def test_local_entities_and_public_instruction_preserve_visibility():
    adapter = _adapter()

    agent_zero = adapter.get_observation("agent_0")
    agent_one = adapter.get_observation("agent_1")
    zero_entities = {
        record.proposition.get("subject")
        for record in agent_zero
        if record.source_kind == "environment_observation"
    }
    one_entities = {
        record.proposition.get("subject")
        for record in agent_one
        if record.source_kind == "environment_observation"
    }

    assert "apple_0" in zero_entities
    assert "basket_0" not in zero_entities
    assert "basket_0" in one_entities
    instruction = next(record for record in agent_zero if record.source_kind == "task_instruction")
    assert instruction.visibility.public is True
    assert instruction.visibility.visible_to == frozenset()


def test_tool_failure_feedback_and_recovery_are_recorded():
    adapter = _adapter()
    pick = next(action for action in adapter.get_legal_actions("agent_0") if action.action_type == "Pick")

    failed = adapter.execute_action("agent_0", pick)
    recovered = adapter.execute_action("agent_0", pick)
    feedback = [
        record for record in adapter.get_observation("agent_0")
        if record.source_kind == "resolved_tool_feedback"
    ]

    assert failed.succeeded is False
    assert recovered.succeeded is True
    assert feedback[0].proposition["succeeded"] is True
    assert adapter.final_metrics()["failed_action_count"] == 1
    assert adapter.final_metrics()["recovered_failure_count"] == 1
    assert adapter.final_metrics()["recovery_after_failure_rate"] == 1.0


def test_action_candidate_dag_records_tool_requirements():
    adapter = _adapter()

    snapshot = adapter.dual_dag_snapshot("agent_0")
    pick = next(
        node for node in snapshot["action_candidate_dag"]["nodes"]
        if node["action_type"] == "Pick"
    )

    assert pick["state"] == "uncertain"
    assert snapshot["action_candidate_dag"]["edges"] == [{
        "source": "partnr:fixture:0:agent_0:entity:apple_0",
        "target": "pick:agent_0:apple_0",
        "relation": "requires",
    }]
    epistemic_ids = {node["node_id"] for node in snapshot["epistemic_dag"]["nodes"]}
    assert snapshot["action_candidate_dag"]["edges"][0]["source"] in epistemic_ids


def test_dependency_free_fixture_smoke_completes_without_performance_claim():
    payload = run_fixture_smoke()

    assert payload["metrics"]["task_success"] is True
    assert payload["metrics"]["task_percent_complete"] == 1.0
    assert payload["metrics"]["failed_action_count"] == 1
    assert payload["metrics"]["recovered_failure_count"] == 1
    assert payload["performance_claim"] is False
    assert payload["evaluator_isolation"] == {
        "evaluation_propositions_agent_visible": False,
        "full_world_graph_agent_visible": False,
    }
