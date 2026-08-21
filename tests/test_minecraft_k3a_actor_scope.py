import json

import pytest

from benchmarks.minecraft.k3a_actor_scope import ACTORS, run_k3a_case, run_k3a_selective_invalidation


@pytest.fixture(scope="module", params=ACTORS)
def k3a_case(request):
    return run_k3a_case(request.param)


def test_k3a_uses_same_actor_private_world_proposition(k3a_case):
    assert k3a_case["common_proposition"] == {
        "namespace": "minecraft",
        "predicate": "target_block_present",
        "arguments": [1, 2, 3],
        "temporal_scope": "current",
        "same_tracked_proposition": True,
    }
    alice = k3a_case["initial_evidence"]["Alice"]
    bob = k3a_case["initial_evidence"]["Bob"]
    assert alice["root_id"] != bob["root_id"]
    assert alice["visible_to"] == ["Alice"]
    assert bob["visible_to"] == ["Bob"]
    assert alice["polarity"] is True and bob["polarity"] is True


def test_k3a_revision_selectively_invalidates_only_affected_actor(k3a_case):
    affected = k3a_case["revised_actor"]
    unaffected = k3a_case["unaffected_actor"]
    affected_record = k3a_case["actors"][affected]
    unaffected_record = k3a_case["actors"][unaffected]
    revision = k3a_case["semantic_revision"]

    assert revision["affected_actor"] == affected
    assert revision["old_polarity"] is True and revision["new_polarity"] is False
    assert revision["same_stable_proposition_key"] is True
    assert revision["new_source_stream_revision"] > revision["old_source_stream_revision"]
    assert revision["supersedes"] == [revision["old_root_id"]]
    assert revision["old_root_current_after"] is False
    assert revision["new_root_current"] is True
    assert revision["visibility"] == [affected]

    assert affected_record["r_p"]["EAdm"] is True
    assert unaffected_record["r_p"]["EAdm"] is True
    assert affected_record["r_d"]["current_EAdm"] is False
    assert affected_record["r_d"]["permit_state"] == "stale"
    assert affected_record["r_d"]["support_root_current"] is False
    assert affected_record["r_d"]["evidence_visibility"] == [affected]
    assert affected_record["r_e"]["execution_allowed"] is False
    assert affected_record["r_e"]["rejection_reason"] == "stale"
    assert affected_record["r_e"]["native_callable_reached"] is False

    assert unaffected_record["r_d"]["current_EAdm"] is True
    assert unaffected_record["r_d"]["permit_state"] == "issued"
    assert unaffected_record["r_d"]["support_root_current"] is True
    assert unaffected_record["r_e"]["execution_allowed"] is True
    assert unaffected_record["r_e"]["native_callable_reached"] is True


def test_k3a_scope_isolation_has_no_cross_actor_leak(k3a_case):
    revised = k3a_case["revised_actor"]
    unaffected = k3a_case["unaffected_actor"]
    assert k3a_case["cross_actor_dependency_leak"] is False
    assert k3a_case["cross_actor_invalidation_leak"] is False
    assert k3a_case["actors"][revised]["r_d"]["intersecting_dependency_ids"]
    assert k3a_case["actors"][unaffected]["r_d"]["intersecting_dependency_ids"] == []


def test_k3a_global_overinvalidates_but_semantic_and_authority_are_selective(k3a_case):
    revised = k3a_case["revised_actor"]
    unaffected = k3a_case["unaffected_actor"]
    assert k3a_case["comparisons"]["global_revision"] == {
        revised: "reject", unaffected: "reject",
    }
    expected = {revised: "reject", unaffected: "allow"}
    assert k3a_case["comparisons"]["semantic_dependency"] == expected
    assert k3a_case["comparisons"]["existing_authority"] == expected
    assert k3a_case["comparisons"]["semantic_checker_is_new_mechanism"] is False


def test_k3a_exact_actions_planner_freeze_and_legality_hold(k3a_case):
    assert k3a_case["r_p_sequence"] < k3a_case["r_d_sequence"] < k3a_case["r_e_sequence"]
    assert k3a_case["r_p_authority_sequence"] < k3a_case["r_d_authority_sequence"]
    assert k3a_case["world_state_unchanged"] is True
    assert k3a_case["case_success"] is True
    assert k3a_case["execution_order"] == [
        k3a_case["revised_actor"], k3a_case["unaffected_actor"]]
    assert k3a_case["actors"][k3a_case["revised_actor"]]["r_e"]["submission_ordinal"] == 1
    assert k3a_case["actors"][k3a_case["unaffected_actor"]]["r_e"]["submission_ordinal"] == 2
    assert k3a_case["planner_freeze"]["instrumentation_scope"] == "controlled_k3a_fixture"
    for actor in ACTORS:
        record = k3a_case["actors"][actor]
        assert record["same_prepared_object"] is True
        assert record["exact_action_preserved"] is True
        assert record["r_p"]["candidate_id"] != k3a_case["actors"][
            next(other for other in ACTORS if other != actor)]["r_p"]["candidate_id"]
        assert record["r_p"]["attempt_id"] != k3a_case["actors"][
            next(other for other in ACTORS if other != actor)]["r_p"]["attempt_id"]
        assert record["r_e"]["EnvPre_oracle"] is True
        assert record["r_e"]["SecPre_oracle"] is True
        assert record["r_e"]["authority_sequence_before_execution"] >= k3a_case[
            "r_d_authority_sequence"]
    for name in ("planner_calls", "llm_calls", "controller_redecisions", "action_regenerations"):
        assert k3a_case["planner_freeze"][name] == 0


def test_k3a_symmetric_fixture_and_artifact(tmp_path):
    path = tmp_path / "k3a.json"
    result = run_k3a_selective_invalidation(artifact_path=path)
    assert result["verdict"] == "K3A_PASS"
    assert result["cases"]["Alice"]["revised_actor"] == "Alice"
    assert result["cases"]["Bob"]["revised_actor"] == "Bob"
    assert result["metrics"] == {
        "symmetric_case_count": 2,
        "successful_case_count": 2,
        "dependency_leak_count": 0,
        "dependency_leak_rate": 0.0,
        "invalidation_leak_count": 0,
        "invalidation_leak_rate": 0.0,
    }
    assert result["production_runtime_modified"] is False
    assert json.loads(path.read_text()) == result
