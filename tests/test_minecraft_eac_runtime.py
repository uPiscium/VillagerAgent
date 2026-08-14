import json
from copy import copy
from dataclasses import replace

import pytest

from benchmarks.common.eac import ActorScope, AuthorityError, EvidenceRoot, Proposition, PropositionKey
from benchmarks.minecraft.eac_runtime import (
    CLASSIFICATION_PATH, FORBIDDEN_EVIDENCE_ORIGINS, MinecraftEACError,
    MinecraftEACRuntime, RUNTIME_ID, SOURCE_PROFILE_PATH,
)
from env.env import VillagerBench, env_type


class FakeTool:
    def __init__(self, name, function):
        self.name = name
        self.func = function

    def __copy__(self):
        return FakeTool(self.name, self.func)


def mine(player_name: str, x: int, y: int, z: int, emotion=None, murmur=""):
    return {"status": True, "position": [x, y, z]}


def runtime(mode="dual_dag_authority", *, env_pre=None, sec_pre=None):
    if env_pre is None:
        env_pre = lambda unused: True
    prechecks = {name: env_pre for name in (
        "MineBlock", "placeBlock", "navigateTo", "attackTarget", "handoverBlock",
        "talkTo", "scanNearbyEntities", "waitForFeedback",
    )}
    return MinecraftEACRuntime(
        mode=mode, run_id="test-run",
        env_prechecks=prechecks,
        sec_prechecks={"MineBlock": sec_pre} if sec_pre else None,
    )


def observe_target(subject, x=1, y=2, z=3):
    subject.ingest_target_observation("Alice", "MineBlock", {"x": x, "y": y, "z": z})


def test_frozen_minecraft_artifacts_authenticate_and_include_dual_class_fixture():
    classification = json.loads(CLASSIFICATION_PATH.read_text())
    source_profile = json.loads(SOURCE_PROFILE_PATH.read_text())
    mine_class = next(item for item in classification["actions"] if item["action_identity"] == "MineBlock")
    assert mine_class["epre"] is True and mine_class["env_pre"] is True
    assert classification["detached_artifact_sha256"] == "7c8bf97b80c96f1d05e8250cb9d89bb21b35c073f49979501090d72f13b56001"
    assert source_profile["detached_profile_sha256"] == "a6bab72a19bf5dc8f91dc07cfb68f0a54b2cf8d52accc237df4e527ebd3491e3"


def test_direct_observation_allows_authority_effect_and_visible_outcome_is_ingested():
    subject = runtime()
    calls = []
    observe_target(subject)
    result = subject.mediate_tool("MineBlock", lambda **kwargs: calls.append(kwargs) or {"status": True}, (), {
        "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
    })
    assert result["status"] is True and len(calls) == 1
    audit = subject.audit_artifact()
    assert audit["runtime_identity"] == RUNTIME_ID
    assert audit["oracle_state_included"] is False
    assert any(item["record_type"] == "visible_action_outcome" for item in audit["evidence_index"])
    with pytest.raises(MinecraftEACError, match="not_admissible"):
        subject.prepare_tool("MineBlock", mine, (), {
            "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
        })


def test_initial_visible_state_grounds_mine_and_current_fluent_recovers():
    subject = runtime()
    state = {"status": True, "message": {"my_name": "Mallory", "blocks": [
        {"name": "stone", "position": [1, 2, 3]},
    ]}}
    roots = subject.ingest_initial_actor_state("Alice", state)
    assert len(roots) == 1
    first = subject.prepare_tool("MineBlock", lambda **kwargs: {"status": True}, (), {
        "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
    })
    subject.execute_prepared(first)
    positive = roots[0]
    assert subject.authority._roots[positive.root_id].current is False
    with pytest.raises(MinecraftEACError, match="not_admissible"):
        subject.prepare_tool("MineBlock", mine, (), {
            "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
        })
    negative = subject.authority._roots[subject._current_roots[("Alice", positive.proposition.key)]]
    replacement = subject.ingest_target_observation("Alice", "MineBlock", {"x": 1, "y": 2, "z": 3})
    assert subject.authority._roots[negative.root_id].current is False
    assert replacement.current is True
    assert subject.prepare_tool("MineBlock", mine, (), {
        "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
    }).permit is not None


def test_initial_state_ingestion_is_actor_bound_and_one_shot():
    subject = runtime()
    state = {"status": True, "message": {"my_name": "Bob", "blocks": [
        {"name": "stone", "position": [1, 2, 3]},
    ]}}
    assert len(subject.ingest_initial_actor_state("Alice", state)) == 1
    assert subject.ingest_initial_actor_state("Alice", state) == ()
    with pytest.raises(MinecraftEACError, match="not_admissible"):
        subject.prepare_tool("MineBlock", mine, (), {
            "player_name": "Bob", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
        })


def test_failed_initial_snapshot_does_not_consume_later_valid_snapshot():
    subject = runtime()
    assert subject.ingest_initial_actor_state("Alice", {"status": False}) == ()
    valid = {"status": True, "message": {"blocks": [
        {"name": "oak_stairs", "position": [1, 2, 3]},
    ]}}
    assert len(subject.ingest_initial_actor_state("Alice", valid)) == 1


def test_other_actor_evidence_does_not_stale_private_permit():
    subject = runtime()
    observe_target(subject)
    prepared = subject.prepare_tool("MineBlock", lambda **kwargs: {"status": True}, (), {
        "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
    })
    proposition = subject._proposition(subject.classification_for("MineBlock"), {"x": 1, "y": 2, "z": 3})
    subject.ingest_actor_record(actor_id="Bob", proposition=replace(proposition, polarity=False),
                                record_type="direct_observation", source="bob-visible", revision=1)
    assert subject.execute_prepared(prepared)["status"] is True


def test_cross_actor_supersession_is_rejected():
    subject = runtime()
    proposition = subject._proposition(subject.classification_for("MineBlock"), {"x": 1, "y": 2, "z": 3})
    bob = subject.ingest_actor_record(actor_id="Bob", proposition=proposition,
                                      record_type="direct_observation", source="bob-visible", revision=1)
    with pytest.raises(ValueError, match="unauthorized"):
        subject.ingest_actor_record(actor_id="Alice", proposition=replace(proposition, polarity=False),
                                    record_type="direct_observation", source="alice-visible",
                                    revision=2, supersedes=(bob.root_id,))


def test_villagerbench_real_initial_state_is_same_source_for_model_and_authority():
    environment = VillagerBench(env_type.none, 0, False, _virtual_debug=True)
    subject = runtime()
    environment.configure_eac_runtime(subject)
    environment.running = True
    environment.agent_pool = [type("VisibleAgent", (), {"name": "Alice"})()]
    state = {"status": True, "message": {"blocks": [
        {"name": "stone", "position": [1, 2, 3]},
    ]}}
    environment.agent_status = lambda unused: state
    assert environment.get_init_state() == [state]
    assert subject.prepare_tool("MineBlock", mine, (), {
        "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
    }).permit is not None


def test_missing_epre_witness_rejects_even_when_envpre_is_true_with_zero_effect():
    calls = []
    subject = runtime(env_pre=lambda unused: True)
    with pytest.raises(MinecraftEACError, match="not_admissible"):
        subject.mediate_tool("MineBlock", lambda **kwargs: calls.append(kwargs), (), {
            "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
        })
    assert calls == []


def test_dual_class_valid_epre_but_failed_envpre_has_zero_native_effect():
    calls = []
    subject = runtime(env_pre=lambda unused: False)
    observe_target(subject)
    with pytest.raises(MinecraftEACError, match="precheck_rejected"):
        subject.mediate_tool("MineBlock", lambda **kwargs: calls.append(kwargs), (), {
            "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
        })
    assert calls == []
    attempt = subject.authority.attempt_snapshot()[0]
    assert attempt.env_pre_result == "failed" and attempt.sec_pre_result == "passed"


def test_peer_report_alone_is_insufficient_and_invisible_evidence_is_rejected():
    subject = runtime()
    classification = subject.classification_for("MineBlock")
    proposition = subject._proposition(classification, {"x": 1, "y": 2, "z": 3})
    subject.ingest_peer_report("Alice", proposition, "Bob")
    with pytest.raises(MinecraftEACError, match="not_admissible"):
        subject.prepare_tool("MineBlock", mine, (), {
            "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
        })
    subject = runtime()
    subject.ingest_actor_record(actor_id="Bob", proposition=proposition,
                                record_type="direct_observation", source="visible",
                                visible_to=("Bob",))
    with pytest.raises(MinecraftEACError, match="not_admissible"):
        subject.prepare_tool("MineBlock", mine, (), {
            "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
        })


@pytest.mark.parametrize("origin", sorted(FORBIDDEN_EVIDENCE_ORIGINS))
def test_evaluator_oracle_origins_cannot_be_ingested(origin):
    subject = runtime()
    proposition = Proposition(PropositionKey("minecraft", "target_observed", (1, 2, 3), "current"))
    with pytest.raises(MinecraftEACError, match="forbidden"):
        subject.ingest_actor_record(actor_id="Alice", proposition=proposition,
                                    record_type="direct_observation", source=origin)


def test_post_permit_invalidation_rejects_without_planner_reconsideration():
    calls = []
    subject = runtime()
    observe_target(subject)
    prepared = subject.prepare_tool("MineBlock", lambda **kwargs: calls.append(kwargs), (), {
        "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
    })
    candidate = subject.authority._candidates[prepared.request.candidate_id]
    subject.authority.retire_actor_scope(candidate.actor)
    with pytest.raises(MinecraftEACError, match="stale"):
        subject.execute_prepared(prepared)
    assert calls == []


def test_post_permit_conflict_and_epre_retirement_reject_old_permits():
    for mutation in ("conflict", "epre"):
        calls = []
        subject = runtime()
        observe_target(subject)
        prepared = subject.prepare_tool("MineBlock", lambda **kwargs: calls.append(kwargs), (), {
            "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
        })
        candidate = subject.authority._candidates[prepared.request.candidate_id]
        if mutation == "conflict":
            opposite = replace(candidate.epre[0], polarity=False)
            subject.ingest_actor_record(actor_id="Alice", proposition=opposite,
                                        record_type="direct_observation", source="visible-conflict")
        else:
            subject.authority.retire_definition("epre", candidate.epre_ref)
        with pytest.raises(MinecraftEACError, match="stale"):
            subject.execute_prepared(prepared)
        assert calls == []


def test_actor_visible_supersession_stales_dependent_permit():
    subject = runtime()
    old = subject.ingest_target_observation("Alice", "MineBlock", {"x": 1, "y": 2, "z": 3}, revision=1)
    prepared = subject.prepare_tool("MineBlock", mine, (), {
        "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
    })
    proposition = subject._proposition(subject.classification_for("MineBlock"), {"x": 1, "y": 2, "z": 3})
    subject.ingest_actor_record(
        actor_id="Alice", proposition=proposition, record_type="direct_observation",
        source="minecraft-visible-observation", revision=2, supersedes=(old.root_id,),
    )
    with pytest.raises(MinecraftEACError, match="stale"):
        subject.execute_prepared(prepared)


def test_unrelated_visible_evidence_and_hidden_truth_do_not_stale_permit():
    calls = []
    subject = runtime()
    observe_target(subject)
    prepared = subject.prepare_tool("MineBlock", lambda **kwargs: calls.append(kwargs) or {"status": True}, (), {
        "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
    })
    unrelated = Proposition(PropositionKey("minecraft", "weather_visible", ("rain",), "current"))
    subject.ingest_actor_record(actor_id="Alice", proposition=unrelated,
                                record_type="direct_observation", source="visible-weather")
    hidden_evaluator_truth = {"target_exists": False}
    assert hidden_evaluator_truth and subject.execute_prepared(prepared)["status"] is True
    assert len(calls) == 1


def test_advisory_and_authority_share_pre_gate_but_only_advisory_bypasses():
    authority = runtime("dual_dag_authority")
    advisory = runtime("dual_dag_advisory")
    calls = []
    with pytest.raises(MinecraftEACError, match="not_admissible"):
        authority.mediate_tool("MineBlock", lambda **kwargs: calls.append("authority"), (), {
            "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
        })
    advisory.mediate_tool("MineBlock", lambda **kwargs: calls.append("advisory") or {"status": True}, (), {
        "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
    })
    assert calls == ["advisory"]
    assert authority.policy_binding.digest_sha256 == advisory.policy_binding.digest_sha256
    assert authority.profile_binding.digest_sha256 == advisory.profile_binding.digest_sha256


def test_villagerbench_registered_tool_is_mediated_and_direct_tool_is_excluded(tmp_path):
    calls = []
    tool = FakeTool("MineBlock", lambda **kwargs: calls.append(kwargs) or {"status": True})
    environment = VillagerBench(env_type.none, 0, False, _virtual_debug=True)
    subject = runtime()
    environment.configure_eac_runtime(subject)
    observe_target(subject)
    guarded = environment.guard_tool_actions([tool], actor_name="Alice")[0]
    guarded.func(player_name="Alice", x=1, y=2, z=3, emotion=[], murmur="")
    assert len(calls) == 1
    # Direct capability access is explicitly outside the Authority claim.
    tool.func(player_name="Alice", x=1, y=2, z=3, emotion=[], murmur="")
    assert len(calls) == 2
    artifact = environment.get_eac_audit_artifact()
    assert artifact["read_only_projection"] is True and "score" not in json.dumps(artifact)


def test_unclassified_registered_tool_fails_closed_with_zero_effect():
    calls = []
    subject = runtime()
    with pytest.raises(MinecraftEACError, match="unclassified"):
        subject.mediate_tool("unknownTool", lambda **kwargs: calls.append(kwargs), (), {
            "player_name": "Alice",
        })
    assert calls == []


def test_registered_tool_cannot_borrow_another_actor_identity():
    environment = VillagerBench(env_type.none, 0, False, _virtual_debug=True)
    subject = runtime()
    environment.configure_eac_runtime(subject)
    guarded = environment.guard_tool_actions([FakeTool("MineBlock", mine)], actor_name="Alice")[0]
    observe_target(subject)
    with pytest.raises(RuntimeError, match="actor identity mismatch"):
        guarded.func(player_name="Bob", x=1, y=2, z=3, emotion=[], murmur="")


def test_classification_digest_mismatch_fails_closed(monkeypatch):
    import benchmarks.minecraft.eac_runtime as module
    original = module._load_json
    monkeypatch.setattr(module, "_load_json", lambda path: {
        **original(path), "detached_artifact_sha256": "0" * 64,
    } if path == module.CLASSIFICATION_PATH else original(path))
    with pytest.raises(MinecraftEACError, match="digest mismatch"):
        MinecraftEACRuntime(mode="dual_dag_authority", run_id="bad")


def test_observation_adapter_implementation_drift_fails_closed(monkeypatch):
    monkeypatch.setattr("benchmarks.minecraft.eac_runtime.Path.read_bytes", lambda unused: b"drift")
    with pytest.raises(MinecraftEACError, match="implementation digest mismatch"):
        MinecraftEACRuntime(mode="dual_dag_authority", run_id="bad")


def test_prepare_tool_copies_mutable_native_arguments():
    subject = runtime()
    observe_target(subject)
    calls = []
    emotion = []
    prepared = subject.prepare_tool("MineBlock", lambda **kwargs: calls.append(kwargs) or {"status": True}, (), {
        "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": emotion, "murmur": "",
    })
    emotion.append("changed")
    subject.execute_prepared(prepared)
    assert calls[0]["emotion"] == []


def test_failed_bridge_result_is_terminal_failure_and_not_positive_outcome_evidence():
    subject = runtime()
    observe_target(subject)
    result = subject.mediate_tool("MineBlock", lambda **kwargs: {"status": False}, (), {
        "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
    })
    assert result["status"] is False
    assert subject.authority.attempt_snapshot()[0].outcome == "effect_failed"
    assert not any(item["record_type"] == "visible_action_outcome"
                   for item in subject.audit_artifact()["evidence_index"])


def test_scan_result_and_message_are_ingested_at_runtime():
    subject = runtime("dual_dag_advisory")
    subject.mediate_tool("scanNearbyEntities", lambda **kwargs: {
        "status": True, "data": [{"name": "cow", "x": 1, "y": 2, "z": 3}],
    }, (), {"player_name": "Alice", "item_name": "cow", "radius": 5,
            "item_num": 1, "emotion": [], "murmur": ""})
    assert any(item["record_type"] == "trusted_tool_result"
               for item in subject.audit_artifact()["evidence_index"])
    subject.mediate_tool("talkTo", lambda **kwargs: {
        "status": True, "new_events": [{"sender": "Bob", "message": "there is a cow"}],
    }, (), {"player_name": "Alice", "entity_name": "Bob", "message": "hello", "emotion": []})
    assert any(item["record_type"] == "peer_report"
               for item in subject.audit_artifact()["evidence_index"])


def test_scan_observation_can_ground_a_later_target_candidate():
    subject = runtime()
    subject._ingest_result_evidence("Alice", "scanNearbyEntities", {
        "status": True, "data": [{"name": "cow", "x": 1, "y": 2, "z": 3}],
    })
    proposition = Proposition(PropositionKey(
        "minecraft", "entity_observed", ("cow", [1, 2, 3]), "current"))
    from benchmarks.common.eac.witness import EvidenceSnapshot, evaluate_epistemic_admissibility
    snapshot = EvidenceSnapshot(tuple(subject.authority._roots.values()), (),
                                tuple(subject.authority._provenance.values()))
    result = evaluate_epistemic_admissibility(
        ActorScope("Alice", 1),
        (proposition,), snapshot, subject.policy_binding, subject.profile_binding)
    assert result.admissible


def test_scan_observation_grounds_real_mineblock_candidate_without_helper_injection():
    subject = runtime()
    subject._ingest_result_evidence("Alice", "scanNearbyEntities", {
        "status": True, "data": [{"x": 1, "y": 2, "z": 3}],
    })
    with pytest.raises(MinecraftEACError, match="not_admissible"):
        subject.prepare_tool("MineBlock", lambda **kwargs: {"status": True}, (), {
            "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
        })


def test_coordinate_only_scan_response_binds_requested_name_for_named_target_epre():
    subject = runtime()
    subject._ingest_result_evidence("Alice", "scanNearbyEntities", {
        "status": True, "data": [{"x": 1, "y": 2, "z": 3}],
    }, {"item_name": "cow"})
    prepared = subject.prepare_tool("attackTarget", lambda **kwargs: {"status": True}, (), {
        "player_name": "Alice", "target_name": "cow", "emotion": [], "murmur": "",
    })
    assert prepared.permit is not None


def test_named_target_evidence_and_candidate_share_identifier_normalization():
    subject = runtime()
    subject._ingest_result_evidence("Alice", "scanNearbyEntities", {
        "status": True, "data": [{"x": 1, "y": 2, "z": 3}], "observed_name": "oak_cow",
    }, {"item_name": "Oak Cow"})
    prepared = subject.prepare_tool("attackTarget", lambda **kwargs: {"status": True}, (), {
        "player_name": "Alice", "target_name": "Oak Cow", "emotion": [], "murmur": "",
    })
    assert prepared.permit is not None


def test_same_action_supports_multiple_target_specific_epre_bindings():
    subject = runtime()
    observe_target(subject, 1, 2, 3)
    first = subject.prepare_tool("MineBlock", lambda **kwargs: {"status": True}, (), {
        "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
    })
    observe_target(subject, 4, 5, 6)
    second = subject.prepare_tool("MineBlock", lambda **kwargs: {"status": True}, (), {
        "player_name": "Alice", "x": 4, "y": 5, "z": 6, "emotion": [], "murmur": "",
    })
    assert first.request.candidate_id != second.request.candidate_id


def test_eac_environment_registers_only_classified_tools():
    environment = VillagerBench(env_type.none, 0, False, _virtual_debug=True)
    environment.configure_eac_runtime(runtime())
    tools = environment.guard_tool_actions([
        FakeTool("MineBlock", mine), FakeTool("fetchContainerContents", lambda **kwargs: None),
    ], actor_name="Alice")
    assert [tool.name for tool in tools] == ["MineBlock"]


def test_default_envpre_uses_native_read_only_preflight(monkeypatch):
    subject = MinecraftEACRuntime(mode="dual_dag_authority", run_id="preflight")
    observe_target(subject)
    calls = []

    class Response:
        def json(self):
            return {"status": False}

    monkeypatch.setattr("env.minecraft_client._minecraft_request",
                        lambda *args, **kwargs: calls.append((args, kwargs)) or Response())
    monkeypatch.setattr("env.minecraft_client.Agent.get_agent_url", lambda unused: "http://agent")
    native = []
    with pytest.raises(MinecraftEACError, match="precheck_rejected"):
        subject.mediate_tool("MineBlock", lambda **kwargs: native.append(kwargs), (), {
            "player_name": "Alice", "x": 1, "y": 2, "z": 3, "emotion": [], "murmur": "",
        })
    assert calls and calls[0][0][1].endswith("/post_eac_preflight")
    assert native == []


def test_audit_projection_is_bounded_and_reports_truncation():
    subject = runtime("dual_dag_advisory")
    for index in range(300):
        proposition = Proposition(PropositionKey("minecraft", "visible", (index,), "current"))
        subject.ingest_actor_record(actor_id="Alice", proposition=proposition,
                                    record_type="direct_observation", source="visible")
    artifact = subject.audit_artifact()
    assert len(artifact["evidence_index"]) == 256
    assert artifact["evidence_truncated"] is True and artifact["evidence_total"] == 300
