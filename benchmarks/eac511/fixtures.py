"""Deterministic Tier-1 integrity fixtures; no task or statistical claims."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from benchmarks.common.eac import (
    ActionRef, ActorScope, AuthorityError, EPreRef, EffectGateway,
    EffectRejected, EvidenceRoot, ExactRequest, Proposition, PropositionKey,
    ProvenanceRecord, RuntimeAuthority, load_support_policy, bind_source_profile,
)
from benchmarks.common.eac.policy import PolicyBinding
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
    if isinstance(value, dict):
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
    v2 = EPreRef(epre.identity, 2,
                 RuntimeAuthority._definition_digest((proposition,)))
    authority.register_epre_definition(v2, (proposition,))
    authority.retire_definition("epre", epre)
    return {"rejection": _reason(lambda: authority.validate_and_consume(request, permit)),
            "lifecycle": authority.permit(permit.permit_id).lifecycle.value,
            "v2_registered": True, "expected": "stale"}


def _policy_rotation() -> dict[str, Any]:
    authority, request, _, _, _, permit = _issued()
    primary_digest = authority.policy.digest
    alternate_binding = PolicyBinding(
        "eac-integrity-alternate", 2, authority.policy_binding.digest_sha256,
        authority.policy_binding.policy,
    )
    alternate = RuntimeAuthority(
        policy_binding=alternate_binding, profile_binding=authority.profile_binding,
        mode="authority", authority_nonce="tier1-alternate-policy-v2",
    )
    authority.retire_authority_semantics()
    return {"rejection": _reason(lambda: authority.validate_and_consume(request, permit)),
             "primary_policy_unchanged": authority.policy.digest == primary_digest,
             "alternate_policy_identity": alternate.policy.identity,
             "alternate_policy_version": alternate.policy.version, "expected": "stale"}


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
        Tier1Kind.POST_PERMIT_INVALIDATION: _stale_dependency,
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
            passed = evidence.get("rejection") == "stale" and evidence.get("effect_count") == 0
        elif kind is Tier1Kind.EPRE_REVISION:
            passed = (evidence.get("rejection") == "stale" and evidence.get("lifecycle") == "stale"
                      and evidence.get("v2_registered") is True)
        else:
            passed = (evidence.get("rejection") == "stale" and
                      evidence.get("primary_policy_unchanged") is True and
                      evidence.get("alternate_policy_identity") == "eac-integrity-alternate" and
                      evidence.get("alternate_policy_version") == 2)
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
