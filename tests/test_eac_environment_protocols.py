"""Contract tests for the environment-facing EAC authority protocols."""

import hashlib
import inspect
from dataclasses import dataclass, field

import pytest

from benchmarks.common.actions import ActionSpec
from benchmarks.common.decision import BudgetState
from benchmarks.common.eac import (
    ActionRef,
    ActorScope,
    CandidateLifecycle,
    EffectGateway,
    ExactRequest,
    EvidenceRoot,
    EPreRef,
    EffectRejected,
    Proposition,
    PropositionKey,
    ProvenanceRecord,
    RuntimeAuthority,
    bind_source_profile,
    load_support_policy,
)
from benchmarks.common.eac.witness import EvidenceSnapshot
from benchmarks.craft.common_bridge import decision_context_from_runtime
from benchmarks.craft.dual_dag.runtime import DualDAGRuntime
from benchmarks.cwah.mock_env import mock_cwah_env_factory


def _profile():
    profile = {
        "profile_id": "test-profile",
        "profile_version": 1,
        "mapping_rules": [{
            "rule_id": "direct", "priority": 0, "record_namespace": "world",
            "record_type": "observation", "root_type": "direct_observation",
            "visibility_field": "visible_to", "source_lineage_field": "lineage",
            "upstream_origin_field": "origin", "trusted_tool_identity": None,
            "trusted_tool_version": None,
        }],
        "trusted_tools": [],
        "supersession_streams": [],
        "derivation_rules": [],
        "aging_rules": [],
        "integrity_contract": {
            "contract_id": "test-contract",
            "contract_version": 1,
            "canonical_content_sha256": "0" * 64,
            "issuer_authentication_rule_id": "test-issuer",
            "rule_evaluation_contract_sha256": "0" * 64,
        },
        "fail_closed": True,
    }
    from benchmarks.common.eac.canonical import canonical_bytes

    profile["detached_profile_sha256"] = hashlib.sha256(
        canonical_bytes(profile)
    ).hexdigest()
    return bind_source_profile(profile)


def _authority(*, reader=None, writer=None, native=None, require_evidence=False):
    authority = RuntimeAuthority(
        policy_binding=load_support_policy(),
        profile_binding=_profile(),
        state_reader=reader,
        candidate_writer=writer,
        authority_nonce="test-nonce",
    )
    proposition = Proposition(PropositionKey("world", "ready", ()))
    action_definition = {"identity": "act", "semantics": "test"}
    action = ActionRef("act", 1, RuntimeAuthority._definition_digest(action_definition))
    declared = (proposition,) if require_evidence else ()
    epre = EPreRef("epre", 1, RuntimeAuthority._definition_digest(declared))
    authority.register_action_definition(action, action_definition)
    authority.register_epre_definition(epre, declared)
    request = ExactRequest("candidate-1", "attempt-1", action)
    actor = ActorScope("agent-1", 1, ("public",))
    authority.register_candidate(
        request,
        actor=actor,
        epre_ref=epre,
        epre=declared,
    )
    if reader is None:
        authority.put_provenance(ProvenanceRecord("prov", "test"))
        authority._put_classified_root(EvidenceRoot(
            "root", "direct_observation", proposition, "test", 1,
            ("agent-1",), "prov", mapping_rule_id="direct",
        ))
    return authority, request


def test_authority_is_independent_of_run_envelope_and_graph_store():
    signature = inspect.signature(RuntimeAuthority)
    assert "run_id" not in signature.parameters
    assert "graph_store" not in signature.parameters
    source = inspect.getsource(RuntimeAuthority)
    assert "#507" not in source
    assert "v4" not in source


def test_authority_consumes_external_evidence_snapshot_reader_protocol():
    proposition = Proposition(PropositionKey("world", "ready", ()))
    snapshot = EvidenceSnapshot(
        roots=(EvidenceRoot("root", "direct_observation", proposition, "external", 1, ("agent-1",), "prov",
                            mapping_rule_id="direct"),),
        provenance=(ProvenanceRecord("prov", "external"),),
        dependency_versions=(("evidence:root", 1), ("provenance:prov", 1),
                             ("conflict:sha256:9ace44b2adc715ea288bf46f946cb3309239c7ddf90c23aaa6fa9506430c9ca3", 1)),
        authenticated_profile_digest=_profile().digest_sha256,
        revision_complete=True,
    )

    @dataclass
    class Reader:
        calls: list[str] = field(default_factory=list)

        def evidence_snapshot(self, actor):
            self.calls.append(actor.actor_id)
            return snapshot

    reader = Reader()
    authority, request = _authority(reader=reader, require_evidence=True)
    authority.evaluate(request.candidate_id)
    assert reader.calls == ["agent-1"]


def test_candidate_writer_observes_canonical_lifecycle():
    @dataclass
    class Writer:
        states: list[tuple[str, CandidateLifecycle]] = field(default_factory=list)

        def record_candidate_state(self, candidate_id, state):
            self.states.append((candidate_id, state))

    writer = Writer()
    authority, request = _authority(writer=writer)
    assert writer.states == [("candidate-1", CandidateLifecycle.PROPOSED)]
    permit = authority.issue_permit(request.candidate_id)
    token = authority.validate_and_consume(request, permit)
    authority.admit_effect(token, request)
    authority.complete_effect(token, "succeeded")
    assert writer.states == [("candidate-1", CandidateLifecycle.PROPOSED),
                             ("candidate-1", CandidateLifecycle.EPISTEMICALLY_ADMISSIBLE)]


def test_craft_context_and_cwah_mock_remain_compatible():
    runtime = DualDAGRuntime(director_ids=["D1", "D2", "D3"], config={})
    context = decision_context_from_runtime(
        runtime=runtime, agent_id="D1", episode_id="episode", step=0,
        legal_actions=(ActionSpec("observe", "observe", {}),),
        remaining_budget=BudgetState(remaining_steps=1),
    )
    assert context.benchmark == "CRAFT"
    assert context.legal_actions[0].action_id == "observe"
    env = mock_cwah_env_factory({})
    assert env.reset(0)[0]["nodes"]
    assert env.step({})[3]["failed_exec"] is False


def test_native_callable_is_only_reachable_through_effect_gateway():
    calls = []
    authority, request = _authority()
    permit = authority.issue_permit(request.candidate_id)
    gateway = EffectGateway(authority, lambda exact: calls.append(exact.candidate_id) or "ok")
    assert not hasattr(gateway, "native_effect")
    assert gateway.execute(request, permit) == "ok"
    assert calls == ["candidate-1"]
    with pytest.raises(EffectRejected, match="missing_permit"):
        gateway.execute(ExactRequest("candidate-2", "attempt-2", request.action))
