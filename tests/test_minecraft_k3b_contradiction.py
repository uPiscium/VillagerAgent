import json

import pytest

from benchmarks.minecraft.k3b_contradiction import (
    K3B_ADVISORY_CONTRADICTION,
    K3B_AUTHORITY_CONTRADICTION,
    K3B_AUTHORITY_UNRELATED,
    run_k3b_condition,
    run_k3b_experiment,
)


@pytest.fixture(scope="module", params=(
    K3B_ADVISORY_CONTRADICTION,
    K3B_AUTHORITY_CONTRADICTION,
))
def contradiction_trace(request):
    return run_k3b_condition(request.param)


def test_k3b_constructs_current_unresolved_conflict_without_supersession(contradiction_trace):
    conflict = contradiction_trace["contradiction"]
    assert conflict["same_stable_proposition_key"] is True
    assert conflict["positive_polarity"] is True
    assert conflict["negative_polarity"] is False
    assert conflict["positive_current"] is True
    assert conflict["negative_current"] is True
    assert conflict["positive_supersedes"] == []
    assert conflict["negative_supersedes"] == []
    assert conflict["positive_root_type"] == "direct_observation"
    assert conflict["negative_root_type"] == "trusted_tool_result"
    assert conflict["positive_root_independently_supports_positive"] is True
    assert conflict["negative_root_independently_supports_opposite"] is True
    assert conflict["positive_visibility"] == ["Alice"]
    assert conflict["negative_visibility"] == ["Alice"]
    assert contradiction_trace["r_d"]["current_EAdm"] is False
    assert contradiction_trace["r_d"]["reasons"] == ["non_defeated.conflict"]
    assert contradiction_trace["r_d"]["validity"]["non_defeated"] is False


def test_k3b_advisory_recognizes_conflict_but_executes():
    trace = run_k3b_condition(K3B_ADVISORY_CONTRADICTION)
    assert trace["r_p"]["EAdm"] is True
    assert trace["r_e"]["current_EAdm"] is False
    assert trace["r_e"]["execution_would_block"] is True
    assert trace["r_e"]["execution_allowed"] is True
    assert trace["r_e"]["native_callable_reached"] is True
    assert trace["comparison"]["M4"] == "not_applicable"


def test_k3b_authority_rejects_conflicting_exact_action():
    trace = run_k3b_condition(K3B_AUTHORITY_CONTRADICTION)
    assert trace["r_p"]["EAdm"] is True
    assert trace["r_e"]["current_EAdm"] is False
    assert trace["r_e"]["execution_allowed"] is False
    assert trace["r_e"]["rejection_reason"] == "stale"
    assert trace["r_e"]["native_callable_reached"] is False


def test_k3b_unrelated_control_remains_valid_and_executes():
    trace = run_k3b_condition(K3B_AUTHORITY_UNRELATED)
    assert trace["contradiction"] is None
    assert trace["r_p"]["EAdm"] is True
    assert trace["r_d"]["current_EAdm"] is True
    assert trace["r_d"]["relevant_action_dependency_changed"] is False
    assert trace["r_e"]["execution_allowed"] is True
    assert trace["r_e"]["native_callable_reached"] is True


def test_k3b_exact_action_no_replanning_and_world_legality(contradiction_trace):
    assert contradiction_trace["same_prepared_object"] is True
    assert contradiction_trace["exact_action_preserved"] is True
    assert contradiction_trace["world_state_unchanged"] is True
    assert contradiction_trace["r_p"]["sequence"] < contradiction_trace["r_d"]["sequence"] < contradiction_trace["r_e"]["sequence"]
    assert contradiction_trace["r_e"]["EnvPre_oracle"] is True
    assert contradiction_trace["r_e"]["SecPre_oracle"] is True
    assert contradiction_trace["planner_freeze"]["instrumentation_scope"] == "controlled_k3b_fixture"
    for field in ("candidate_id", "attempt_id", "exact_request_digest", "action", "arguments", "target"):
        assert contradiction_trace["r_p"][field] == contradiction_trace["r_e"][field]
    for name in ("planner_calls", "llm_calls", "controller_redecisions", "action_regenerations"):
        assert contradiction_trace["planner_freeze"][name] == 0


def test_k3b_comparison_matrix_and_artifact(tmp_path):
    path = tmp_path / "k3b.json"
    result = run_k3b_experiment(artifact_path=path)
    assert result["observed_matrix"] == {
        "M0": {"contradiction": "allow", "unrelated": "allow"},
        "M1": {"contradiction": "allow", "unrelated": "allow"},
        "M2": {"contradiction": "reject", "unrelated": "reject"},
        "M3": {"contradiction": "reject", "unrelated": "allow"},
        "M4": {"contradiction": "reject", "unrelated": "allow"},
    }
    assert result["verdict"] == "K3B_PASS"
    assert result["production_runtime_modified"] is False
    for trace in result["traces"].values():
        assert trace["comparison_scope"]["M3_is_independent_mechanism"] is False
        assert trace["comparison_scope"]["prior_system_reproduction_claimed"] is False
        for model, decision in trace["comparison_decisions"].items():
            assert decision["reason"]
            assert "inputs_used" in decision
    assert json.loads(path.read_text()) == result
