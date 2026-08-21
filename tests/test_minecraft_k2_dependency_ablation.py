import json

import pytest

from benchmarks.minecraft.k1_f1 import semantic_dependency_intersection
from benchmarks.minecraft.k2_dependency_ablation import (
    AdmissionOnlyInput,
    ExactRequestOnlyInput,
    ExistingAuthorityInput,
    GlobalRevisionInput,
    K2_MODELS,
    M0_ADMISSION_ONLY,
    M1_EXACT_REQUEST_ONLY,
    M2_GLOBAL_REVISION,
    M3_SEMANTIC_DEPENDENCY_SIGNAL,
    M4_EXISTING_AUTHORITY,
    SemanticDependencySignalInput,
    evaluate_m0,
    evaluate_m1,
    evaluate_m2,
    evaluate_m3,
    evaluate_m4,
    run_k2_ablation,
)

EXPECTED_MATRIX = {
    M0_ADMISSION_ONLY: {"relevant": "allow", "unrelated": "allow"},
    M1_EXACT_REQUEST_ONLY: {"relevant": "allow", "unrelated": "allow"},
    M2_GLOBAL_REVISION: {"relevant": "reject", "unrelated": "reject"},
    M3_SEMANTIC_DEPENDENCY_SIGNAL: {"relevant": "reject", "unrelated": "allow"},
    M4_EXISTING_AUTHORITY: {"relevant": "reject", "unrelated": "allow"},
}


@pytest.fixture(scope="module")
def k2_result():
    return run_k2_ablation()


def test_k2_observed_matrix_and_verdict(k2_result):
    assert k2_result["observed_matrix"] == EXPECTED_MATRIX
    assert k2_result["verdict"] == "K2_EMPIRICAL_PASS"
    assert k2_result["production_runtime_modified"] is False
    assert k2_result["prior_system_reproduction_claimed"] is False
    assert k2_result["interpretation"]["m3_is_independent_mechanism"] is False
    assert k2_result["interpretation"]["final_check_novelty_claimed"] is False


@pytest.mark.parametrize("model", K2_MODELS)
def test_k2_model_observed_decisions_match_matrix(k2_result, model):
    for case in ("relevant", "unrelated"):
        assert k2_result["decisions"][model][case]["decision"] == EXPECTED_MATRIX[model][case]
        assert k2_result["decisions"][model][case]["reason"]


def test_k2_model_information_boundaries(k2_result):
    assert k2_result["decisions"][M0_ADMISSION_ONLY]["relevant"]["inputs_used"] == ["r_p.EAdm"]
    assert k2_result["decisions"][M1_EXACT_REQUEST_ONLY]["relevant"]["inputs_used"] == [
        "r_p/r_e.candidate_id", "r_p/r_e.attempt_id", "r_p/r_e.exact_request_digest",
        "r_p/r_e.action", "r_p/r_e.arguments", "r_p/r_e.target",
    ]
    assert k2_result["decisions"][M2_GLOBAL_REVISION]["relevant"]["inputs_used"] == [
        "r_p.authority_epoch", "r_e.authority_epoch_before_execution",
    ]
    assert k2_result["decisions"][M3_SEMANTIC_DEPENDENCY_SIGNAL]["relevant"][
        "relevant_action_dependency_changed"] is True
    assert k2_result["decisions"][M3_SEMANTIC_DEPENDENCY_SIGNAL]["unrelated"][
        "relevant_action_dependency_changed"] is False
    assert k2_result["decisions"][M4_EXISTING_AUTHORITY]["relevant"]["reason"] == (
        "existing_authority_stale")


def test_k2_metrics(k2_result):
    assert k2_result["metrics"] == {
        M0_ADMISSION_ONLY: {
            "relevant_detection": False, "unrelated_retention": True,
            "two_case_correctness": False,
        },
        M1_EXACT_REQUEST_ONLY: {
            "relevant_detection": False, "unrelated_retention": True,
            "two_case_correctness": False,
        },
        M2_GLOBAL_REVISION: {
            "relevant_detection": True, "unrelated_retention": False,
            "two_case_correctness": False,
        },
        M3_SEMANTIC_DEPENDENCY_SIGNAL: {
            "relevant_detection": True, "unrelated_retention": True,
            "two_case_correctness": True,
        },
        M4_EXISTING_AUTHORITY: {
            "relevant_detection": True, "unrelated_retention": True,
            "two_case_correctness": True,
        },
    }


def test_k2_source_traces_preserve_k1_invariants(k2_result):
    for trace in k2_result["source_traces"].values():
        assert trace["same_prepared_object"] is True
        assert trace["exact_action_preserved"] is True
        assert trace["world_state_unchanged"] is True
        assert trace["r_p"]["sequence"] < trace["r_d"]["sequence"] < trace["r_e"]["sequence"]
        assert trace["r_p"]["authority_epoch"] < trace["r_e"]["authority_epoch_before_execution"]
        assert trace["r_e"]["EnvPre_oracle"] is True
        assert trace["r_e"]["SecPre_oracle"] is True
        for name in ("planner_calls", "llm_calls", "controller_redecisions", "action_regenerations"):
            assert trace["planner_freeze"][name] == 0
        for field in ("candidate_id", "attempt_id", "exact_request_digest", "action",
                      "arguments", "target"):
            assert trace["r_p"][field] == trace["r_e"][field]


def test_k2_native_confirmation_uses_existing_k1_paths(k2_result):
    evidence = k2_result["contextual_native_evidence"]
    assert evidence[M0_ADMISSION_ONLY] == {
        "checker_only": True, "not_model_execution": True,
        "advisory_relevant_native_effect_reached": True,
    }
    assert evidence[M1_EXACT_REQUEST_ONLY] == {
        "checker_only": True, "not_model_execution": True,
        "advisory_relevant_native_effect_reached": True,
    }
    assert evidence[M2_GLOBAL_REVISION] == {"checker_only": True}
    assert evidence[M3_SEMANTIC_DEPENDENCY_SIGNAL] == {"checker_only": True}
    assert evidence[M4_EXISTING_AUTHORITY] == {
        "relevant_native_effect_reached": False,
        "unrelated_native_effect_reached": True,
    }


def test_k2_comparators_enforce_minimal_typed_information_boundaries():
    assert evaluate_m0(AdmissionOnlyInput(True)).allow is True
    assert evaluate_m0(AdmissionOnlyInput(False)).allow is False

    exact = (("candidate", "c"), ("request", "digest"))
    assert evaluate_m1(ExactRequestOnlyInput(exact, exact)).allow is True
    assert evaluate_m1(ExactRequestOnlyInput(exact, (("candidate", "other"),))).allow is False

    assert evaluate_m2(GlobalRevisionInput(1, 1)).allow is True
    assert evaluate_m2(GlobalRevisionInput(1, 2)).allow is False

    assert evaluate_m3(SemanticDependencySignalInput(False)).allow is True
    assert evaluate_m3(SemanticDependencySignalInput(True)).allow is False

    assert evaluate_m4(ExistingAuthorityInput(True, None)).allow is True
    rejected = evaluate_m4(ExistingAuthorityInput(False, "stale"))
    assert rejected.allow is False and rejected.reason == "existing_authority_stale"


def test_k2_semantic_signal_uses_fixture_dependency_intersection():
    admission = {"evidence:e1", "conflict:target", "scope:Alice"}
    assert semantic_dependency_intersection(admission, {"evidence:e1", "evidence:e2"}) == (
        "evidence:e1",)
    assert semantic_dependency_intersection(admission, {"evidence:weather"}) == ()
    assert semantic_dependency_intersection(
        admission, {"evidence:weather", "scope:Alice"}) == ("scope:Alice",)


def test_k2_artifact_is_bounded_json_projection(tmp_path):
    path = tmp_path / "k2-ablation.json"
    result = run_k2_ablation(artifact_path=path)
    assert json.loads(path.read_text()) == result
    assert result["read_only_projection"] is True
    assert result["bounded"] is True
