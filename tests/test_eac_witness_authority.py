import hashlib

import pytest

from benchmarks.common.eac import (
    ActorScope,
    EvidenceRoot,
    Proposition,
    PropositionKey,
    ProvenanceRecord,
    SupportDerivation,
    bind_source_profile,
    load_support_policy,
    match_mapping,
)
from benchmarks.common.eac.canonical import canonical_bytes
from benchmarks.common.eac.witness import evaluate_epistemic_admissibility
from benchmarks.common.eac.witness import EvidenceSnapshot


def _profile(**changes):
    """Return a minimally valid profile with its detached digest recomputed."""
    profile = {
        "profile_id": "test-profile",
        "profile_version": 1,
        "detached_profile_sha256": "",
        "mapping_rules": [{
            "rule_id": "direct",
            "priority": 1,
            "record_namespace": "world",
            "record_type": "observation",
            "root_type": "direct_observation",
            "visibility_field": "visible_to",
            "source_lineage_field": "source_lineage_id",
            "upstream_origin_field": "upstream_origin_id",
            "trusted_tool_identity": None,
            "trusted_tool_version": None,
        }],
        "trusted_tools": [],
        "supersession_streams": [],
        "derivation_rules": [{"rule_id": "and", "rule_version": 1,
                               "canonical_content_sha256": "0" * 64}],
        "aging_rules": [],
        "integrity_contract": {
            "contract_id": "test", "contract_version": 1,
            "canonical_content_sha256": "0" * 64,
            "issuer_authentication_rule_id": "issuer",
            "rule_evaluation_contract_sha256": "0" * 64,
        },
        "fail_closed": True,
    }
    profile.update(changes)
    detached = dict(profile)
    detached.pop("detached_profile_sha256")
    profile["detached_profile_sha256"] = hashlib.sha256(canonical_bytes(detached)).hexdigest()
    return profile


@pytest.fixture
def deps():
    return load_support_policy(), bind_source_profile(_profile())


def _prop(polarity=True, scope="current", argument="lantern"):
    return Proposition(PropositionKey("world", "lit", (argument,), scope), polarity)


def _state(prop, *, root_type="direct_observation", visible=("agent",), source="sensor",
           lineage="sensor", upstream="sensor", root_id="root", revision=1,
           provenance=True, supersedes=(), current=True):
    roots = (EvidenceRoot(root_id, root_type, prop, source, revision, visible,
                          "prov" if provenance else None, supersedes, lineage, upstream,
                          True, current),)
    prov = (ProvenanceRecord("prov", source),) if provenance else ()
    return EvidenceSnapshot(roots, (), prov)


def test_stable_proposition_key_excludes_observation_revisions():
    assert PropositionKey("world", "lit", ("lantern",), "current") == PropositionKey(
        "world", "lit", ("lantern",), "current"
    )
    assert PropositionKey("world", "lit", ("lantern",), "current") != PropositionKey(
        "world", "lit", ("lantern",), "task_phase=B"
    )


@pytest.mark.parametrize("value", [1.5, float("nan"), {"é": 1}, "\ud800", 2**53])
def test_constrained_jcs_rejects_unsupported_values(value):
    with pytest.raises(ValueError):
        canonical_bytes(value)


def test_policy_digest_is_authenticated(deps):
    policy, profile = deps
    assert policy.digest_sha256 == "ef34b67ef618ed4b34a9c2720d854e02d8fb6af917a0cbe472daef8cc5603d51"
    assert profile.digest_sha256 == hashlib.sha256(canonical_bytes({k: v for k, v in _profile().items()
                                                                     if k != "detached_profile_sha256"})).hexdigest()


@pytest.mark.parametrize("record", [
    {"namespace": "missing", "type": "observation", "visible_to": ["agent"],
     "source_lineage_id": "s", "upstream_origin_id": "u"},
    {"namespace": "world", "type": "observation",
     "source_lineage_id": "s", "upstream_origin_id": "u"},
])
def test_profile_mapping_fails_closed_without_mapping_or_visibility(record, deps):
    with pytest.raises(ValueError):
        match_mapping(record, deps[1])


def test_profile_mapping_tie_and_unknown_trusted_tool_fail_closed():
    tied = _profile(mapping_rules=[_profile()["mapping_rules"][0], dict(_profile()["mapping_rules"][0], priority=1)])
    tied_binding = bind_source_profile(tied)
    with pytest.raises(ValueError):
        match_mapping({"namespace": "world", "type": "observation", "visible_to": ["agent"],
                       "source_lineage_id": "s", "upstream_origin_id": "u"}, tied_binding)
    bad_tool = _profile(mapping_rules=[dict(_profile()["mapping_rules"][0], root_type="trusted_tool_result",
                                             trusted_tool_identity="unknown", trusted_tool_version="1")])
    with pytest.raises(ValueError):
        bind_source_profile(bad_tool)


def test_profile_unknown_fields_fail_closed():
    with pytest.raises(ValueError, match="unknown fields"):
        bind_source_profile(_profile(unexpected=True))


def test_missing_or_unjustified_epre_and_invisible_root_are_rejected(deps):
    policy, profile = deps
    prop = _prop()
    actor = ActorScope("agent", 1)
    empty = evaluate_epistemic_admissibility(actor, (prop,), EvidenceSnapshot(), policy, profile)
    invisible = evaluate_epistemic_admissibility(actor, (prop,), _state(prop, visible=("other",)), policy, profile)
    assert not empty.admissible and not invisible.admissible


def test_evaluator_only_root_is_not_actor_visible_support(deps):
    policy, profile = deps
    prop = _prop()
    result = evaluate_epistemic_admissibility(
        ActorScope("agent", 1), (prop,), _state(prop, root_type="visible_action_outcome", visible=()), policy, profile
    )
    assert not result.admissible


def test_self_support_and_unsupported_cycle_are_rejected(deps):
    policy, profile = deps
    prop = _prop()
    self_support = EvidenceSnapshot((), (SupportDerivation("d", "and", ("d",), prop),), ())
    cycle = EvidenceSnapshot((), (
        SupportDerivation("a", "and", ("b",), prop),
        SupportDerivation("b", "and", ("a",), prop),
    ), ())
    actor = ActorScope("agent", 1)
    assert not evaluate_epistemic_admissibility(actor, (prop,), self_support, policy, profile).admissible
    assert not evaluate_epistemic_admissibility(actor, (prop,), cycle, policy, profile).admissible


def test_finite_grounded_derivation_is_admissible(deps):
    policy, profile = deps
    premise, conclusion = _prop(), _prop(scope="task")
    root = _state(premise)
    state = EvidenceSnapshot(root.roots, (SupportDerivation("d", "and", ("root",), conclusion),), root.provenance)
    evaluators = {("and", 1, "0" * 64): lambda premises, result: premises == (premise,) and result == conclusion}
    result = evaluate_epistemic_admissibility(ActorScope("agent", 1), (conclusion,), state, policy, profile,
                                              rule_evaluators=evaluators)
    assert result.admissible


def test_derivation_requires_exact_versioned_evaluator_and_semantic_match(deps):
    policy, profile = deps
    premise, conclusion = _prop(), _prop(scope="task")
    root = _state(premise)
    state = EvidenceSnapshot(root.roots, (SupportDerivation("d", "and", ("root",), conclusion),), root.provenance)
    actor = ActorScope("agent", 1)
    assert not evaluate_epistemic_admissibility(actor, (conclusion,), state, policy, profile).admissible
    wrong_digest = {("and", 1, "1" * 64): lambda unused_premises, unused_result: True}
    assert not evaluate_epistemic_admissibility(
        actor, (conclusion,), state, policy, profile, rule_evaluators=wrong_digest).admissible
    rejects_semantics = {("and", 1, "0" * 64): lambda unused_premises, unused_result: False}
    assert not evaluate_epistemic_admissibility(
        actor, (conclusion,), state, policy, profile, rule_evaluators=rejects_semantics).admissible


def test_peer_alone_and_repeated_same_source_reports_are_insufficient(deps):
    policy, profile = deps
    prop = _prop()
    reports = tuple(EvidenceRoot(str(i), "unverified_peer_report", prop, "peer", i, ("agent",),
                                 None, (), "same", "same", True, True) for i in (1, 2))
    assert not evaluate_epistemic_admissibility(ActorScope("agent", 1), (prop,),
                                                EvidenceSnapshot(reports), policy, profile).admissible


def test_corrobated_peer_report_adds_context_but_not_support_weight(deps):
    policy, profile = deps
    prop = _prop()
    direct = _state(prop, root_id="direct", lineage="sensor", upstream="sensor")
    peer = EvidenceRoot("peer", "unverified_peer_report", prop, "peer", 1, ("agent",), None, (), "peer", "peer")
    state = EvidenceSnapshot(direct.roots + (peer,), (), direct.provenance)
    result = evaluate_epistemic_admissibility(ActorScope("agent", 1), (prop,), state, policy, profile)
    assert result.admissible and {root.root_id for root in result.witnesses[0].roots} == {"direct", "peer"}


def test_contradictory_prima_facie_roots_defeat_both_polarities(deps):
    policy, profile = deps
    prop = _prop()
    positive = _state(prop, root_id="yes")
    negative = _state(_prop(False), root_id="no")
    state = EvidenceSnapshot(positive.roots + negative.roots, (), positive.provenance)
    actor = ActorScope("agent", 1)
    assert not evaluate_epistemic_admissibility(actor, (prop,), state, policy, profile).admissible
    assert not evaluate_epistemic_admissibility(actor, (_prop(False),), state, policy, profile).admissible


def test_same_fluent_supersession_invalidates_old_but_other_scope_survives(deps):
    policy, profile = deps
    old = _state(_prop(), root_id="old", revision=1)
    newer = _state(_prop(), root_id="new", revision=2, supersedes=("old",))
    other = _state(_prop(scope="task_phase=B"), root_id="other")
    state = EvidenceSnapshot(old.roots + newer.roots + other.roots, (), old.provenance)
    actor = ActorScope("agent", 1)
    assert evaluate_epistemic_admissibility(actor, (_prop(),), state, policy, profile).admissible
    assert evaluate_epistemic_admissibility(actor, (_prop(scope="task_phase=B"),), state, policy, profile).admissible


def test_epre_and_envpre_are_independent_environment_legality_cannot_substitute(deps):
    policy, profile = deps
    target, legality = _prop(), _prop(scope="environment")
    state = _state(legality)
    result = evaluate_epistemic_admissibility(ActorScope("agent", 1), (target,), state, policy, profile)
    assert not result.admissible


def test_same_action_legality_probe_cannot_support_its_epre(deps):
    policy, profile = deps
    proposition = _prop()
    root = EvidenceRoot(
        "probe", "trusted_tool_result", proposition, "legality", 1, ("agent",), "prov",
        originating_action_identity="build", evidence_gathering_action=False)
    state = EvidenceSnapshot((root,), (), (ProvenanceRecord("prov", "legality"),))
    result = evaluate_epistemic_admissibility(
        ActorScope("agent", 1), (proposition,), state, policy, profile,
        forbidden_support_action="build")
    assert not result.admissible
