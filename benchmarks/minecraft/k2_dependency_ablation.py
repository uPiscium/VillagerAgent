"""Phase K2 final-check information ablation over the frozen K1 traces.

M0--M3 are research-only comparison checkers. They do not reproduce prior
systems and do not alter RuntimeAuthority, EffectGateway, or Minecraft runtime
semantics. M4 reports the existing Authority behavior observed by K1.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from benchmarks.minecraft.k1_f1 import (
    K1_ADVISORY_RELEVANT,
    K1_AUTHORITY_RELEVANT,
    K1_AUTHORITY_UNRELATED,
    K1InvariantError,
    run_k1_condition,
)
from env.runtime_paths import atomic_write_json

M0_ADMISSION_ONLY = "M0"
M1_EXACT_REQUEST_ONLY = "M1"
M2_GLOBAL_REVISION = "M2"
M3_SEMANTIC_DEPENDENCY_SIGNAL = "M3"
M4_EXISTING_AUTHORITY = "M4"
K2_MODELS = (
    M0_ADMISSION_ONLY,
    M1_EXACT_REQUEST_ONLY,
    M2_GLOBAL_REVISION,
    M3_SEMANTIC_DEPENDENCY_SIGNAL,
    M4_EXISTING_AUTHORITY,
)
EXACT_FIELDS = ("candidate_id", "attempt_id", "exact_request_digest", "action", "arguments", "target")


@dataclass(frozen=True)
class FinalCheckDecision:
    allow: bool
    reason: str
    inputs_used: tuple[str, ...]
    relevant_action_dependency_changed: bool | None = None

    def artifact(self) -> dict[str, Any]:
        return {
            "decision": "allow" if self.allow else "reject",
            "reason": self.reason,
            "inputs_used": list(self.inputs_used),
            "relevant_action_dependency_changed": self.relevant_action_dependency_changed,
        }


@dataclass(frozen=True)
class AdmissionOnlyInput:
    admitted_at_rp: bool


@dataclass(frozen=True)
class ExactRequestOnlyInput:
    admission_identity: tuple[Any, ...]
    effect_identity: tuple[Any, ...]


@dataclass(frozen=True)
class GlobalRevisionInput:
    admission_epoch: int
    effect_epoch: int


@dataclass(frozen=True)
class SemanticDependencySignalInput:
    relevant_action_dependency_changed: bool


@dataclass(frozen=True)
class ExistingAuthorityInput:
    execution_allowed: bool
    rejection_reason: str | None


def evaluate_m0(value: AdmissionOnlyInput) -> FinalCheckDecision:
    admitted = value.admitted_at_rp
    return FinalCheckDecision(
        admitted,
        "admission_epistemically_admissible" if admitted else "admission_not_admissible",
        ("r_p.EAdm",),
    )


def evaluate_m1(value: ExactRequestOnlyInput) -> FinalCheckDecision:
    exact_match = value.admission_identity == value.effect_identity
    return FinalCheckDecision(
        exact_match,
        "exact_request_unchanged" if exact_match else "exact_request_changed",
        tuple(f"r_p/r_e.{field}" for field in EXACT_FIELDS),
    )


def evaluate_m2(value: GlobalRevisionInput) -> FinalCheckDecision:
    changed = value.admission_epoch != value.effect_epoch
    return FinalCheckDecision(
        not changed,
        "global_authority_revision_changed" if changed else "global_authority_revision_unchanged",
        ("r_p.authority_epoch", "r_e.authority_epoch_before_execution"),
    )


def evaluate_m3(value: SemanticDependencySignalInput) -> FinalCheckDecision:
    changed = value.relevant_action_dependency_changed
    return FinalCheckDecision(
        not changed,
        "relevant_action_dependency_changed" if changed else "relevant_action_dependencies_unchanged",
        ("r_d.relevant_action_dependency_changed",),
        relevant_action_dependency_changed=changed,
    )


def evaluate_m4(value: ExistingAuthorityInput) -> FinalCheckDecision:
    allowed = value.execution_allowed
    return FinalCheckDecision(
        allowed,
        ("existing_authority_allowed" if allowed
         else "existing_authority_" + str(value.rejection_reason)),
        ("existing_authority_gateway_outcome",),
    )


def _trace_summary(trace: dict[str, Any]) -> dict[str, Any]:
    return {
        "condition": trace["condition"],
        "mode": trace["mode"],
        "r_p": {key: trace["r_p"][key] for key in (
            "sequence", "authority_sequence", "authority_epoch", "EAdm", "dependency_ids",
            *EXACT_FIELDS,
        )},
        "r_d": {key: trace["r_d"][key] for key in (
            "sequence", "authority_sequence", "authority_epoch", "mutation_type", "current_EAdm",
            "mutation_dependency_ids", "intersecting_dependency_ids",
            "relevant_action_dependency_changed",
        )},
        "r_e": {key: trace["r_e"][key] for key in (
            "sequence", "authority_epoch_before_execution", "current_EAdm",
            "admission_permit_or_shadow_fresh", "EnvPre_oracle", "SecPre_oracle",
            "execution_allowed", "rejection_reason", "execution_would_block",
            "native_effect_reached", *EXACT_FIELDS,
        )},
        "planner_freeze": trace["planner_freeze"],
        "same_prepared_object": trace["same_prepared_object"],
        "exact_action_preserved": trace["exact_action_preserved"],
        "world_state_unchanged": trace["world_state_unchanged"],
        "native_effect_count": trace["native_effect_count"],
    }


def _metric(relevant: FinalCheckDecision, unrelated: FinalCheckDecision) -> dict[str, bool]:
    relevant_detection = not relevant.allow
    unrelated_retention = unrelated.allow
    return {
        "relevant_detection": relevant_detection,
        "unrelated_retention": unrelated_retention,
        "two_case_correctness": relevant_detection and unrelated_retention,
    }


def _verdict(matrix: dict[str, dict[str, str]]) -> str:
    ideal = {
        M0_ADMISSION_ONLY: {"relevant": "allow", "unrelated": "allow"},
        M1_EXACT_REQUEST_ONLY: {"relevant": "allow", "unrelated": "allow"},
        M2_GLOBAL_REVISION: {"relevant": "reject", "unrelated": "reject"},
        M3_SEMANTIC_DEPENDENCY_SIGNAL: {"relevant": "reject", "unrelated": "allow"},
        M4_EXISTING_AUTHORITY: {"relevant": "reject", "unrelated": "allow"},
    }
    if matrix == ideal:
        # M3 is an explicit projection of the semantic dependency signal that
        # also drives M4, not an independent implementation. Outcome equality
        # therefore supports the empirical propagation claim, not mechanism novelty.
        return "K2_EMPIRICAL_PASS"
    return "K2_FAIL"


def _exact_identity(trace: dict[str, Any], phase: str) -> tuple[Any, ...]:
    return tuple(json_value(trace[phase][field]) for field in EXACT_FIELDS)


def json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple((key, json_value(item)) for key, item in sorted(value.items()))
    if isinstance(value, list):
        return tuple(json_value(item) for item in value)
    return value


def _inputs(trace: dict[str, Any]) -> dict[str, Any]:
    return {
        M0_ADMISSION_ONLY: AdmissionOnlyInput(trace["r_p"]["EAdm"] is True),
        M1_EXACT_REQUEST_ONLY: ExactRequestOnlyInput(
            _exact_identity(trace, "r_p"), _exact_identity(trace, "r_e")),
        M2_GLOBAL_REVISION: GlobalRevisionInput(
            trace["r_p"]["authority_epoch"], trace["r_e"]["authority_epoch_before_execution"]),
        M3_SEMANTIC_DEPENDENCY_SIGNAL: SemanticDependencySignalInput(
            trace["r_d"]["relevant_action_dependency_changed"] is True),
    }


def _validate_source_trace(trace: dict[str, Any]) -> None:
    if not trace["exact_action_preserved"] or not trace["same_prepared_object"]:
        raise K1InvariantError("K2 source trace did not retain the exact prepared action")
    if not trace["world_state_unchanged"]:
        raise K1InvariantError("K2 source trace changed the world fixture")
    freeze = trace["planner_freeze"]
    if any(freeze[name] != 0 for name in (
        "planner_calls", "llm_calls", "controller_redecisions", "action_regenerations",
    )):
        raise K1InvariantError("K2 source trace violated planner freeze")
    if trace["r_e"]["EnvPre_oracle"] is not True or trace["r_e"]["SecPre_oracle"] is not True:
        raise K1InvariantError("K2 source trace did not preserve EnvPre/SecPre separation")
    if any(trace["r_p"][field] != trace["r_e"][field] for field in EXACT_FIELDS):
        raise K1InvariantError("K2 source trace changed exact request identity")


def _validate_result(result: dict[str, Any]) -> None:
    for trace in result["source_traces"].values():
        _validate_source_trace(trace)
    for model in K2_MODELS:
        observed = result["observed_matrix"][model]
        decisions = result["decisions"][model]
        if observed["relevant"] != decisions["relevant"]["decision"]:
            raise K1InvariantError(f"{model} relevant matrix/decision mismatch")
        if observed["unrelated"] != decisions["unrelated"]["decision"]:
            raise K1InvariantError(f"{model} unrelated matrix/decision mismatch")


def run_k2_ablation(*, artifact_path: str | Path | None = None) -> dict[str, Any]:
    """Run K1 source traces and evaluate the five isolated final-check models."""
    advisory_relevant = run_k1_condition(K1_ADVISORY_RELEVANT)
    authority_relevant = run_k1_condition(K1_AUTHORITY_RELEVANT)
    authority_unrelated = run_k1_condition(K1_AUTHORITY_UNRELATED)

    comparator_relevant = authority_relevant
    comparator_unrelated = authority_unrelated
    evaluators: dict[str, Callable[[Any], FinalCheckDecision]] = {
        M0_ADMISSION_ONLY: evaluate_m0,
        M1_EXACT_REQUEST_ONLY: evaluate_m1,
        M2_GLOBAL_REVISION: evaluate_m2,
        M3_SEMANTIC_DEPENDENCY_SIGNAL: evaluate_m3,
    }
    relevant_inputs = _inputs(comparator_relevant)
    unrelated_inputs = _inputs(comparator_unrelated)
    decisions: dict[str, dict[str, FinalCheckDecision]] = {
        model: {
            "relevant": evaluator(relevant_inputs[model]),
            "unrelated": evaluator(unrelated_inputs[model]),
        }
        for model, evaluator in evaluators.items()
    }
    decisions[M4_EXISTING_AUTHORITY] = {
        "relevant": evaluate_m4(ExistingAuthorityInput(
            authority_relevant["r_e"]["execution_allowed"],
            authority_relevant["r_e"]["rejection_reason"])),
        "unrelated": evaluate_m4(ExistingAuthorityInput(
            authority_unrelated["r_e"]["execution_allowed"],
            authority_unrelated["r_e"]["rejection_reason"])),
    }
    matrix = {
        model: {
            case: "allow" if decision.allow else "reject"
            for case, decision in cases.items()
        }
        for model, cases in decisions.items()
    }
    result = {
        "schema_version": "minecraft-k2-final-check-ablation/1",
        "scenario": "f1_final_check_information_ablation",
        "models_are_research_comparators": True,
        "prior_system_reproduction_claimed": False,
        "production_runtime_modified": False,
        "interpretation": {
            "m3_signal_source": (
                "fixture intersection of admission manifest dependencies and explicit mutation dependencies"),
            "m3_is_independent_mechanism": False,
            "final_check_novelty_claimed": False,
            "verdict_scope": "two-case F1 relevant-supersession/unrelated-mutation fixture",
        },
        "source_traces": {
            "advisory_relevant": _trace_summary(advisory_relevant),
            "authority_relevant": _trace_summary(authority_relevant),
            "authority_unrelated": _trace_summary(authority_unrelated),
        },
        "decisions": {
            model: {case: decision.artifact() for case, decision in cases.items()}
            for model, cases in decisions.items()
        },
        "observed_matrix": matrix,
        "metrics": {
            model: _metric(cases["relevant"], cases["unrelated"])
            for model, cases in decisions.items()
        },
        "contextual_native_evidence": {
            M0_ADMISSION_ONLY: {
                "checker_only": True,
                "not_model_execution": True,
                "advisory_relevant_native_effect_reached": advisory_relevant["r_e"]["native_effect_reached"],
            },
            M1_EXACT_REQUEST_ONLY: {
                "checker_only": True,
                "not_model_execution": True,
                "advisory_relevant_native_effect_reached": advisory_relevant["r_e"]["native_effect_reached"],
            },
            M2_GLOBAL_REVISION: {"checker_only": True},
            M3_SEMANTIC_DEPENDENCY_SIGNAL: {"checker_only": True},
            M4_EXISTING_AUTHORITY: {
                "relevant_native_effect_reached": authority_relevant["r_e"]["native_effect_reached"],
                "unrelated_native_effect_reached": authority_unrelated["r_e"]["native_effect_reached"],
            },
        },
        "verdict": _verdict(matrix),
        "read_only_projection": True,
        "bounded": True,
    }
    _validate_result(result)
    if artifact_path is not None:
        atomic_write_json(artifact_path, result)
    return result
