import json
from pathlib import Path

import pytest

from benchmarks.common.actions import ActionSpec
from benchmarks.common.adapter import BenchmarkAdapter, BenchmarkEvaluatorAccess
from benchmarks.common.decision import BudgetState, DecisionContext
from benchmarks.craft.common_bridge import decision_context_from_runtime
from benchmarks.craft.craft_protocol import CraftPrivateView, CraftPublicState
from benchmarks.craft.dual_dag.runtime import DualDAGRuntime


FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "craft_regression"
    / "dual_dag_smoke_fixture.json"
)


def test_craft_runtime_projects_agent_facing_decision_context():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    runtime = _runtime_from_fixture(fixture)

    context = decision_context_from_runtime(
        runtime=runtime,
        agent_id="D1",
        episode_id="craft-regression-0",
        step=fixture["initial_public_state"]["turn_index"],
        legal_actions=(ActionSpec(
            action_id="action:1:0",
            action_type="place_block",
            parameters={"block": "ys", "position": "(0,0)", "layer": 0},
        ),),
        remaining_budget=BudgetState(remaining_steps=1, remaining_information_actions=0),
    )

    context.validate_agent_facing()
    visible_node_ids = {node["node_id"] for node in context.visible_epistemic_nodes}
    assert context.benchmark == "CRAFT"
    assert context.actor_id == "D1"
    assert "observed:D1:1:row_0:0" in visible_node_ids
    assert "observed:D2:1:row_2:2" not in visible_node_ids
    assert "claim:D1:1" in visible_node_ids
    assert "claim:D2:1" in visible_node_ids
    assert [candidate["node_id"] for candidate in context.visible_candidates] == ["action:1:0", "action:1:1"]
    assert context.legal_actions[0].action_id == "action:1:0"
    assert context.remaining_budget.remaining_steps == 1
    assert all(event.payload.keys() <= {"node_id", "node_type"} for event in context.recent_public_events)


def test_common_decision_context_rejects_agent_facing_forbidden_keys():
    context = DecisionContext(
        benchmark="CRAFT",
        episode_id="bad",
        step=1,
        actor_id="D1",
        visible_epistemic_nodes=({"node_id": "n1", "evaluator_snapshot": {"hidden": True}},),
        visible_candidates=(),
        legal_actions=(),
        remaining_budget=BudgetState(),
    )

    with pytest.raises(ValueError, match="evaluator_snapshot"):
        context.validate_agent_facing()


def test_agent_facing_adapter_protocol_excludes_evaluator_snapshot():
    assert not hasattr(BenchmarkAdapter, "evaluator_snapshot")
    assert hasattr(BenchmarkEvaluatorAccess, "evaluator_snapshot")


def _runtime_from_fixture(fixture: dict) -> DualDAGRuntime:
    runtime = DualDAGRuntime(director_ids=["D1", "D2", "D3"], config=fixture["config"])
    public_state = _public_state(fixture["initial_public_state"])
    runtime.update_public_state(turn_index=public_state.turn_index, public_state=public_state)
    for private_view in fixture["private_views"]:
        runtime.update_private_observation(
            director_id=private_view["director_id"],
            turn_index=public_state.turn_index,
            private_view=_private_view(private_view),
        )
    for message in fixture["director_messages"]:
        runtime.add_reported_claim(
            director_id=message["director_id"],
            turn_index=message["turn_index"],
            message=message["message"],
        )
    candidates = runtime.build_action_candidates(
        turn_index=public_state.turn_index,
        oracle_moves=fixture["oracle_moves"],
    )
    runtime.update_action_candidate_states(turn_index=public_state.turn_index)
    runtime.current_turn_decision_support(turn_index=public_state.turn_index, candidates=candidates)
    return runtime


def _private_view(row: dict) -> CraftPrivateView:
    return CraftPrivateView(
        director_id=row["director_id"],
        view_name=row["view_name"],
        raw_view=row["raw_view"],
        text_view=row["text_view"],
        structured_view=row["structured_view"],
    )


def _public_state(row: dict) -> CraftPublicState:
    return CraftPublicState(
        turn_index=row["turn_index"],
        public_messages=row["public_messages"],
        builder_actions=row["builder_actions"],
        visible_constructed_structure=row["visible_constructed_structure"],
        progress_summary=row["progress_summary"],
    )
