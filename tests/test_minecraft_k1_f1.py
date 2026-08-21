import json

import pytest

from benchmarks.minecraft.eac_runtime import MinecraftEACRuntime
from benchmarks.minecraft.k1_f1 import (
    K1_ADVISORY_RELEVANT,
    K1_AUTHORITY_RELEVANT,
    K1_AUTHORITY_UNRELATED,
    K1DeterministicSynchronizationPoint,
    K1InvariantError,
    run_k1_condition,
)


def _assert_exact_request_preserved(trace):
    for field in ("candidate_id", "attempt_id", "exact_request_digest", "action",
                  "arguments", "target"):
        assert trace["r_p"][field] == trace["r_e"][field]
    assert trace["exact_action_preserved"] is True
    assert trace["same_prepared_object"] is True
    assert trace["r_p"]["sequence"] < trace["r_d"]["sequence"] < trace["r_e"]["sequence"]
    assert trace["planner_freeze"] == {
        "instrumentation_scope": "controlled_k1_fixture",
        "execution_model": "direct_retained_prepared_action",
        "planner_instantiated": False,
        "llm_instantiated": False,
        "controller_instantiated": False,
        "planner_calls": 0,
        "llm_calls": 0,
        "controller_redecisions": 0,
        "action_regenerations": 0,
    }


def test_k1_advisory_executes_exact_action_after_relevant_supersession(tmp_path):
    path = tmp_path / "k1-advisory.json"
    trace = run_k1_condition(K1_ADVISORY_RELEVANT, artifact_path=path)

    assert trace["r_p"]["EAdm"] is True
    assert trace["r_d"]["current_EAdm"] is False
    assert trace["r_d"]["polarity"] is False
    assert trace["r_d"]["superseded_root"] in trace["r_p"]["witness_root_ids"]
    assert trace["r_e"]["current_EAdm"] is False
    assert trace["r_e"]["admission_permit_or_shadow_fresh"] is False
    assert trace["r_e"]["execution_would_block"] is True
    assert trace["r_e"]["execution_allowed"] is True
    assert trace["r_e"]["native_effect_reached"] is True
    assert trace["r_e"]["gateway_env_precheck_calls"] == 1
    assert trace["r_e"]["gateway_sec_precheck_calls"] == 1
    assert trace["native_effect_count"] == 1
    assert trace["outcome"] == "advisory_current_nonadmissible_exact_action_executed"
    assert trace["r_e"]["EnvPre_oracle"] is True
    assert trace["r_e"]["SecPre_oracle"] is True
    assert json.loads(path.read_text()) == trace
    _assert_exact_request_preserved(trace)


def test_k1_authority_rejects_exact_action_after_relevant_supersession(tmp_path):
    path = tmp_path / "k1-authority.json"
    trace = run_k1_condition(K1_AUTHORITY_RELEVANT, artifact_path=path)

    assert trace["r_p"]["EAdm"] is True
    assert trace["r_p"]["permit_or_shadow_state"] == "issued"
    assert trace["r_d"]["current_EAdm"] is False
    assert trace["r_d"]["old_root_current"] is False
    assert trace["r_d"]["new_root_current"] is True
    assert trace["r_e"]["current_EAdm"] is False
    assert trace["r_e"]["admission_permit_or_shadow_fresh"] is False
    assert trace["r_e"]["execution_allowed"] is False
    assert trace["r_e"]["rejection_reason"] == "stale"
    assert trace["r_e"]["native_effect_reached"] is False
    assert trace["r_e"]["gateway_env_precheck_calls"] == 0
    assert trace["r_e"]["gateway_sec_precheck_calls"] == 0
    assert trace["r_e"]["permit_staled_before_submission"] is True
    assert trace["r_e"]["effect_rejected_stale_audit_event"] is False
    assert trace["r_e"]["execution_rejected_by_gateway"] is True
    assert trace["native_effect_count"] == 0
    assert trace["outcome"] == "authority_stale_rejected"
    assert trace["r_e"]["EnvPre_oracle"] is True
    assert trace["r_e"]["SecPre_oracle"] is True
    assert json.loads(path.read_text()) == trace
    _assert_exact_request_preserved(trace)


def test_k1_unrelated_actor_visible_mutation_retains_authority_action(tmp_path):
    trace = run_k1_condition(
        K1_AUTHORITY_UNRELATED,
        artifact_path=tmp_path / "k1-unrelated.json",
    )

    assert trace["r_p"]["EAdm"] is True
    assert trace["r_d"]["mutation_type"] == "unrelated_actor_visible_update"
    assert trace["r_d"]["superseded_root"] is None
    assert trace["r_d"]["current_EAdm"] is True
    assert trace["r_e"]["admission_permit_or_shadow_fresh"] is True
    assert trace["r_e"]["execution_allowed"] is True
    assert trace["r_e"]["native_effect_reached"] is True
    assert trace["r_e"]["gateway_env_precheck_calls"] == 1
    assert trace["r_e"]["gateway_sec_precheck_calls"] == 1
    assert trace["native_effect_count"] == 1
    assert trace["outcome"] == "unrelated_retained"
    _assert_exact_request_preserved(trace)


def test_normal_runtime_path_has_no_k1_synchronization_point():
    calls = []
    runtime = MinecraftEACRuntime(
        mode="dual_dag_authority",
        run_id="k1-disabled",
        env_prechecks={"MineBlock": lambda unused: True},
    )
    runtime.ingest_target_observation("Alice", "MineBlock", {"x": 1, "y": 2, "z": 3})

    result = runtime.mediate_tool(
        "MineBlock",
        lambda **kwargs: calls.append(kwargs) or {"status": True},
        (),
        {"player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": ""},
    )

    assert result["status"] is True
    assert len(calls) == 1


def test_fixture_synchronization_point_rejects_reconstructed_action():
    runtime = MinecraftEACRuntime(
        mode="dual_dag_advisory",
        run_id="k1-object-identity",
        env_prechecks={"talkTo": lambda unused: True},
    )
    arguments = {
        "player_name": "Alice", "entity_name": "Bob", "message": "hello", "emotion": [],
    }
    native = lambda **unused: {"status": True, "new_events": []}
    first = runtime.prepare_tool("talkTo", native, (), arguments)
    reconstructed = runtime.prepare_tool("talkTo", native, (), arguments)
    synchronization_point = K1DeterministicSynchronizationPoint()

    assert synchronization_point.admission(first) == 1
    with pytest.raises(K1InvariantError, match="reconstructed or substituted"):
        synchronization_point.revision(reconstructed)
