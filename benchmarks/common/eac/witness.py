"""Bounded, fail-closed evaluation of EAC evidence witnesses.

This module is intentionally an evidence evaluator only.  In particular it
does not issue permissions, infer authority, or mutate the supplied snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable, Mapping, Protocol, Sequence, runtime_checkable

from .canonical import canonical_bytes
from .model import (ActorScope, EPreAssessment, EvidenceRoot, EpistemicAdmissibility, EPreRef,
                    JustificationWitness, Proposition, PropositionKey,
                    ProvenanceRecord, SupportDerivation, WitnessValidity,
                    PolicyRef, ProfileRef)
from .policy import PolicyBinding, SourceProfileBinding, load_support_policy, policy_ref, profile_ref


@runtime_checkable
class EvidenceState(Protocol):
    """The read-only portion of runtime state required by this evaluator."""
    @property
    def roots(self) -> Iterable[EvidenceRoot]: ...
    @property
    def derivations(self) -> Iterable[SupportDerivation]: ...
    @property
    def provenance(self) -> Iterable[ProvenanceRecord]: ...


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    roots: tuple[EvidenceRoot, ...] = ()
    derivations: tuple[SupportDerivation, ...] = ()
    provenance: tuple[ProvenanceRecord, ...] = ()
    dependency_versions: tuple[tuple[str, int | str], ...] = ()
    authenticated_profile_digest: str | None = None
    revision_complete: bool = False


MAX_ROOTS = 4096
MAX_DERIVATIONS = 4096
MAX_PROVENANCE = 4096
MAX_DEPTH = 64
_SUFFICIENT = frozenset(("direct_observation", "trusted_tool_result", "visible_action_outcome"))


def _key(p: Proposition) -> tuple:
    return (p.key.namespace, p.key.predicate,
            canonical_bytes(list(p.key.arguments)).decode("utf-8"),
            p.key.temporal_scope, p.polarity)


def _same(a: Proposition, b: Proposition, opposite: bool = False) -> bool:
    return _key(a)[:4] == _key(b)[:4] and (
        a.polarity != b.polarity if opposite else a.polarity == b.polarity
    )


def _digest(value: object) -> str:
    try:
        return "witness:sha256:" + sha256(canonical_bytes(value)).hexdigest()
    except Exception:
        return "witness:invalid"


def _items(state: EvidenceState | Mapping[str, object]) -> tuple[tuple[EvidenceRoot, ...], tuple[SupportDerivation, ...], tuple[ProvenanceRecord, ...]]:
    def get(name: str) -> object:
        if isinstance(state, Mapping):
            return state.get(name, ())
        return getattr(state, name)
    roots, derivations, provenance = tuple(get("roots")), tuple(get("derivations")), tuple(get("provenance"))
    if len(roots) > MAX_ROOTS or len(derivations) > MAX_DERIVATIONS or len(provenance) > MAX_PROVENANCE:
        raise ValueError("evidence snapshot exceeds evaluation bounds")
    if any(not isinstance(x, EvidenceRoot) for x in roots) or any(not isinstance(x, SupportDerivation) for x in derivations):
        raise ValueError("unknown evidence item")
    if any(not isinstance(x, ProvenanceRecord) for x in provenance):
        raise ValueError("unknown provenance item")
    return roots, derivations, provenance


def _bindings(policy: PolicyBinding | None, profile: SourceProfileBinding | None):
    if policy is None:
        policy = load_support_policy()
    if not isinstance(policy, PolicyBinding) or policy.policy_id != "eac-primary-support" or policy.policy_version != 1:
        raise ValueError("unsupported policy")
    if not isinstance(profile, SourceProfileBinding):
        raise ValueError("a bound source profile is required")
    return policy, profile


def _visible(root: EvidenceRoot, actor: ActorScope) -> bool:
    return actor.actor_id in root.visible_to or "*" in root.visible_to


def _fresh(root: EvidenceRoot, roots: Sequence[EvidenceRoot]) -> bool:
    if not root.valid or not root.current:
        return False
    # Supersession is explicit and only applies to an identical tracked key.
    for newer in roots:
        if root.root_id in newer.supersedes and _same(newer.proposition, root.proposition) and newer.revision != root.revision:
            return False
    return True


def _allowed_rule(rule: str, profile: SourceProfileBinding) -> bool:
    return any(isinstance(x, Mapping) and x.get("rule_id") == rule for x in profile.profile["derivation_rules"])


def _rule_evaluator(rule: str, profile: SourceProfileBinding, evaluators):
    declaration = next((item for item in profile.profile["derivation_rules"]
                        if isinstance(item, Mapping) and item.get("rule_id") == rule), None)
    if declaration is None:
        return None
    identity = (rule, declaration["rule_version"], declaration["canonical_content_sha256"])
    return (evaluators or {}).get(identity)


def _witness(prop: Proposition, actor: ActorScope, roots: Sequence[EvidenceRoot], derivations: Sequence[SupportDerivation],
             provenance: Sequence[ProvenanceRecord], profile: SourceProfileBinding, *, include_defeat: bool,
             memo: dict[tuple, JustificationWitness | None], rule_evaluators=None,
             stack: frozenset[tuple] = frozenset(), depth: int = 0) -> JustificationWitness | None:
    ident = _key(prop)
    if ident in memo: return memo[ident]
    if depth > MAX_DEPTH or ident in stack:
        memo[ident] = None; return None
    provenance_ids = {item.provenance_id for item in provenance}
    sufficient = [r for r in roots if _same(r.proposition, prop) and r.root_type in _SUFFICIENT
                  and _visible(r, actor) and _fresh(r, roots) and r.provenance_id in provenance_ids]
    peer = [r for r in roots if _same(r.proposition, prop) and r.root_type == "unverified_peer_report" and _visible(r, actor) and _fresh(r, roots)]
    candidates: list[tuple[tuple[EvidenceRoot, ...], tuple[SupportDerivation, ...]]] = [(tuple([r]), ()) for r in sufficient]
    for d in derivations:
        if not _same(d.conclusion, prop) or not _allowed_rule(d.rule, profile) or not d.premises:
            continue
        branches: list[JustificationWitness] = []
        ok = True
        for pid in d.premises:
            matching = next((x for x in derivations if x.derivation_id == pid), None)
            root = next((x for x in roots if x.root_id == pid), None)
            if root is not None:
                branch = _root_witness(root, actor, roots, provenance)
            elif matching is not None and matching.derivation_id not in stack:
                branch = _derivation_witness(matching, actor, roots, derivations, provenance,
                                             profile, memo, rule_evaluators, stack | {ident, d.derivation_id}, depth + 1)
            else:
                branch = None
            if branch is None: ok = False; break
            branches.append(branch)
        evaluator = _rule_evaluator(d.rule, profile, rule_evaluators)
        if ok and evaluator is not None and evaluator(tuple(branch.proposition for branch in branches), d.conclusion) is True:
            if all(pid in {item.provenance_id for item in provenance} for pid in d.provenance):
                candidates.append((tuple(r for b in branches for r in b.roots), (d,) + tuple(x for b in branches for x in b.derivations)))
    result = None
    if candidates:
        rts, ders = min(candidates, key=lambda x: (tuple(r.root_id for r in x[0]), tuple(d.derivation_id for d in x[1])))
        # Peer reports are contextual only, and never promote repeated reports.
        contextual = tuple(p for p in peer if any(p.source_lineage_id != r.source_lineage_id and not p.upstream_origin_id == r.upstream_origin_id for r in rts))
        required_provenance = {r.provenance_id for r in rts if r.provenance_id}
        required_provenance.update(pid for d in ders for pid in d.provenance)
        prov = tuple(x for x in provenance if x.provenance_id in required_provenance)
        vals = ((WitnessValidity.SCOPED, True), (WitnessValidity.GROUNDED, True), (WitnessValidity.FRESH, True),
                (WitnessValidity.NON_DEFEATED, True), (WitnessValidity.SUPPORTS, True))
        result = JustificationWitness(_digest([_key(prop), [r.root_id for r in rts], [d.derivation_id for d in ders]]), prop, rts + contextual, ders, prov, vals,
                                      tuple(r.root_id for r in rts) + tuple(d.derivation_id for d in ders))
    memo[ident] = result
    return result


def _root_witness(root, actor, roots, provenance):
    if (root.root_type not in _SUFFICIENT or not _visible(root, actor) or not _fresh(root, roots)
            or root.provenance_id not in {item.provenance_id for item in provenance}):
        return None
    validity = tuple((dimension, True) for dimension in WitnessValidity)
    prov = tuple(item for item in provenance if item.provenance_id == root.provenance_id)
    return JustificationWitness(_digest([root.root_id, root.revision]), root.proposition,
                                (root,), (), prov, validity, (root.root_id,))


def _derivation_witness(derivation, actor, roots, derivations, provenance, profile, memo, rule_evaluators, stack, depth):
    if depth > MAX_DEPTH or not _allowed_rule(derivation.rule, profile) or derivation.derivation_id in stack:
        return None
    branches = []
    for premise in derivation.premises:
        root = next((item for item in roots if item.root_id == premise), None)
        child = next((item for item in derivations if item.derivation_id == premise), None)
        branch = (_root_witness(root, actor, roots, provenance) if root is not None else
                  _derivation_witness(child, actor, roots, derivations, provenance, profile,
                                      memo, rule_evaluators, stack | {derivation.derivation_id}, depth + 1)
                  if child is not None else None)
        if branch is None:
            return None
        branches.append(branch)
    evaluator = _rule_evaluator(derivation.rule, profile, rule_evaluators)
    if evaluator is None or evaluator(tuple(branch.proposition for branch in branches), derivation.conclusion) is not True:
        return None
    if not all(pid in {item.provenance_id for item in provenance} for pid in derivation.provenance):
        return None
    roots_used = tuple(root for branch in branches for root in branch.roots)
    derivations_used = (derivation,) + tuple(item for branch in branches for item in branch.derivations)
    provenance_ids = {root.provenance_id for root in roots_used} | set(derivation.provenance)
    prov = tuple(item for item in provenance if item.provenance_id in provenance_ids)
    validity = tuple((dimension, True) for dimension in WitnessValidity)
    return JustificationWitness(_digest([derivation.derivation_id, [root.root_id for root in roots_used]]),
                                derivation.conclusion, roots_used, derivations_used, prov, validity,
                                tuple(root.root_id for root in roots_used) + tuple(item.derivation_id for item in derivations_used))


def _assessment(prop, actor, roots, derivations, provenance, profile, witness, opposite, rule_evaluators):
    relevant_roots = tuple(root for root in roots if _same(root.proposition, prop)
                           and root.root_type in _SUFFICIENT)
    relevant_derivations = tuple(item for item in derivations if _same(item.conclusion, prop)
                                 and _allowed_rule(item.rule, profile))
    provenance_ids = {item.provenance_id for item in provenance}
    has_declared_support = bool(relevant_roots or relevant_derivations)
    scoped_roots = tuple(root for root in relevant_roots if _visible(root, actor))
    grounded_roots = tuple(root for root in relevant_roots if root.provenance_id in provenance_ids)
    fresh_roots = tuple(root for root in relevant_roots if _fresh(root, roots))
    root_grounded = bool(grounded_roots)
    derivation_grounded = False
    for derivation in relevant_derivations:
        derivation_grounded = (_derivation_witness(
            derivation, actor, roots, derivations, provenance, profile, {}, rule_evaluators,
            frozenset(), 0) is not None)
        if derivation_grounded:
            break
    scoped = (bool(scoped_roots) or derivation_grounded) if has_declared_support else False
    grounded = root_grounded or derivation_grounded
    fresh = bool(fresh_roots) or derivation_grounded
    non_defeated = opposite is None
    supports = witness is not None
    validity = ((WitnessValidity.SCOPED, scoped), (WitnessValidity.GROUNDED, grounded),
                (WitnessValidity.FRESH, fresh), (WitnessValidity.NON_DEFEATED, non_defeated),
                (WitnessValidity.SUPPORTS, supports))
    reasons = []
    recoveries = []
    if not has_declared_support:
        reasons.append("supports.no_allowed_path"); recoveries.append("gather_observation")
    elif not scoped:
        reasons.append("scoped.invisible"); recoveries.append("gather_observation")
    if has_declared_support and not grounded:
        reasons.append("grounded.no_finite_path"); recoveries.append("gather_observation")
    if has_declared_support and not fresh:
        reasons.append("fresh.stale_or_superseded"); recoveries.append("wait_for_evidence")
    if not non_defeated:
        reasons.append("non_defeated.conflict"); recoveries.append("request_clarification")
    if has_declared_support and not supports:
        reasons.append("supports.no_allowed_path"); recoveries.append("gather_observation")
    if not reasons and witness is None:
        reasons.append("supports.no_valid_combined_path"); recoveries.append("gather_observation")
    dependencies = tuple(dict.fromkeys(
        [root.root_id for root in relevant_roots]
        + [item.derivation_id for item in relevant_derivations]
    ))[:128]
    admissible = witness is not None and all(value for unused_dimension, value in validity)
    return EPreAssessment(prop, admissible, validity, witness.witness_id if witness else None,
                          dependencies, tuple(reasons), tuple(dict.fromkeys(recoveries)))


def evaluate_epistemic_admissibility(actor: ActorScope, propositions: Iterable[Proposition], state: EvidenceState | Mapping[str, object],
                                     policy: PolicyBinding | None = None, profile: SourceProfileBinding | None = None,
                                     *, epre: EPreRef | None = None, rule_evaluators=None,
                                     forbidden_support_action: str | None = None) -> EpistemicAdmissibility:
    """Evaluate every declared EPre proposition, without selecting among conflicts."""
    try:
        policy, profile = _bindings(policy, profile)
        roots, derivations, provenance = _items(state)
        if forbidden_support_action is not None:
            roots = tuple(root for root in roots if not (
                root.originating_action_identity == forbidden_support_action
                and not root.evidence_gathering_action))
        props = tuple(propositions)
        if any(not isinstance(p, Proposition) for p in props): raise ValueError("unknown proposition")
        witnesses: list[JustificationWitness] = []; assessments: list[EPreAssessment] = []
        for prop in props:
            memo: dict[tuple, JustificationWitness | None] = {}
            witness = _witness(prop, actor, roots, derivations, provenance, profile, include_defeat=False, memo=memo, rule_evaluators=rule_evaluators)
            opposite = _witness(Proposition(prop.key, not prop.polarity), actor, roots, derivations, provenance, profile, include_defeat=False, memo=memo, rule_evaluators=rule_evaluators)
            assessment = _assessment(prop, actor, roots, derivations, provenance, profile,
                                     witness, opposite, rule_evaluators)
            if not assessment.admissible:
                witness = None
            if witness:
                witnesses.append(witness)
            assessments.append(assessment)
        reasons = tuple(dict.fromkeys(reason for item in assessments for reason in item.reasons))
        recoveries = tuple(dict.fromkeys(recovery for item in assessments for recovery in item.recoveries))
        explicitly_classified = bool(props) or epre is not None
        if not explicitly_classified:
            reasons = ("classification.missing_epre",)
        return EpistemicAdmissibility(
            explicitly_classified and len(witnesses) == len(props) and all(item.admissible for item in assessments),
            tuple(witnesses), reasons, recoveries, policy_ref(policy), profile_ref(profile),
            tuple(assessments))
    except Exception as exc:
        return EpistemicAdmissibility(False, (), (f"fail_closed:{type(exc).__name__}",), (), None, None)


evaluate = evaluate_epistemic_admissibility
