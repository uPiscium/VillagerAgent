"""Frozen value vocabulary for the Issue #511 control plane."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class PerturbationFamily(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"
    P5 = "P5"
    P6 = "P6"
    P7 = "P7"
    P8 = "P8"
    P9 = "P9"
    P10 = "P10"


class InjectionPhase(str, Enum):
    BEFORE_INITIAL_OBSERVATION = "BEFORE_INITIAL_OBSERVATION"
    AFTER_INITIAL_OBSERVATION = "AFTER_INITIAL_OBSERVATION"
    BEFORE_CANDIDATE_EVALUATION = "BEFORE_CANDIDATE_EVALUATION"
    AFTER_EADM_BEFORE_PERMIT = "AFTER_EADM_BEFORE_PERMIT"
    AFTER_PERMIT_BEFORE_EFFECT = "AFTER_PERMIT_BEFORE_EFFECT"
    AFTER_EFFECT = "AFTER_EFFECT"
    EVALUATOR_ONLY_ASYNC = "EVALUATOR_ONLY_ASYNC"


class Condition(str, Enum):
    BASELINE = "baseline"
    ADVISORY = "advisory"
    AUTHORITY = "authority"


class Tier(str, Enum):
    INTEGRITY = "tier1_integrity"
    TASK = "tier2_task"


class Visibility(str, Enum):
    ACTOR_VISIBLE = "ACTOR_VISIBLE"
    EVALUATOR_ONLY = "EVALUATOR_ONLY"
    PUBLIC_SANITIZED = "PUBLIC_SANITIZED"
    NONE = "NONE"


class IntegrityFixtureKind(str, Enum):
    ACTOR_SCOPE = "actor_scope"
    DUAL_CLASS = "dual_class_preconditions"
    PRE_GATE_EQUIVALENCE = "advisory_authority_pre_gate_equivalence"
    REPLAY_AND_REVOCATION = "replay_and_revocation"
    SUPPORTED_PATH_BYPASS = "supported_path_bypass"
    UNRELATED_DEPENDENCY = "unrelated_dependency_retention"
    RELEVANT_DEPENDENCY = "relevant_dependency_invalidation"
    POST_PERMIT_INVALIDATION = "P8_post_permit_invalidation"
    EPRE_REVISION = "P9_epre_revision"
    POLICY_REVISION = "P10_policy_revision"


SEEDS: tuple[int, ...] = (11, 23, 37, 53, 71)


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: str
    family: PerturbationFamily
    tier: Tier
    digest: str
    document: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.scenario_id or len(self.digest) != 64:
            raise ValueError("invalid scenario identity")


@dataclass(frozen=True, slots=True)
class MatrixCell:
    run_id: str
    scenario_id: str
    scenario_digest: str
    family: PerturbationFamily
    seed: int
    condition: Condition
    pre_gate_input_digest: str
    enforcement: Mapping[str, Any]


# Compatibility aliases are intentionally narrow.
Phase = InjectionPhase
Seed = int
Tier1Kind = IntegrityFixtureKind
