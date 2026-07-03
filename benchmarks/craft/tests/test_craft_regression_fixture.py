import csv
import json
from pathlib import Path

from benchmarks.craft.craft_protocol import CraftPrivateView, CraftPublicState
from benchmarks.craft.dual_dag.runtime import DualDAGRuntime
from benchmarks.craft.leakage_guard import LeakageGuard
from benchmarks.craft.result_converter import normalize_results


FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "craft_regression"
    / "dual_dag_smoke_fixture.json"
)


def test_craft_dual_dag_smoke_fixture_preserves_regression_invariants(tmp_path):
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
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
    decision_support = runtime.current_turn_decision_support(
        turn_index=public_state.turn_index,
        candidates=candidates,
    )
    updated_candidates = runtime.update_action_candidate_states(turn_index=public_state.turn_index)
    candidate_states_after_update = {
        candidate["node_id"]: candidate["state"]
        for candidate in updated_candidates
    }
    runtime.add_public_builder_action(
        turn_index=public_state.turn_index,
        action=fixture["selected_builder_action"],
    )

    expected = fixture["expected"]
    assert [candidate["node_id"] for candidate in candidates] == expected["candidate_ids"]
    assert decision_support["recommended_candidate_id"] == expected["recommended_candidate_id"]
    assert candidate_states_after_update == expected["candidate_states_after_update"]
    assert runtime.action_nodes["action:1:0"]["state"] == expected["selected_candidate_final_state"]

    snapshot = runtime.serialized_snapshot()
    node_ids = {node["node_id"] for node in snapshot["epistemic_nodes"]}
    assert set(expected["required_node_ids"]).issubset(node_ids)
    for node_id in expected["private_visibility_node_ids"]:
        assert runtime.epistemic_nodes[node_id]["provenance"]["visibility"] == "private"
    for node_id in expected["public_visibility_node_ids"]:
        assert runtime.epistemic_nodes[node_id]["provenance"]["visibility"] == "public"

    serialized_text = json.dumps(snapshot, sort_keys=True)
    for forbidden in expected["forbidden_serialized_terms"]:
        assert forbidden not in serialized_text

    guard = LeakageGuard(fixture["config"])
    leakage_report = guard.inspect_prompt(
        director_id="D1",
        prompt_messages=[{"role": "user", "content": "Use only bottom left yellow small public evidence."}],
        forbidden_payloads={
            "target_structure": "hidden target payload",
            "oracle_moves": fixture["oracle_moves"],
            "D2_raw_private_view": "hidden-back-view",
        },
    )
    assert leakage_report["passed"] is True

    raw_result = _raw_result_from_fixture(fixture, snapshot, leakage_report)
    normalize_results(
        config=fixture["config"],
        condition="villageragent_directors_dual_dag_regression",
        raw_result=raw_result,
        output_dir=tmp_path,
    )
    normalized_dir = tmp_path / "normalized"
    for artifact_name in expected["normalized_artifacts"]:
        assert (normalized_dir / artifact_name).exists()
    normalized_summary = json.loads((normalized_dir / "summary.json").read_text(encoding="utf-8"))
    metrics_rows = list(csv.DictReader((normalized_dir / "metrics.csv").open(encoding="utf-8")))
    assert normalized_summary["seed"] == fixture["config"]["run"]["seed"]
    assert metrics_rows[0]["leakage_passed"] == "True"
    assert normalized_summary["runtime"]["candidate_executed_count"] == 1


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


def _raw_result_from_fixture(fixture: dict, snapshot: dict, leakage_report: dict) -> dict:
    return {
        "structure_id": fixture["config"]["run"]["structures"][0],
        "turns": [{
            "structure_id": fixture["config"]["run"]["structures"][0],
            "turn_index": fixture["initial_public_state"]["turn_index"],
            "director_outputs": fixture["director_messages"],
            "builder_action": fixture["selected_builder_action"],
            "move_executed": True,
            "progress": 0.25,
        }],
        "dual_dag": snapshot,
        "leakage_report": {"checks": [leakage_report]},
        "leakage_passed": True,
        "final_progress": 0.25,
        "completed": False,
    }
