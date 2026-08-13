"""Shared Epistemic–Action Consistency Runtime Authority primitives."""

from .authority import AuthorityError, AuthorityStateReader, CandidateStateWriter, RuntimeAuthority
from .gateway import EffectGateway, EffectRejected
from .model import (
    ActionRef, ActorScope, AttemptRecord, AuditRecord, CandidateLifecycle, DependencyManifest,
    EPreAssessment, EPreRef, EpistemicAdmissibility, EvidenceRoot, ExactRequest, FencingToken,
    JustificationWitness, NativeEffectResult, PermitLifecycle, PermitView, PolicyRef, ProfileRef,
    Proposition, PropositionKey, ProvenanceRecord, RejectionReason, SupportDerivation,
    WitnessValidity,
)
from .policy import (
    PolicyBinding, SourceProfileBinding, bind_source_profile, load_support_policy,
    match_mapping,
)

__all__ = [name for name in globals() if not name.startswith("_")]
