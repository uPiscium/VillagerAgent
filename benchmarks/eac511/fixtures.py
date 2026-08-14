"""Deterministic Tier-1 integrity fixtures; no task or statistical claims."""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from benchmarks.common.eac import (
    ActionRef, ActorScope, AuthorityError, EPreRef, EffectGateway,
    EffectRejected, EvidenceRoot, ExactRequest, Proposition, PropositionKey,
    ProvenanceRecord, RuntimeAuthority, load_support_policy, bind_source_profile,
)
from benchmarks.common.eac.policy import PolicyBinding
from benchmarks.common.eac.canonical import canonical_bytes
from .identity import detached_digest
from .model import Tier1Kind


@dataclass(frozen=True, slots=True)
class Tier1Record:
    """A reproducible control-plane integrity observation."""
    fixture_id: str
    kind: Tier1Kind
    passed: bool
    evidence_digest: str
    evidence: tuple[tuple[str, Any], ...] = ()


def _profile():
    root = Path(__file__).resolve().parents[2]
    return bind_source_profile(json.loads(
        (root / "docs/eac/minecraft_source_profile_v1.json").read_text()))


def _authority(*, mode: str = "authority", actor_id: str = "Alice",
               actor_revision: int = 1, candidate_id: str = "candidate",
               attempt_id: str = "attempt", capability: str = "build"):
    policy, profile = load_support_policy(), _profile()
    authority = RuntimeAuthority(policy_binding=policy, profile_binding=profile,
                                 mode=mode, authority_nonce="tier1-fixed-nonce")
    proposition = Proposition(PropositionKey(
        "minecraft", "target_block_present", (1, 2, 3), "current"))
    action_definition = {"identity": "build", "semantics": "tier1"}
    action = ActionRef("build", 1, RuntimeAuthority._definition_digest(action_definition))
    epre = EPreRef("target-present", 1,
                   RuntimeAuthority._definition_digest((proposition,)))
    authority.register_action_definition(action, action_definition)
    authority.register_epre_definition(epre, (proposition,))
    authority.put_provenance(ProvenanceRecord("tier1-provenance", "sensor"))
    authority._put_classified_root(EvidenceRoot(
        "tier1-root", "direct_observation", proposition, "sensor", 1,
        ("Alice", "Bob"), "tier1-provenance", source_lineage_id="sensor",
        upstream_origin_id="sensor", mapping_rule_id="minecraft-direct-observation"))
    request = ExactRequest(candidate_id, attempt_id, action,
                           (("count", 1),), target=[1, 2, 3])
    actor = ActorScope(actor_id, actor_revision, ("village",))
    authority.register_candidate(request, actor=actor, epre_ref=epre,
                                 epre=(proposition,),
                                 capability_dependencies=(capability,))
    return authority, request, proposition, action, epre


def _issued(**kwargs):
    authority, request, proposition, action, epre = _authority(**kwargs)
    permit = authority.issue_permit(request.candidate_id)
    return authority, request, proposition, action, epre, permit


def _reason(call: Callable[[], Any]) -> str:
    try:
        call()
    except (AuthorityError, EffectRejected) as exc:
        return getattr(exc, "reason", str(exc))
    return "accepted"


def _json_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict) or hasattr(value, "items"):
        return {key: _json_value(item) for key, item in value.items()}
    return value


def _stale_dependency() -> dict[str, Any]:
    authority, request, _, _, _, permit = _issued()
    effects: list[str] = []
    authority.mutate_dependencies(("capability:build",), reason="relevant mutation")
    gateway = EffectGateway(authority, lambda unused: effects.append("effect"))
    reason = _reason(lambda: gateway.execute(request, permit))
    return {"rejection": reason, "effect_count": len(effects),
            "lifecycle": authority.permit(permit.permit_id).lifecycle.value,
            "expected": "stale"}


def _post_permit_epistemic_invalidation() -> dict[str, Any]:
    """Invalidate actor-visible support, not a generic capability dependency."""
    authority, request, _, _, _, permit = _issued()
    before = authority.evaluate(request.candidate_id)
    before_witness = before.witnesses[0] if before.witnesses else None
    before_validity = (before_witness.validity if before_witness else ())
    # This is the actor-visible evidence change at the required injection point.
    authority.remove_root("tier1-root")
    after = authority.evaluate(request.candidate_id)
    after_witness = after.witnesses[0] if after.witnesses else None
    effects: list[str] = []
    gateway = EffectGateway(authority, lambda unused: effects.append("effect"))
    rejection = _reason(lambda: gateway.execute(request, permit))
    return {
        "witness_id_before": before_witness.witness_id if before_witness else None,
        "witness_validity_before": tuple((key.value, value) for key, value in before_validity),
        "eadm_before": before.admissible,
        "witness_id_after": after_witness.witness_id if after_witness else None,
        "witness_validity_after": tuple((key.value, value) for key, value in
                                         (after_witness.validity if after_witness else ())),
        "assessment_validity_after": tuple((key.value, value) for key, value in
                                            (after.assessments[0].validity
                                             if after.assessments else ())),
        "eadm_after": after.admissible,
        "permit_lifecycle": authority.permit(permit.permit_id).lifecycle.value,
        "rejection": rejection,
        "effect_count": len(effects),
        "mutation": "actor_visible_evidence_support",
        "witness_transition": "valid_to_invalid",
        "injection_phase": "AFTER_PERMIT_BEFORE_EFFECT",
        "planner_reprompt": False,
    }


def _unrelated_dependency() -> dict[str, Any]:
    authority, request, _, _, _, permit = _issued()
    effects: list[str] = []
    authority.mutate_dependencies(("unrelated",), reason="unrelated mutation")
    gateway = EffectGateway(authority, lambda unused: effects.append("effect"))
    result = gateway.execute(request, permit)
    return {"result": result, "effect_count": len(effects), "expected": "effect"}


def _replay_and_revocation() -> dict[str, Any]:
    authority, request, _, _, _, permit = _issued()
    token = authority.validate_and_consume(request, permit)
    authority.admit_effect(token, request)
    authority.complete_effect(token, "succeeded")
    replay = _reason(lambda: authority.validate_and_consume(request, permit))
    authority2, request2, _, _, _, first = _issued()
    second = authority2.issue_permit(request2.candidate_id)
    revoked = _reason(lambda: authority2.validate_and_consume(request2, first))
    return {"replay": replay, "revoked": revoked,
            "new_permit": second.permit_id != first.permit_id,
            "expected": ("replay", "revoked")}


def _missing_permit() -> dict[str, Any]:
    authority, request, _, _, _, _ = _issued()
    effects: list[str] = []
    gateway = EffectGateway(authority, lambda unused: effects.append("effect"))
    reason = _reason(lambda: gateway.execute(request))
    return {"rejection": reason, "effect_count": len(effects),
            "expected": "missing_permit"}


def _epre_revision() -> dict[str, Any]:
    authority, request, proposition, _, epre, permit = _issued()
    v2_proposition = Proposition(
        PropositionKey("minecraft", "target_block_present", (1, 2, 4), "current"))
    v2 = EPreRef(epre.identity, 2,
                 RuntimeAuthority._definition_digest((v2_proposition,)))
    authority.register_epre_definition(v2, (v2_proposition,))
    authority.retire_definition("epre", epre)
    old_continue = _reason(lambda: authority.evaluate(request.candidate_id))
    old_reissue = _reason(lambda: authority.issue_permit(request.candidate_id))
    authority._put_classified_root(EvidenceRoot(
        "tier1-root-v2", "direct_observation", v2_proposition, "sensor", 2,
        ("Alice",), "tier1-provenance", source_lineage_id="sensor",
        upstream_origin_id="sensor", mapping_rule_id="minecraft-direct-observation"))
    v2_request = ExactRequest("candidate-v2", "attempt-v2", request.action,
                              request.arguments, request.target)
    authority.register_candidate(v2_request, actor=ActorScope("Alice", 1, ("village",)),
                                 epre_ref=v2, epre=(v2_proposition,),
                                 capability_dependencies=("build",))
    v2_decision = authority.evaluate(v2_request.candidate_id)
    old_permit_rejection = _reason(lambda: authority.validate_and_consume(request, permit))
    return {"old_continue_rejection": old_continue, "old_reissue_rejection": old_reissue,
            "old_permit_rejection": old_permit_rejection,
            "old_permit_lifecycle": authority.permit(permit.permit_id).lifecycle.value,
            "v2_registered": True, "v2_evaluated": True,
            "v2_epre_version": v2.version, "v2_admissible": v2_decision.admissible}


def _policy_rotation() -> dict[str, Any]:
    authority, request, _, _, _, permit = _issued()
    primary_digest = authority.policy.digest
    alternate_policy = _json_value(authority.policy_binding.policy)
    alternate_policy["policy_id"] = "eac-integrity-alternate"
    alternate_policy["policy_version"] = 2
    unchanged_rule_sections = all(
        alternate_policy[key] == _json_value(authority.policy_binding.policy[key])
        for key in ("support_semantics", "conflict_and_defeat", "supersession", "freshness")
    )
    alternate_digest = hashlib.sha256(canonical_bytes(alternate_policy)).hexdigest()
    alternate_binding = PolicyBinding(
        "eac-integrity-alternate", 2, alternate_digest, alternate_policy,
    )
    alternate = RuntimeAuthority(
        policy_binding=alternate_binding, profile_binding=authority.profile_binding,
        mode="authority", authority_nonce="tier1-alternate-policy-v2",
    )
    alternate.register_action_definition(request.action, {"identity": "build", "semantics": "tier1"})
    alternate.register_epre_definition(epre := EPreRef("target-present", 1,
        RuntimeAuthority._definition_digest((Proposition(PropositionKey(
            "minecraft", "target_block_present", (1, 2, 3), "current")),))),
        (Proposition(PropositionKey("minecraft", "target_block_present", (1, 2, 3), "current")),))
    alternate.put_provenance(ProvenanceRecord("tier1-provenance", "sensor"))
    alternate._put_classified_root(EvidenceRoot(
        "tier1-root", "direct_observation", Proposition(PropositionKey(
            "minecraft", "target_block_present", (1, 2, 3), "current")), "sensor", 1,
        ("Alice",), "tier1-provenance", source_lineage_id="sensor",
        upstream_origin_id="sensor", mapping_rule_id="minecraft-direct-observation"))
    alternate_request = ExactRequest("candidate-integrity-v2", "attempt-integrity-v2",
                                     request.action, request.arguments, request.target)
    alternate.register_candidate(alternate_request, actor=ActorScope("Alice", 1, ("village",)),
                                 epre_ref=epre, epre=(Proposition(PropositionKey(
                                     "minecraft", "target_block_present", (1, 2, 3), "current")),),
                                 capability_dependencies=("build",))
    authority.retire_authority_semantics()
    alternate_decision = alternate.evaluate(alternate_request.candidate_id)
    alternate_permit_rejection = _reason(
        lambda: alternate.issue_permit(alternate_request.candidate_id))
    alternate_effects: list[str] = []
    alternate_effect_rejection = _reason(lambda: EffectGateway(
        alternate, lambda unused: alternate_effects.append("effect")
    ).execute(alternate_request))
    alternate_outcome = (
        "FAIL_CLOSED_UNAPPROVED_POLICY_IDENTITY"
        if (not alternate_decision.admissible and
            alternate_permit_rejection == "not_admissible" and
            alternate_effect_rejection == "missing_permit" and not alternate_effects)
        else "UNEXPECTED_ALTERNATE_POLICY_OUTCOME"
    )
    return {"rejection": _reason(lambda: authority.validate_and_consume(request, permit)),
             "old_permit_lifecycle": authority.permit(permit.permit_id).lifecycle.value,
             "primary_policy_unchanged": authority.policy.digest == primary_digest,
             "alternate_policy_identity": alternate.policy.identity,
             "alternate_policy_version": alternate.policy.version,
              "alternate_candidate_registered": True,
              "alternate_candidate_evaluated": True,
              "alternate_candidate_admissible": alternate_decision.admissible,
              "alternate_candidate_reasons": alternate_decision.reasons,
              "alternate_permit_rejection": alternate_permit_rejection,
              "alternate_effect_rejection": alternate_effect_rejection,
              "alternate_evaluation_outcome": alternate_outcome,
              "support_rule_sections_unchanged": unchanged_rule_sections,
              "alternate_effect_count": len(alternate_effects)}


def _actor_scope() -> dict[str, Any]:
    authority, request, proposition, action, epre, alice_permit = _issued()
    other = ExactRequest("candidate-bob", "attempt-bob", action)
    authority.register_candidate(other, actor=ActorScope("Bob", 1, ("village",)),
                                 epre_ref=epre, epre=(proposition,),
                                 capability_dependencies=("build",))
    bob_permit = authority.issue_permit(other.candidate_id)
    authority.retire_actor_scope(ActorScope("Alice", 1, ("village",)))
    bob_token = authority.validate_and_consume(other, bob_permit)
    authority.reject_pre_effect(bob_token, "fixture_complete")
    return {"alice_rejection": _reason(lambda: authority.validate_and_consume(request, alice_permit)),
            "bob_valid": True, "expected": "actor_scope_isolated"}


def _dual_prechecks() -> dict[str, Any]:
    results: dict[str, Any] = {}
    for label, env, sec in (("epre_pass_envpre_reject", lambda _: False, lambda _: True),
                            ("epre_pass_secpre_reject", lambda _: True, lambda _: False)):
        authority, request, _, _, _, permit = _issued()
        effects: list[str] = []
        gateway = EffectGateway(authority, lambda unused: effects.append("effect"),
                                env_pre=env, sec_pre=sec)
        results[label] = {"rejection": _reason(lambda: gateway.execute(request, permit)),
                          "effect_count": len(effects)}
    return results


def _advisory_authority() -> dict[str, Any]:
    authority, request, _, _, _, permit = _issued()
    shadow_authority, shadow_request, _, _, _, _ = _issued(mode="advisory")
    authority_decision = authority.evaluate(request.candidate_id)
    advisory_decision = shadow_authority.evaluate(shadow_request.candidate_id)
    effects: list[str] = []
    EffectGateway(authority, lambda unused: effects.append("authority")).execute(request, permit)
    advisory_effects: list[str] = []
    EffectGateway(shadow_authority, lambda unused: advisory_effects.append("advisory")).execute_advisory(shadow_request)
    return {"same_pregate": authority_decision == advisory_decision,
            "authority_effects": len(effects),
            "advisory_effects": len(advisory_effects), "authority_enforces": True}


def tier1_fixtures() -> tuple[Tier1Record, ...]:
    """Execute deterministic in-process controls and return explicit observations."""
    checks: dict[Tier1Kind, Callable[[], dict[str, Any]]] = {
        Tier1Kind.ACTOR_SCOPE: _actor_scope,
        Tier1Kind.DUAL_CLASS: _dual_prechecks,
        Tier1Kind.PRE_GATE_EQUIVALENCE: _advisory_authority,
        Tier1Kind.REPLAY_AND_REVOCATION: _replay_and_revocation,
        Tier1Kind.SUPPORTED_PATH_BYPASS: _missing_permit,
        Tier1Kind.UNRELATED_DEPENDENCY: _unrelated_dependency,
        Tier1Kind.RELEVANT_DEPENDENCY: _stale_dependency,
        Tier1Kind.POST_PERMIT_INVALIDATION: _post_permit_epistemic_invalidation,
        Tier1Kind.EPRE_REVISION: _epre_revision,
        Tier1Kind.POLICY_REVISION: _policy_rotation,
    }
    records = []
    for kind in Tier1Kind:
        evidence = checks[kind]()
        if kind is Tier1Kind.ACTOR_SCOPE:
            passed = evidence.get("alice_rejection") == "stale" and evidence.get("bob_valid") is True
        elif kind is Tier1Kind.DUAL_CLASS:
            passed = all(isinstance(item, dict) and item.get("rejection") == "precheck_rejected" and
                         item.get("effect_count") == 0 for item in evidence.values())
        elif kind is Tier1Kind.PRE_GATE_EQUIVALENCE:
            passed = evidence == {"same_pregate": True, "authority_effects": 1,
                                  "advisory_effects": 1, "authority_enforces": True}
        elif kind is Tier1Kind.REPLAY_AND_REVOCATION:
            passed = (evidence.get("replay") == "replay" and evidence.get("revoked") == "revoked"
                      and evidence.get("new_permit") is True)
        elif kind is Tier1Kind.SUPPORTED_PATH_BYPASS:
            passed = evidence.get("rejection") == "missing_permit" and evidence.get("effect_count") == 0
        elif kind is Tier1Kind.UNRELATED_DEPENDENCY:
            passed = evidence.get("result") is None and evidence.get("effect_count") == 1
        elif kind in (Tier1Kind.RELEVANT_DEPENDENCY, Tier1Kind.POST_PERMIT_INVALIDATION):
            if kind is Tier1Kind.POST_PERMIT_INVALIDATION:
                passed = (evidence.get("eadm_before") is True and
                          evidence.get("eadm_after") is False and
                          all(value is True for _, value in evidence.get("witness_validity_before", ())) and
                          evidence.get("witness_id_before") is not None and
                          evidence.get("witness_id_after") is None and
                          any(value is False for _, value in evidence.get("assessment_validity_after", ())) and
                          evidence.get("permit_lifecycle") == "stale" and
                          evidence.get("rejection") == "stale" and
                          evidence.get("effect_count") == 0 and
                           evidence.get("mutation") == "actor_visible_evidence_support" and
                           evidence.get("witness_transition") == "valid_to_invalid" and
                           evidence.get("injection_phase") == "AFTER_PERMIT_BEFORE_EFFECT" and
                           evidence.get("planner_reprompt") is False)
            else:
                passed = evidence.get("rejection") == "stale" and evidence.get("effect_count") == 0
        elif kind is Tier1Kind.EPRE_REVISION:
            passed = (evidence.get("old_continue_rejection") == "semantic_binding_retired" and
                      evidence.get("old_reissue_rejection") == "semantic_binding_retired" and
                      evidence.get("old_permit_rejection") == "stale" and
                      evidence.get("old_permit_lifecycle") == "stale" and
                      evidence.get("v2_registered") is True and
                      evidence.get("v2_evaluated") is True and
                      evidence.get("v2_epre_version") == 2 and
                      evidence.get("v2_admissible") is True)
        else:
            passed = (evidence.get("rejection") == "stale" and
                       evidence.get("primary_policy_unchanged") is True and
                       evidence.get("alternate_policy_identity") == "eac-integrity-alternate" and
                       evidence.get("alternate_policy_version") == 2 and
                       evidence.get("old_permit_lifecycle") == "stale" and
                       evidence.get("alternate_candidate_registered") is True and
                       evidence.get("alternate_candidate_evaluated") is True and
                       evidence.get("alternate_candidate_admissible") is False and
                       evidence.get("alternate_candidate_reasons") == ("fail_closed:ValueError",) and
                       evidence.get("alternate_permit_rejection") == "not_admissible" and
                       evidence.get("alternate_effect_rejection") == "missing_permit" and
                       evidence.get("alternate_evaluation_outcome") ==
                       "FAIL_CLOSED_UNAPPROVED_POLICY_IDENTITY" and
                       evidence.get("support_rule_sections_unchanged") is True and
                       evidence.get("alternate_effect_count") == 0)
        frozen = tuple(sorted(evidence.items(), key=lambda item: item[0]))
        records.append(Tier1Record(f"tier1:{kind.value}", kind, passed,
                                   detached_digest({"kind": kind.value,
                                                    "evidence": _json_value(evidence)}),
                                   frozen))
    return tuple(records)


def validate_tier1_records(records: tuple[Tier1Record, ...]) -> None:
    """Ensure fixture output is complete, deterministic, and outcome-bearing."""
    expected = tuple(Tier1Kind)
    if tuple(record.kind for record in records) != expected:
        raise ValueError("Tier1 records must contain each integrity fixture exactly once")
    if any(not record.fixture_id.startswith("tier1:") or
           len(record.evidence_digest) != 64 or
           not record.evidence for record in records):
        raise ValueError("invalid Tier1 deterministic record")
