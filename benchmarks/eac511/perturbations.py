"""Pure deterministic P1-P10 plans and actor/evaluator-isolated operators."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .model import InjectionPhase, PerturbationFamily, Visibility


@dataclass(frozen=True, slots=True)
class Perturbation:
    """An immutable injection description; it cannot execute an experiment."""
    family: PerturbationFamily
    phase: InjectionPhase
    operator: str
    visibility: Visibility
    parameters: Mapping[str, Any]


_SPECS = (
    ("P1", "AFTER_INITIAL_OBSERVATION", "peer_claim", "ACTOR_VISIBLE"),
    ("P2", "BEFORE_CANDIDATE_EVALUATION", "conflict", "ACTOR_VISIBLE"),
    ("P3", "BEFORE_CANDIDATE_EVALUATION", "supersession", "ACTOR_VISIBLE"),
    ("P4", "EVALUATOR_ONLY_ASYNC", "hidden_world_change", "NONE"),
    ("P5", "BEFORE_CANDIDATE_EVALUATION", "support_removal", "ACTOR_VISIBLE"),
    ("P6", "BEFORE_CANDIDATE_EVALUATION", "actor_scope_or_delay", "ACTOR_VISIBLE"),
    ("P7", "AFTER_EADM_BEFORE_PERMIT", "envpre_change", "ACTOR_VISIBLE"),
    ("P8", "AFTER_PERMIT_BEFORE_EFFECT", "post_permit_invalidation", "ACTOR_VISIBLE"),
    ("P9", "AFTER_PERMIT_BEFORE_EFFECT", "epre_revision", "ACTOR_VISIBLE"),
    ("P10", "AFTER_PERMIT_BEFORE_EFFECT", "alternate_policy_rotation", "ACTOR_VISIBLE"),
)


def perturbation_plan(seed: int) -> tuple[Perturbation, ...]:
    """Return one stable description for each P1-P10, without running it."""
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")
    return tuple(
        Perturbation(PerturbationFamily(family), InjectionPhase(phase), operator,
                     Visibility(visibility), {"seed": seed, "family": family})
        for family, phase, operator, visibility in _SPECS
    )


def apply_p4(actor_state: Mapping[str, Any], oracle_state: Mapping[str, Any], *, seed: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return copies with P4 changing evaluator state only."""
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")
    actor = dict(actor_state)
    oracle = dict(oracle_state)
    oracle["p4_marker"] = seed
    return actor, oracle


def apply_operator(family: PerturbationFamily | str, actor_state: Mapping[str, Any],
                   evaluator_state: Mapping[str, Any], *, seed: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply a pure state-copy operator; P4 is the sole evaluator-only mutation."""
    selected = PerturbationFamily(family)
    if selected is PerturbationFamily.P4:
        return apply_p4(actor_state, evaluator_state, seed=seed)
    actor = dict(actor_state)
    actor[f"{selected.value.lower()}_marker"] = seed
    return actor, dict(evaluator_state)
