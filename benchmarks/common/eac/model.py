"""Immutable data model for the frozen EAC semantics/1 contract.

This module deliberately contains no authority or state-transition logic.  The
small value objects here are the vocabulary shared by adapters and the runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from secrets import token_bytes
from typing import Any, ClassVar, Mapping, Optional


class _Text(str):
    def __new__(cls, value: str):
        if not isinstance(value, str) or not value or any("\ud800" <= c <= "\udfff" for c in value):
            raise ValueError("identifiers must be non-empty, non-surrogate strings")
        return str.__new__(cls, value)


@dataclass(frozen=True, slots=True, eq=False)
class PropositionKey:
    namespace: str
    predicate: str
    arguments: tuple[Any, ...] = ()
    temporal_scope: str = ""

    def __post_init__(self) -> None:
        if not self.namespace or not self.predicate or not isinstance(self.arguments, tuple):
            raise ValueError("invalid proposition key")
        from .canonical import canonical_argument
        object.__setattr__(self, "arguments", tuple(canonical_argument(value) for value in self.arguments))

    def _identity_bytes(self) -> bytes:
        from .canonical import canonical_bytes
        return canonical_bytes([self.namespace, self.predicate, list(self.arguments), self.temporal_scope])

    def __eq__(self, other: object) -> bool:
        return isinstance(other, PropositionKey) and self._identity_bytes() == other._identity_bytes()

    def __hash__(self) -> int:
        return hash(self._identity_bytes())


@dataclass(frozen=True, slots=True)
class Proposition:
    key: PropositionKey
    polarity: bool = True

    def __post_init__(self) -> None:
        if type(self.polarity) is not bool:
            raise ValueError("proposition polarity must be boolean")


@dataclass(frozen=True, slots=True)
class VersionedRef:
    identity: str
    version: int | str
    digest: str | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.identity, str) or not self.identity
                or any("\ud800" <= char <= "\udfff" for char in self.identity)
                or not isinstance(self.version, (int, str)) or isinstance(self.version, bool)
                or (isinstance(self.version, str) and (not self.version or any(
                    "\ud800" <= char <= "\udfff" for char in self.version)))):
            raise ValueError("versioned reference requires identity and version")
        if self.digest is not None and (not self.digest or any(char not in "0123456789abcdef" for char in self.digest.removeprefix("sha256:"))
                                        or len(self.digest.removeprefix("sha256:")) != 64):
            raise ValueError("versioned reference digest must be SHA-256")


PolicyRef = VersionedRef
ProfileRef = VersionedRef
EPreRef = VersionedRef
ActionRef = VersionedRef


@dataclass(frozen=True, slots=True)
class ActorScope:
    actor_id: str
    visibility_revision: int | str
    scope: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope", tuple(self.scope))


@dataclass(frozen=True, slots=True)
class EvidenceRoot:
    root_id: str
    root_type: str
    proposition: Proposition
    source: str
    revision: int | str
    visible_to: tuple[str, ...] = ()
    provenance_id: str | None = None
    supersedes: tuple[str, ...] = ()
    source_lineage_id: str = ""
    upstream_origin_id: str = ""
    valid: bool = True
    current: bool = True
    source_stream_id: str | None = None
    source_stream_revision: int | None = None
    issuer: str | None = None
    mapping_rule_id: str | None = None
    originating_action_identity: str | None = None
    evidence_gathering_action: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "visible_to", tuple(self.visible_to))
        object.__setattr__(self, "supersedes", tuple(self.supersedes))


@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    provenance_id: str
    origin: str
    upstream: tuple[str, ...] = ()
    issuer: str | None = None
    metadata: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        from .canonical import canonical_argument
        object.__setattr__(self, "upstream", tuple(self.upstream))
        object.__setattr__(self, "metadata", tuple((str(key), canonical_argument(value))
                                                   for key, value in self.metadata))


@dataclass(frozen=True, slots=True)
class SupportDerivation:
    derivation_id: str
    rule: str
    premises: tuple[str, ...]
    conclusion: Proposition
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "premises", tuple(self.premises))
        object.__setattr__(self, "provenance", tuple(self.provenance))


class WitnessValidity(str, Enum):
    SCOPED = "scoped"
    GROUNDED = "grounded"
    FRESH = "fresh"
    NON_DEFEATED = "non_defeated"
    SUPPORTS = "supports"


@dataclass(frozen=True, slots=True)
class JustificationWitness:
    witness_id: str
    proposition: Proposition
    roots: tuple[EvidenceRoot, ...] = ()
    derivations: tuple[SupportDerivation, ...] = ()
    provenance: tuple[ProvenanceRecord, ...] = ()
    validity: tuple[tuple[WitnessValidity, bool], ...] = ()
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EPreAssessment:
    proposition: Proposition
    admissible: bool
    validity: tuple[tuple[WitnessValidity, bool], ...]
    witness_id: str | None = None
    dependencies: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    recoveries: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.dependencies) > 128 or len(self.reasons) > 16 or len(self.recoveries) > 8:
            raise ValueError("EPre assessment diagnostics exceed bounds")


@dataclass(frozen=True, slots=True)
class EpistemicAdmissibility:
    admissible: bool
    witnesses: tuple[JustificationWitness, ...] = ()
    reasons: tuple[str, ...] = ()
    recoveries: tuple[str, ...] = ()
    policy: PolicyRef | None = None
    profile: ProfileRef | None = None
    assessments: tuple[EPreAssessment, ...] = ()


@dataclass(frozen=True, slots=True, eq=False)
class ExactRequest:
    candidate_id: str
    attempt_id: str
    action: ActionRef
    arguments: tuple[tuple[str, Any], ...] = ()
    target: Any = None

    def __post_init__(self) -> None:
        from .canonical import canonical_argument
        if not self.candidate_id or not self.attempt_id or not isinstance(self.action, ActionRef):
            raise ValueError("invalid exact request identity")
        names = tuple(name for name, unused in self.arguments)
        if len(names) != len(set(names)) or any(not isinstance(name, str) or not name.isascii() or not name for name in names):
            raise ValueError("request argument names must be unique ASCII strings")
        object.__setattr__(self, "arguments", tuple(sorted(
            ((name, canonical_argument(value)) for name, value in self.arguments), key=lambda item: item[0])))
        object.__setattr__(self, "target", canonical_argument(self.target))

    def identity_bytes(self) -> bytes:
        from .canonical import canonical_bytes
        return canonical_bytes({
            "candidate_id": self.candidate_id,
            "attempt_id": self.attempt_id,
            "action": {"identity": self.action.identity, "version": self.action.version,
                       "digest": self.action.digest},
            "arguments": {name: value for name, value in self.arguments},
            "target": self.target,
        })

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ExactRequest) and self.identity_bytes() == other.identity_bytes()

    def __hash__(self) -> int:
        return hash(self.identity_bytes())


@dataclass(frozen=True, slots=True)
class DependencyExpectation:
    dependency_id: str
    revision: int | str
    kind: str = ""
    required: bool = True


@dataclass(frozen=True, slots=True)
class DependencyManifest:
    request: ExactRequest
    actor: ActorScope
    expectations: tuple[DependencyExpectation, ...] = ()
    action: ActionRef | None = None
    epre: EPreRef | None = None
    policy: PolicyRef | None = None
    profile: ProfileRef | None = None
    fingerprint: str | None = None
    propositions: tuple[Proposition, ...] = ()
    witness_ids: tuple[str, ...] = ()
    authority_epoch: int = 0
    authority_nonce: str = ""


class CandidateLifecycle(str, Enum):
    PROPOSED = "proposed"
    WAITING_FOR_EVIDENCE = "waiting_for_evidence"
    BLOCKED_CONFLICT = "blocked_conflict"
    BLOCKED_PRECONDITION = "blocked_precondition"
    EPISTEMICALLY_ADMISSIBLE = "epistemically_admissible"


class PermitLifecycle(str, Enum):
    NONE = "none"
    ISSUED = "issued"
    STALE = "stale"
    REVOKED = "revoked"
    CONSUMED = "consumed"


class RejectionReason(str, Enum):
    INVALID_REQUEST = "invalid_request"
    STALE = "stale"
    REVOKED = "revoked"
    REPLAY = "replay"
    MISMATCH = "mismatch"
    PRECONDITION = "precondition"
    CONFLICT = "conflict"
    NOT_ADMISSIBLE = "not_admissible"
    MISSING_PERMIT = "missing_permit"
    INVALID_FENCE = "invalid_fence"
    PRECHECK_REJECTED = "precheck_rejected"


@dataclass(frozen=True, slots=True)
class PermitView:
    permit_id: str
    request: ExactRequest
    lifecycle: PermitLifecycle
    fingerprint: str
    mode: str = "authority"
    manifest: DependencyManifest | None = None


class _FencingToken:
    __slots__ = ("__value",)
    def __init__(self, value: bytes, _internal: object):
        if _internal is not _FencingToken:
            raise TypeError("fencing tokens are created by the authority")
        self.__value = value

    @classmethod
    def _issue(cls) -> "_FencingToken":
        return cls(token_bytes(32), cls)

    def _bytes(self) -> bytes:
        return self.__value


FencingToken = _FencingToken


@dataclass(frozen=True, slots=True)
class AuditRecord:
    event_id: str
    event: str
    candidate_id: str
    attempt_id: str | None = None
    permit_id: str | None = None
    dependencies: tuple[str, ...] = ()
    details: tuple[tuple[str, Any], ...] = ()
    sequence: int = 0

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("audit sequence must be non-negative")
        if len(self.details) > 128:
            raise ValueError("audit record details are bounded to 128 items")


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    attempt_id: str
    permit_id: str
    state: str
    outcome: str = "none"
    fence: int | None = None
    env_pre_result: str = "not_checked"
    sec_pre_result: str = "not_checked"
    request_digest: str | None = None
    manifest_fingerprint: str | None = None
    enforcement: str = "authority"
    would_block: bool | None = None


@dataclass(frozen=True, slots=True)
class NativeEffectResult:
    """Explicit native result when the environment can determine failure."""
    value: Any = None
    outcome: str = "succeeded"

    def __post_init__(self) -> None:
        if self.outcome not in {"succeeded", "effect_failed"}:
            raise ValueError("native effect outcome must be succeeded or effect_failed")
