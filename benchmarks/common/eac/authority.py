"""Environment-independent, in-process reference EAC Runtime Authority."""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
from hashlib import sha256
from secrets import token_hex
from threading import RLock
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Protocol

from .canonical import FrozenJSONArray, FrozenJSONObject, canonical_argument, canonical_bytes, thaw_json
from .model import (
    ActionRef, ActorScope, AttemptRecord, AuditRecord, CandidateLifecycle, DependencyExpectation,
    DependencyManifest, EPreRef, EpistemicAdmissibility, EvidenceRoot, ExactRequest,
    FencingToken, PermitLifecycle, PermitView, PolicyRef, ProfileRef, Proposition,
    ProvenanceRecord, RejectionReason, SupportDerivation, NativeEffectResult,
)
from .witness import EvidenceSnapshot, evaluate_epistemic_admissibility
from .policy import match_mapping


class AuthorityError(RuntimeError):
    def __init__(self, reason: RejectionReason | str):
        self.reason = reason.value if isinstance(reason, RejectionReason) else str(reason)
        super().__init__(self.reason)


class AuthorityStateReader(Protocol):
    """Optional adapter over an environment-owned evidence store."""
    def evidence_snapshot(self, actor: ActorScope) -> EvidenceSnapshot: ...


class CandidateStateWriter(Protocol):
    def record_candidate_state(self, candidate_id: str, state: CandidateLifecycle) -> None: ...


@dataclass
class _Candidate:
    request: ExactRequest
    actor: ActorScope
    epre_ref: EPreRef
    epre: tuple[Proposition, ...]
    env_pre: tuple[Proposition, ...]
    sec_pre: tuple[str, ...]
    capability_dependencies: tuple[str, ...]
    lifecycle: CandidateLifecycle = CandidateLifecycle.PROPOSED
    evaluation: EpistemicAdmissibility | None = None
    manifest: DependencyManifest | None = None
    permit_id: str | None = None
    permit_state: PermitLifecycle = PermitLifecycle.NONE


@dataclass
class _PermitRecord:
    permit_id: str
    candidate_id: str
    request: ExactRequest
    manifest: DependencyManifest
    lifecycle: PermitLifecycle = PermitLifecycle.ISSUED


@dataclass
class _TokenState:
    permit_id: str
    candidate_id: str
    request_digest: str
    expectations: tuple[DependencyExpectation, ...]
    fence: int
    admitted: bool = False


def _plain(value: Any) -> Any:
    if isinstance(value, (FrozenJSONArray, FrozenJSONObject)):
        return thaw_json(value)
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _audit_value(value: Any) -> Any:
    plain = _plain(value)
    if isinstance(plain, dict):
        return tuple((key, _audit_value(item)) for key, item in sorted(plain.items()))
    if isinstance(plain, list):
        return tuple(_audit_value(item) for item in plain)
    return plain


def _digest(value: Any) -> str:
    return "sha256:" + sha256(canonical_bytes(_plain(value))).hexdigest()


def _proposition_slot(proposition: Proposition, polarity: bool | None = None) -> str:
    key = proposition.key
    payload = [key.namespace, key.predicate, list(key.arguments), key.temporal_scope]
    return "conflict:" + _digest(payload)


def _binding_slot(kind: str, reference) -> str:
    payload = [kind, reference.identity, reference.version, reference.digest]
    return f"{kind}-binding:" + sha256(canonical_bytes(payload)).hexdigest()


def _actor_scope_binding(actor: ActorScope) -> str:
    payload = [actor.actor_id, actor.visibility_revision, list(actor.scope)]
    return "actor-scope-binding:" + sha256(canonical_bytes(payload)).hexdigest()


def _revision_map(items: Iterable[tuple[str, int | str]]) -> dict[str, int | str]:
    result: dict[str, int | str] = {}
    if not isinstance(items, (tuple, list)):
        raise ValueError("dependency revisions must be a sequence")
    for item in items:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError("dependency revision entry must be an ID/revision pair")
        dependency_id, revision = item
        if (not isinstance(dependency_id, str) or not dependency_id or dependency_id in result
                or any("\ud800" <= char <= "\udfff" for char in dependency_id)):
            raise ValueError("dependency revision IDs must be unique non-empty strings")
        if (not isinstance(revision, (int, str)) or isinstance(revision, bool)
                or (isinstance(revision, int) and revision < 0)
                or (isinstance(revision, str) and (not revision or any(
                    "\ud800" <= char <= "\udfff" for char in revision)))):
            raise ValueError("dependency revision must be an unambiguous integer or string")
        result[dependency_id] = revision
    return result


class RuntimeAuthority:
    """Lock-serialized reference authority for one immutable run/mode.

    The implementation is a software trust boundary, not an OS sandbox.  Every
    supported native effect must remain behind :class:`EffectGateway`.
    """

    def __init__(self, *, policy_binding, profile_binding, mode: str = "authority",
                 state_reader: AuthorityStateReader | None = None,
                 candidate_writer: CandidateStateWriter | None = None,
                 audit_limit: int = 1024, authority_nonce: str | None = None,
                 source_authenticator=None, rule_evaluators=None):
        if mode not in {"authority", "advisory"}:
            raise ValueError("mode must be authority or advisory")
        if not getattr(policy_binding, "digest_sha256", None) or not getattr(profile_binding, "digest_sha256", None):
            raise ValueError("authenticated policy and SourceProfile are required")
        self._mode = mode
        self.policy_binding = policy_binding
        self.profile_binding = profile_binding
        self.policy = PolicyRef(policy_binding.policy_id, policy_binding.policy_version,
                                policy_binding.digest_sha256)
        self.profile = ProfileRef(profile_binding.profile_id, profile_binding.profile_version,
                                  profile_binding.digest_sha256)
        self._reader, self._writer = state_reader, candidate_writer
        self._source_authenticator = source_authenticator
        self._rule_evaluators = MappingProxyType(dict(rule_evaluators or {}))
        self._lock = RLock()
        self._epoch = 0
        self._nonce = authority_nonce or token_hex(16)
        self._fence = 0
        self._dependency_versions: dict[str, int] = {}
        self._roots: dict[str, EvidenceRoot] = {}
        self._derivations: dict[str, SupportDerivation] = {}
        self._provenance: dict[str, ProvenanceRecord] = {}
        self._candidates: dict[str, _Candidate] = {}
        self._permits: dict[str, _PermitRecord] = {}
        self._tokens: dict[bytes, _TokenState] = {}
        self._attempts: dict[str, AttemptRecord] = {}
        self._audit: deque[AuditRecord] = deque(maxlen=max(1, min(audit_limit, 4096)))
        self._sequence = 0
        self._reserved_attempt_ids: set[str] = set()
        self._effect_active = False
        self._callback_active = 0
        self._action_definitions: dict[tuple[str, int | str, str], Any] = {}
        self._epre_definitions: dict[tuple[str, int | str, str], tuple[Proposition, ...]] = {}
        self._retired_bindings: set[str] = set()

    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    @property
    def mode(self) -> str:
        return self._mode

    def _version(self, dependency_id: str) -> int:
        return self._dependency_versions.get(dependency_id, 0)

    def _ensure_mutable(self) -> None:
        if self._effect_active or self._callback_active:
            raise AuthorityError("mutation_during_effect")

    @contextmanager
    def _callback_boundary(self):
        self._callback_active += 1
        try:
            yield
        finally:
            self._callback_active -= 1

    @staticmethod
    def _definition_digest(value: Any) -> str:
        return sha256(canonical_bytes(_plain(value))).hexdigest()

    def register_action_definition(self, action: ActionRef, definition: Any) -> None:
        canonical_argument(_plain(definition))
        if action.digest != self._definition_digest(definition):
            raise ValueError("action definition digest mismatch")
        with self._lock:
            self._ensure_mutable()
            key = (action.identity, action.version, action.digest)
            version_key = (action.identity, action.version)
            if any(existing[:2] == version_key and existing != key for existing in self._action_definitions):
                raise ValueError("action identity/version already binds another digest")
            if key in self._action_definitions:
                return
            self._action_definitions[key] = canonical_argument(_plain(definition))

    def register_epre_definition(self, epre: EPreRef, propositions: Iterable[Proposition]) -> None:
        declared = tuple(propositions)
        if any(not isinstance(item, Proposition) for item in declared):
            raise ValueError("EPre definition requires propositions")
        if epre.digest != self._definition_digest(declared):
            raise ValueError("EPre definition digest mismatch")
        with self._lock:
            self._ensure_mutable()
            key = (epre.identity, epre.version, epre.digest)
            version_key = (epre.identity, epre.version)
            if any(existing[:2] == version_key and existing != key for existing in self._epre_definitions):
                raise ValueError("EPre identity/version already binds another digest")
            if key in self._epre_definitions:
                return
            self._epre_definitions[key] = declared

    def retire_definition(self, kind: str, reference: ActionRef | EPreRef) -> None:
        if kind not in {"action", "epre"}:
            raise ValueError("definition kind must be action or epre")
        with self._lock:
            self._ensure_mutable()
            slot = _binding_slot(kind, reference)
            registry = self._action_definitions if kind == "action" else self._epre_definitions
            if (reference.identity, reference.version, reference.digest) not in registry:
                raise ValueError("unknown semantic binding")
            if slot in self._retired_bindings:
                return
            self._retired_bindings.add(slot)
            self._bump((slot,))

    def retire_authority_semantics(self) -> None:
        """Permanently obsolete this authority's immutable policy/profile bindings."""
        with self._lock:
            self._ensure_mutable()
            slots = {_binding_slot("policy", self.policy), _binding_slot("profile", self.profile)}
            new_slots = slots - self._retired_bindings
            if not new_slots:
                return
            self._retired_bindings.update(new_slots)
            self._bump(new_slots)

    def retire_actor_scope(self, actor: ActorScope) -> None:
        """Obsolete one exact actor visibility/scope binding for future issuance."""
        if not isinstance(actor, ActorScope):
            raise ValueError("typed actor scope required")
        with self._lock:
            self._ensure_mutable()
            slot = _actor_scope_binding(actor)
            if slot in self._retired_bindings:
                return
            self._retired_bindings.add(slot)
            self._bump((slot,))

    def _candidate_bindings_current(self, candidate: _Candidate) -> bool:
        return (_binding_slot("action", candidate.request.action) not in self._retired_bindings
                and _binding_slot("epre", candidate.epre_ref) not in self._retired_bindings
                and _actor_scope_binding(candidate.actor) not in self._retired_bindings
                and _binding_slot("policy", self.policy) not in self._retired_bindings
                and _binding_slot("profile", self.profile) not in self._retired_bindings
                and self.policy == PolicyRef(self.policy_binding.policy_id, self.policy_binding.policy_version,
                                             self.policy_binding.digest_sha256)
                and self.profile == ProfileRef(self.profile_binding.profile_id, self.profile_binding.profile_version,
                                               self.profile_binding.digest_sha256))

    def _bump(self, dependency_ids: Iterable[str]) -> set[str]:
        self._ensure_mutable()
        changed = {str(item) for item in dependency_ids}
        if not changed:
            return changed
        self._epoch += 1
        for dependency_id in changed:
            self._dependency_versions[dependency_id] = self._version(dependency_id) + 1
        self._stale_affected(changed)
        return changed

    def _record(self, event: str, candidate: _Candidate, **details: Any) -> None:
        self._sequence += 1
        bounded = tuple((str(key), _audit_value(value)) for key, value in list(details.items())[:32])
        dependencies = tuple(item.dependency_id for item in
                             (candidate.manifest.expectations if candidate.manifest else ()))[:128]
        self._audit.append(AuditRecord(token_hex(12), event, candidate.request.candidate_id,
                                       candidate.request.attempt_id, candidate.permit_id,
                                       dependencies, bounded, self._sequence))

    def _write_state(self, candidate: _Candidate) -> None:
        if self._writer is not None:
            with self._callback_boundary():
                self._writer.record_candidate_state(candidate.request.candidate_id, candidate.lifecycle)

    def register_candidate(self, request: ExactRequest, *, actor: ActorScope,
                           epre_ref: EPreRef, epre: Iterable[Proposition],
                           env_pre: Iterable[Proposition] = (),
                           sec_pre: Iterable[str] = (),
                           capability_dependencies: Iterable[str] = ()) -> ExactRequest:
        if not isinstance(request, ExactRequest) or not isinstance(actor, ActorScope) or not isinstance(epre_ref, EPreRef):
            raise ValueError("typed request, actor scope, and EPre definition are required")
        if request.action.digest is None or epre_ref.digest is None:
            raise ValueError("action and EPre content digests are required")
        # Deep canonical validation prevents mutable caller input from changing a request.
        canonical_argument(_plain(request))
        declared, environmental = tuple(epre), tuple(env_pre)
        if any(not isinstance(item, Proposition) for item in declared + environmental):
            raise ValueError("preconditions must be typed propositions")
        with self._lock:
            self._ensure_mutable()
            action_key = (request.action.identity, request.action.version, request.action.digest)
            epre_key = (epre_ref.identity, epre_ref.version, epre_ref.digest)
            if action_key not in self._action_definitions:
                raise ValueError("unknown authenticated action definition")
            if self._epre_definitions.get(epre_key) != declared:
                raise ValueError("unknown or mismatched EPre definition")
            if request.candidate_id in self._candidates:
                raise ValueError("candidate already registered")
            if request.attempt_id in self._reserved_attempt_ids:
                raise ValueError("attempt identity already registered")
            self._reserved_attempt_ids.add(request.attempt_id)
            candidate = _Candidate(request, actor, epre_ref, declared, environmental,
                                   tuple(str(item) for item in sec_pre),
                                   tuple(str(item) for item in capability_dependencies))
            self._candidates[request.candidate_id] = candidate
            self._record("candidate_proposed", candidate, epre=_plain(declared), env_pre=_plain(environmental))
            self._write_state(candidate)
            return request

    def _snapshot(self, actor: ActorScope) -> EvidenceSnapshot:
        if self._reader is not None:
            with self._callback_boundary():
                snapshot = self._reader.evidence_snapshot(actor)
            if not isinstance(snapshot, EvidenceSnapshot):
                raise ValueError("store returned invalid evidence snapshot")
            if (snapshot.authenticated_profile_digest != self.profile.digest
                    or snapshot.revision_complete is not True):
                raise ValueError("store snapshot is not authenticated and revision-complete")
            versions = _revision_map(snapshot.dependency_versions)
            required = {
                *("evidence:" + item.root_id for item in snapshot.roots),
                *("derivation:" + item.derivation_id for item in snapshot.derivations),
                *("provenance:" + item.provenance_id for item in snapshot.provenance),
            }
            if not required.issubset(versions):
                raise ValueError("store snapshot omits dependency revisions")
            allowed_rules = {item["rule_id"] for item in self.profile_binding.profile["mapping_rules"]}
            if any(root.mapping_rule_id not in allowed_rules for root in snapshot.roots):
                raise ValueError("store snapshot contains unclassified evidence")
            return snapshot
        return EvidenceSnapshot(tuple(self._roots.values()), tuple(self._derivations.values()),
                                tuple(self._provenance.values()))

    def put_provenance(self, record: ProvenanceRecord) -> None:
        if not isinstance(record, ProvenanceRecord):
            raise ValueError("typed provenance required")
        with self._lock:
            self._ensure_mutable()
            old = self._provenance.get(record.provenance_id)
            if old == record:
                return
            self._provenance[record.provenance_id] = record
            self._bump(("provenance:" + record.provenance_id,))

    def _put_classified_root(self, root: EvidenceRoot) -> None:
        if not isinstance(root, EvidenceRoot):
            raise ValueError("typed evidence root required")
        if root.mapping_rule_id is None:
            raise ValueError("evidence must be SourceProfile-classified")
        with self._lock:
            self._ensure_mutable()
            old = self._roots.get(root.root_id)
            if old == root:
                return
            changed = {"evidence:" + root.root_id, _proposition_slot(root.proposition)}
            if root.provenance_id:
                changed.add("provenance:" + root.provenance_id)
            replacements = []
            for old_id in root.supersedes:
                previous = self._roots.get(old_id)
                if (previous is not None and previous.proposition.key == root.proposition.key
                        and previous.source_stream_id == root.source_stream_id
                        and root.source_stream_id is not None
                        and root.source_stream_revision is not None
                        and previous.source_stream_revision is not None
                        and root.source_stream_revision > previous.source_stream_revision
                        and root.issuer is not None):
                    replacements.append((old_id, replace(previous, current=False)))
                    changed.add("evidence:" + old_id)
                else:
                    raise ValueError("unauthorized or non-monotonic supersession")
            self._roots[root.root_id] = root
            for old_id, replacement in replacements:
                self._roots[old_id] = replacement
            self._bump(changed)

    def put_root(self, root: EvidenceRoot) -> None:
        raise ValueError("direct classified-root insertion is unavailable; use ingest_record")

    def ingest_record(self, record: Mapping[str, Any], *, proposition: Proposition,
                      root_id: str, revision: int, provenance_id: str,
                      supersedes: tuple[str, ...] = ()) -> EvidenceRoot:
        rule = match_mapping(record, self.profile_binding)
        if record.get("proposition") != _plain(proposition):
            raise ValueError("record proposition binding mismatch")
        if proposition.key.namespace != rule["record_namespace"]:
            raise ValueError("record mapping does not authorize proposition namespace")
        with self._lock, self._callback_boundary():
            if self._source_authenticator is None or self._source_authenticator(
                    record, proposition, self.profile_binding, rule) is not True:
                raise ValueError("record source authentication failed")
        visible = record[rule["visibility_field"]]
        if not isinstance(visible, (tuple, list)) or not visible or any(not isinstance(x, str) for x in visible):
            raise ValueError("invalid actor visibility")
        stream_revision = None
        if record.get("source_stream_id") is not None:
            stream = next(item for item in self.profile_binding.profile["supersession_streams"]
                          if item["source_stream_id"] == record["source_stream_id"])
            stream_revision = record[stream["revision_field"]]
        root = EvidenceRoot(
            root_id, rule["root_type"], proposition, str(record.get("source", record.get("issuer", "source"))),
            revision, tuple(visible), provenance_id, tuple(supersedes),
            str(record[rule["source_lineage_field"]]), str(record[rule["upstream_origin_field"]]),
            True, True, record.get("source_stream_id"), stream_revision,
            record.get("issuer"), rule["rule_id"],
            record.get("originating_action_identity"),
            record.get("evidence_gathering_action") is True,
        )
        self._put_classified_root(root)
        return root

    def remove_root(self, root_id: str) -> None:
        with self._lock:
            self._ensure_mutable()
            root = self._roots.pop(root_id, None)
            if root is not None:
                self._bump(("evidence:" + root_id, _proposition_slot(root.proposition)))

    def put_derivation(self, derivation: SupportDerivation) -> None:
        if not isinstance(derivation, SupportDerivation):
            raise ValueError("typed derivation required")
        with self._lock:
            self._ensure_mutable()
            old = self._derivations.get(derivation.derivation_id)
            if old == derivation:
                return
            self._derivations[derivation.derivation_id] = derivation
            self._bump(("derivation:" + derivation.derivation_id,
                        _proposition_slot(derivation.conclusion)))

    def mutate_dependencies(self, dependency_ids: Iterable[str], *, reason: str) -> None:
        with self._lock:
            self._ensure_mutable()
            changed = self._bump(dependency_ids)
            for candidate in self._candidates.values():
                if changed.intersection(self._candidate_dependency_ids(candidate)):
                    self._record("dependency_changed", candidate, reason=reason, changed=tuple(sorted(changed)))

    def _candidate_dependency_ids(self, candidate: _Candidate) -> set[str]:
        if candidate.manifest is None:
            return set()
        return {item.dependency_id for item in candidate.manifest.expectations}

    def _stale_affected(self, changed: set[str]) -> None:
        for candidate in self._candidates.values():
            if not changed.intersection(self._candidate_dependency_ids(candidate)):
                continue
            if candidate.permit_state is PermitLifecycle.ISSUED:
                candidate.permit_state = PermitLifecycle.STALE
                if candidate.permit_id:
                    self._permits[candidate.permit_id].lifecycle = PermitLifecycle.STALE
                self._record("permit_stale", candidate, changed=tuple(sorted(changed)))
            candidate.lifecycle = CandidateLifecycle.BLOCKED_PRECONDITION
            self._write_state(candidate)

    def _manifest(self, candidate: _Candidate, decision: EpistemicAdmissibility,
                  snapshot: EvidenceSnapshot) -> DependencyManifest:
        dependency_ids = {
            _binding_slot("action", candidate.request.action),
            _binding_slot("epre", candidate.epre_ref),
            _actor_scope_binding(candidate.actor),
            "scope:" + candidate.actor.actor_id,
            _binding_slot("policy", self.policy),
            _binding_slot("profile", self.profile),
            *("capability:" + item for item in candidate.capability_dependencies),
            *("sec_pre:" + item for item in candidate.sec_pre),
        }
        for proposition in candidate.epre:
            dependency_ids.add(_proposition_slot(proposition))
        for witness in decision.witnesses:
            dependency_ids.update("evidence:" + root.root_id for root in witness.roots)
            dependency_ids.update("derivation:" + item.derivation_id for item in witness.derivations)
            for provenance in witness.provenance:
                dependency_ids.add("provenance:" + provenance.provenance_id)
        snapshot_versions = _revision_map(snapshot.dependency_versions)
        external_ids = set(dependency_ids).intersection(snapshot_versions)
        for dependency_id in external_ids:
            revision = snapshot_versions[dependency_id]
            self._dependency_versions[dependency_id] = revision
        if self._reader is not None:
            scope_watch = "scope:" + candidate.actor.actor_id
            if scope_watch not in snapshot_versions:
                raise ValueError("external snapshot omits actor scope watch")
            dependency_ids.add(scope_watch)
            external_ids.add(scope_watch)
            for proposition in candidate.epre:
                watch = _proposition_slot(proposition)
                if watch not in external_ids:
                    raise ValueError("external snapshot omits proposition conflict watch")
        expectations = tuple(DependencyExpectation(item, self._version(item),
                                                    "external" if item in external_ids else item.split(":", 1)[0])
                             for item in sorted(dependency_ids))
        manifest = DependencyManifest(
            candidate.request, candidate.actor, expectations, candidate.request.action,
            candidate.epre_ref, self.policy, self.profile, None, candidate.epre,
            tuple(witness.witness_id for witness in decision.witnesses),
            max((item.revision for item in expectations if type(item.revision) is int), default=0),
            self._nonce,
        )
        return replace(manifest, fingerprint=_digest(manifest))

    def evaluate(self, candidate_id: str) -> EpistemicAdmissibility:
        with self._lock:
            self._ensure_mutable()
            candidate = self._candidates[candidate_id]
            if not self._candidate_bindings_current(candidate):
                candidate.lifecycle = CandidateLifecycle.BLOCKED_PRECONDITION
                raise AuthorityError("semantic_binding_retired")
            snapshot = self._snapshot(candidate.actor)
            with self._callback_boundary():
                decision = evaluate_epistemic_admissibility(
                    candidate.actor, candidate.epre, snapshot,
                    self.policy_binding, self.profile_binding, epre=candidate.epre_ref,
                    rule_evaluators=self._rule_evaluators,
                    forbidden_support_action=candidate.request.action.identity,
                )
            candidate.evaluation = decision
            candidate.lifecycle = (CandidateLifecycle.EPISTEMICALLY_ADMISSIBLE if decision.admissible
                                   else CandidateLifecycle.BLOCKED_CONFLICT if "non_defeated.conflict" in decision.reasons
                                   else CandidateLifecycle.WAITING_FOR_EVIDENCE)
            previous_fingerprint = candidate.manifest.fingerprint if candidate.manifest else None
            candidate.manifest = self._manifest(candidate, decision, snapshot)
            if candidate.permit_state is PermitLifecycle.ISSUED and candidate.permit_id and (
                not decision.admissible or previous_fingerprint != candidate.manifest.fingerprint
            ):
                candidate.permit_state = PermitLifecycle.STALE
                self._permits[candidate.permit_id].lifecycle = PermitLifecycle.STALE
                self._record("permit_stale", candidate, reason="reevaluation")
            self._record("epre_evaluated", candidate, admissible=decision.admissible,
                         validity=[assessment.validity for assessment in decision.assessments],
                         assessments=tuple((assessment.proposition, assessment.admissible,
                                            assessment.validity, assessment.reasons,
                                            assessment.dependencies, assessment.recoveries)
                                           for assessment in decision.assessments),
                         reasons=decision.reasons, recoveries=decision.recoveries,
                         fingerprint=candidate.manifest.fingerprint,
                         witnesses=tuple((w.witness_id, tuple(r.root_id for r in w.roots),
                                          tuple(d.derivation_id for d in w.derivations))
                                         for w in decision.witnesses),
                         policy=self.policy, profile=self.profile)
            self._write_state(candidate)
            return decision

    def issue_permit(self, candidate_id: str) -> PermitView:
        with self._lock:
            self._ensure_mutable()
            candidate = self._candidates[candidate_id]
            decision = self.evaluate(candidate_id)
            if not decision.admissible or candidate.manifest is None:
                self._record("permit_rejected", candidate, reason=RejectionReason.NOT_ADMISSIBLE.value)
                raise AuthorityError(RejectionReason.NOT_ADMISSIBLE)
            if candidate.permit_id and candidate.permit_state is PermitLifecycle.ISSUED:
                candidate.permit_state = PermitLifecycle.REVOKED
                self._permits[candidate.permit_id].lifecycle = PermitLifecycle.REVOKED
                self._record("permit_revoked", candidate, reason="replacement")
            if candidate.permit_state is PermitLifecycle.CONSUMED:
                raise AuthorityError("new_attempt_identity_required")
            candidate.permit_id = token_hex(24)
            candidate.permit_state = PermitLifecycle.ISSUED
            self._permits[candidate.permit_id] = _PermitRecord(
                candidate.permit_id, candidate.request.candidate_id,
                candidate.request, candidate.manifest,
            )
            self._record("permit_issued", candidate, fingerprint=candidate.manifest.fingerprint)
            return self.permit(candidate.permit_id)

    def shadow_permit(self, candidate_id: str) -> PermitView:
        """Advisory-only, non-consumable permit projection using the identical pre-gate."""
        with self._lock:
            candidate = self._candidates[candidate_id]
            self.evaluate(candidate_id)
            if candidate.manifest is None:
                raise AuthorityError(RejectionReason.NOT_ADMISSIBLE)
            return PermitView("shadow:" + candidate.request.attempt_id, candidate.request,
                              PermitLifecycle.NONE, candidate.manifest.fingerprint or "",
                              "advisory", candidate.manifest)

    def permit(self, permit_id: str) -> PermitView:
        record = self._permits[permit_id]
        return PermitView(permit_id, record.request, record.lifecycle,
                          record.manifest.fingerprint or "", self.mode, record.manifest)

    def _expectations_fresh(self, expectations: Iterable[DependencyExpectation]) -> bool:
        return all(item.dependency_id not in self._retired_bindings
                   and self._version(item.dependency_id) == item.revision for item in expectations)

    def _external_expectations_fresh(self, candidate: _Candidate,
                                     expectations: Iterable[DependencyExpectation]) -> bool:
        if self._reader is None:
            return True
        with self._callback_boundary():
            snapshot = self._reader.evidence_snapshot(candidate.actor)
        if (not isinstance(snapshot, EvidenceSnapshot)
                or snapshot.authenticated_profile_digest != self.profile.digest
                or snapshot.revision_complete is not True):
            return False
        try:
            observed = _revision_map(snapshot.dependency_versions)
        except ValueError:
            return False
        return all(observed.get(item.dependency_id) == item.revision
                   for item in expectations if item.kind == "external")

    def shadow_fresh(self, request: ExactRequest, shadow: PermitView) -> bool:
        """Recheck Advisory's shadow manifest without enforcing a permit gate."""
        with self._lock:
            candidate = self._candidates.get(request.candidate_id)
            if (candidate is None or request != candidate.request or shadow.request != request
                    or shadow.manifest is None or shadow.fingerprint != shadow.manifest.fingerprint):
                return False
            return (self._expectations_fresh(shadow.manifest.expectations)
                    and self._external_expectations_fresh(candidate, shadow.manifest.expectations))

    def validate_and_consume(self, request: ExactRequest, permit: PermitView | str) -> FencingToken:
        with self._lock:
            self._ensure_mutable()
            permit_id = permit.permit_id if isinstance(permit, PermitView) else str(permit)
            record = self._permits.get(permit_id)
            if record is None:
                raise AuthorityError(RejectionReason.STALE)
            candidate = self._candidates[record.candidate_id]
            if request != record.request:
                self._record("permit_rejected", candidate, reason=RejectionReason.MISMATCH.value)
                raise AuthorityError(RejectionReason.MISMATCH)
            if record.lifecycle is PermitLifecycle.CONSUMED:
                raise AuthorityError(RejectionReason.REPLAY)
            if record.lifecycle is PermitLifecycle.REVOKED:
                raise AuthorityError(RejectionReason.REVOKED)
            if (record.lifecycle is not PermitLifecycle.ISSUED
                    or not self._expectations_fresh(record.manifest.expectations)
                    or not self._external_expectations_fresh(candidate, record.manifest.expectations)):
                record.lifecycle = PermitLifecycle.STALE
                raise AuthorityError(RejectionReason.STALE)
            if (record.lifecycle is not PermitLifecycle.ISSUED
                    or not self._expectations_fresh(record.manifest.expectations)):
                raise AuthorityError(RejectionReason.STALE)
            if isinstance(permit, PermitView) and (permit.fingerprint != record.manifest.fingerprint or permit.request != request):
                raise AuthorityError(RejectionReason.MISMATCH)
            record.lifecycle = PermitLifecycle.CONSUMED
            if candidate.permit_id == permit_id:
                candidate.permit_state = PermitLifecycle.CONSUMED
            self._fence += 1
            token = FencingToken._issue()
            self._tokens[token._bytes()] = _TokenState(
                permit_id, request.candidate_id, _digest(request),
                record.manifest.expectations, self._fence,
            )
            self._attempts[request.attempt_id] = AttemptRecord(
                request.attempt_id, permit_id, "precheck", fence=self._fence,
                request_digest=_digest(request),
                manifest_fingerprint=record.manifest.fingerprint,
            )
            self._record("permit_consumed", candidate, fence=self._fence)
            return token

    def reject_pre_effect(self, token: FencingToken, reason: str, *,
                          env_pre_result: str | None = None,
                          sec_pre_result: str | None = None) -> None:
        with self._lock:
            state, candidate = self._token_candidate(token)
            del self._tokens[token._bytes()]
            self._attempts[candidate.request.attempt_id] = replace(
                self._attempts[candidate.request.attempt_id], state="completed",
                outcome="pre_effect_rejected",
                env_pre_result=env_pre_result or ("failed" if reason == "env_pre" else "passed"),
                sec_pre_result=sec_pre_result or ("failed" if reason == "sec_pre" else "not_checked"),
            )
            self._record("pre_effect_rejected", candidate, reason=reason, fence=state.fence)

    def _token_candidate(self, token: FencingToken) -> tuple[_TokenState, _Candidate]:
        try:
            state = self._tokens[token._bytes()]
            record = self._permits[state.permit_id]
            return state, self._candidates[record.candidate_id]
        except (AttributeError, KeyError):
            raise AuthorityError(RejectionReason.INVALID_FENCE) from None

    def admit_effect(self, token: FencingToken, request: ExactRequest) -> None:
        """Final linearized freshness check at the in-process effect boundary."""
        with self._lock:
            state, candidate = self._token_candidate(token)
            if state.admitted:
                raise AuthorityError(RejectionReason.REPLAY)
            if request != candidate.request or state.request_digest != _digest(request):
                raise AuthorityError(RejectionReason.MISMATCH)
            if (not self._expectations_fresh(state.expectations)
                    or not self._external_expectations_fresh(candidate, state.expectations)):
                del self._tokens[token._bytes()]
                self._attempts[request.attempt_id] = replace(
                    self._attempts[request.attempt_id], state="completed", outcome="pre_effect_rejected")
                self._record("effect_rejected_stale", candidate, fence=state.fence)
                raise AuthorityError(RejectionReason.STALE)
            state.admitted = True
            self._attempts[request.attempt_id] = replace(
                self._attempts[request.attempt_id], state="effect_admitted",
                env_pre_result="passed", sec_pre_result="passed",
            )
            self._record("effect_admitted", candidate, fence=state.fence)

    def complete_effect(self, token: FencingToken, outcome: str) -> None:
        if outcome not in {"succeeded", "effect_failed", "effect_unknown"}:
            raise ValueError("invalid effect outcome")
        with self._lock:
            state, candidate = self._token_candidate(token)
            if not state.admitted:
                raise AuthorityError(RejectionReason.INVALID_FENCE)
            del self._tokens[token._bytes()]
            self._attempts[candidate.request.attempt_id] = replace(
                self._attempts[candidate.request.attempt_id], state="completed", outcome=outcome,
            )
            self._record("effect_completed", candidate, outcome=outcome, fence=state.fence)

    def execute_fenced(self, token: FencingToken, request: ExactRequest, native_effect):
        """Validate, admit, and enter the in-process native effect under one lock.

        The reference authority intentionally holds its mutation lock through the
        native call. Environment integrations needing out-of-process execution
        must provide an equivalent fenced gateway; this method does not claim it.
        """
        with self._lock:
            self.admit_effect(token, request)
            self._effect_active = True
            try:
                result = native_effect(request)
            except BaseException:
                self.complete_effect(token, "effect_unknown")
                raise
            else:
                outcome = result.outcome if isinstance(result, NativeEffectResult) else "succeeded"
                self.complete_effect(token, outcome)
                return result.value if isinstance(result, NativeEffectResult) else result
            finally:
                self._effect_active = False

    def record_advisory(self, request: ExactRequest, *, would_block: bool, outcome: str,
                        env_pre_result: str, sec_pre_result: str,
                        manifest_fingerprint: str) -> None:
        if self.mode != "advisory":
            raise AuthorityError("advisory_mode_required")
        with self._lock:
            self._ensure_mutable()
            candidate = self._candidates[request.candidate_id]
            if request != candidate.request or request.attempt_id in self._attempts:
                raise AuthorityError(RejectionReason.MISMATCH)
            self._attempts[request.attempt_id] = AttemptRecord(
                request.attempt_id, "advisory", "completed", outcome=outcome,
                env_pre_result=env_pre_result, sec_pre_result=sec_pre_result,
                request_digest=_digest(request), manifest_fingerprint=manifest_fingerprint,
                enforcement="advisory_bypass", would_block=would_block)
            self._record("advisory_only", candidate, would_block=would_block, outcome=outcome,
                         request_digest=_digest(request), env_pre=env_pre_result,
                         sec_pre=sec_pre_result, fingerprint=manifest_fingerprint)

    def audit_snapshot(self, *, limit: int = 100, after_sequence: int = 0) -> tuple[AuditRecord, ...]:
        if type(limit) is not int or limit < 0 or limit > 256:
            raise ValueError("audit limit must be between 0 and 256")
        with self._lock:
            # Frozen records contain only copied immutable tuples/scalars.
            return tuple(record for record in self._audit if record.sequence > after_sequence)[:limit]

    def attempt_snapshot(self) -> tuple[AttemptRecord, ...]:
        with self._lock:
            return tuple(self._attempts[key] for key in sorted(self._attempts))
