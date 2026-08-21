"""Phase K3b unresolved semantic contradiction consistency fixture."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from benchmarks.common.eac import Proposition, PropositionKey, ProvenanceRecord
from benchmarks.common.eac.authority import _proposition_slot
from benchmarks.common.eac.witness import EvidenceSnapshot, evaluate_epistemic_admissibility
from benchmarks.minecraft.eac_runtime import MinecraftEACError, MinecraftEACRuntime
from benchmarks.minecraft.k1_f1 import (
    FROZEN_CLASSIFICATION_DIGEST,
    FROZEN_SOURCE_PROFILE,
    FROZEN_SUPPORT_POLICY,
    K1DeterministicSynchronizationPoint,
    semantic_dependency_intersection,
)
from benchmarks.minecraft.k2_dependency_ablation import (
    AdmissionOnlyInput,
    ExactRequestOnlyInput,
    ExistingAuthorityInput,
    GlobalRevisionInput,
    SemanticDependencySignalInput,
    evaluate_m0,
    evaluate_m1,
    evaluate_m2,
    evaluate_m3,
    evaluate_m4,
    json_value,
)
from env.runtime_paths import atomic_write_json

K3B_ADVISORY_CONTRADICTION = "K3B-A"
K3B_AUTHORITY_CONTRADICTION = "K3B-B"
K3B_AUTHORITY_UNRELATED = "K3B-C"
K3B_CONDITIONS = (
    K3B_ADVISORY_CONTRADICTION,
    K3B_AUTHORITY_CONTRADICTION,
    K3B_AUTHORITY_UNRELATED,
)
EXACT_FIELDS = ("candidate_id", "attempt_id", "exact_request_digest", "action", "arguments", "target")


class K3bInvariantError(AssertionError):
    pass


def _request_snapshot(prepared) -> dict[str, Any]:
    request = prepared.request
    identity = json.loads(request.identity_bytes().decode("utf-8"))
    return {
        "candidate_id": request.candidate_id,
        "attempt_id": request.attempt_id,
        "exact_request_digest": "sha256:" + hashlib.sha256(request.identity_bytes()).hexdigest(),
        "action": {
            "identity": request.action.identity,
            "version": request.action.version,
            "digest": request.action.digest,
        },
        "arguments": identity["arguments"],
        "target": identity["target"],
    }


def _last_authority_sequence(runtime: MinecraftEACRuntime) -> int:
    audit = runtime.authority.audit_snapshot(limit=256)
    return audit[-1].sequence if audit else 0


def _exact_identity(trace: dict[str, Any], phase: str) -> tuple[Any, ...]:
    return tuple(json_value(trace[phase][field]) for field in EXACT_FIELDS)


def _comparison_decisions(trace: dict[str, Any]) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    decisions = {
        "M0": evaluate_m0(AdmissionOnlyInput(trace["r_p"]["EAdm"])),
        "M1": evaluate_m1(ExactRequestOnlyInput(
            _exact_identity(trace, "r_p"), _exact_identity(trace, "r_e"))),
        "M2": evaluate_m2(GlobalRevisionInput(
            trace["r_p"]["authority_epoch"], trace["r_e"]["authority_epoch_before_execution"])),
        "M3": evaluate_m3(SemanticDependencySignalInput(
            trace["r_d"]["relevant_action_dependency_changed"])),
    }
    result = {model: "allow" if decision.allow else "reject" for model, decision in decisions.items()}
    artifacts = {model: decision.artifact() for model, decision in decisions.items()}
    if trace["mode"] == "dual_dag_authority":
        authority = evaluate_m4(ExistingAuthorityInput(
            trace["r_e"]["execution_allowed"], trace["r_e"]["rejection_reason"]))
        result["M4"] = "allow" if authority.allow else "reject"
        artifacts["M4"] = authority.artifact()
    else:
        result["M4"] = "not_applicable"
        artifacts["M4"] = {
            "decision": "not_applicable",
            "reason": "existing_authority_not_run_in_advisory_mode",
            "inputs_used": [],
            "relevant_action_dependency_changed": None,
        }
    return result, artifacts


def _validate_trace(trace: dict[str, Any]) -> None:
    relevant = trace["condition"] != K3B_AUTHORITY_UNRELATED
    if not trace["r_p"]["EAdm"]:
        raise K3bInvariantError("action was not admissible at r_p")
    if not trace["same_prepared_object"] or not trace["exact_action_preserved"]:
        raise K3bInvariantError("exact prepared action was not retained")
    if any(trace["r_p"][field] != trace["r_e"][field] for field in EXACT_FIELDS):
        raise K3bInvariantError("exact action identity changed")
    if not trace["r_p"]["sequence"] < trace["r_d"]["sequence"] < trace["r_e"]["sequence"]:
        raise K3bInvariantError("phase order is invalid")
    if not trace["world_state_unchanged"]:
        raise K3bInvariantError("world fixture changed")
    if not trace["r_e"]["EnvPre_oracle"] or not trace["r_e"]["SecPre_oracle"]:
        raise K3bInvariantError("detached legality oracle failed")
    for name in ("planner_calls", "llm_calls", "controller_redecisions", "action_regenerations"):
        if trace["planner_freeze"][name] != 0:
            raise K3bInvariantError("planner freeze was violated")
    if relevant:
        contradiction = trace["contradiction"]
        if not all((
            contradiction["same_stable_proposition_key"] is True,
            contradiction["positive_polarity"] is True,
            contradiction["negative_polarity"] is False,
            contradiction["positive_current"] is True,
            contradiction["negative_current"] is True,
            contradiction["positive_supersedes"] == [],
            contradiction["negative_supersedes"] == [],
            contradiction["positive_root_independently_supports_positive"] is True,
            contradiction["negative_root_independently_supports_opposite"] is True,
            contradiction["unresolved"] is True,
        )):
            raise K3bInvariantError("required unresolved contradiction was not constructed")
        if trace["r_d"]["current_EAdm"] is not False:
            raise K3bInvariantError("contradiction did not make EAdm false")
        if trace["r_d"]["reasons"] != ["non_defeated.conflict"]:
            raise K3bInvariantError("EAdm failed for a reason other than contradiction")
        if trace["r_d"]["validity"]["non_defeated"] is not False:
            raise K3bInvariantError("NonDefeated did not become false")
        if trace["mode"] == "dual_dag_advisory":
            if not all((trace["r_e"]["execution_allowed"] is True,
                        trace["r_e"]["execution_would_block"] is True,
                        trace["r_e"]["native_callable_reached"] is True)):
                raise K3bInvariantError("Advisory did not record-and-bypass the conflict")
        elif not all((trace["r_e"]["execution_allowed"] is False,
                      trace["r_e"]["rejection_reason"] == "stale",
                      trace["r_e"]["native_callable_reached"] is False)):
            raise K3bInvariantError("Authority did not stale-reject the conflict")
    elif trace["r_d"]["current_EAdm"] is not True:
        raise K3bInvariantError("unrelated control changed EAdm")
    elif not all((trace["r_e"]["execution_allowed"] is True,
                  trace["r_e"]["native_callable_reached"] is True)):
        raise K3bInvariantError("unrelated control did not execute")


def run_k3b_condition(condition: str, *, artifact_path: str | Path | None = None) -> dict[str, Any]:
    if condition not in K3B_CONDITIONS:
        raise ValueError("unknown K3b condition")
    relevant = condition != K3B_AUTHORITY_UNRELATED
    mode = "dual_dag_advisory" if condition == K3B_ADVISORY_CONTRADICTION else "dual_dag_authority"
    barrier = K1DeterministicSynchronizationPoint()
    planner_freeze = {
        "instrumentation_scope": "controlled_k3b_fixture",
        "execution_model": "direct_retained_prepared_action",
        "planner_instantiated": False,
        "llm_instantiated": False,
        "controller_instantiated": False,
        "planner_calls": 0,
        "llm_calls": 0,
        "controller_redecisions": 0,
        "action_regenerations": 0,
    }
    world = MappingProxyType({"target": (1, 2, 3), "target_present": True, "diggable": True})

    def world_snapshot():
        return {
            "target": list(world["target"]),
            "target_present": world["target_present"],
            "diggable": world["diggable"],
        }

    world_at_admission = world_snapshot()
    gateway_calls = {"env": 0, "sec": 0}

    def gateway_env(request):
        gateway_calls["env"] += 1
        target = json.loads(request.identity_bytes())["target"]
        return (tuple(target[key] for key in ("x", "y", "z")) == world["target"]
                and world["target_present"] and world["diggable"])

    def gateway_sec(unused):
        gateway_calls["sec"] += 1
        return True

    runtime = MinecraftEACRuntime(
        mode=mode,
        run_id="k3b-" + condition.lower(),
        env_prechecks={"MineBlock": gateway_env},
        sec_prechecks={"MineBlock": gateway_sec},
    )
    e1 = runtime.ingest_target_observation("Alice", "MineBlock", {"x": 1, "y": 2, "z": 3})
    native_calls = []

    def native_mine(**kwargs):
        native_calls.append(dict(kwargs))
        return {"status": True, "position": [kwargs["x"], kwargs["y"], kwargs["z"]]}

    prepared = runtime.prepare_tool("MineBlock", native_mine, (), {
        "player_name": "Alice", "x": 1, "y": 2, "z": 3,
        "emotion": [], "murmur": "",
    })
    admission = prepared.permit
    if mode == "dual_dag_advisory":
        admission = runtime.authority.shadow_permit(prepared.request.candidate_id)
    admission_decision = runtime.authority.evaluate(prepared.request.candidate_id)
    r_p = {
        "sequence": barrier.admission(prepared),
        "authority_sequence": _last_authority_sequence(runtime),
        "authority_epoch": runtime.authority.epoch,
        **_request_snapshot(prepared),
        "EAdm": admission_decision.admissible,
        "witness_root_ids": [
            root.root_id for witness in admission_decision.witnesses for root in witness.roots],
        "dependency_fingerprint": admission.fingerprint,
        "dependency_ids": [item.dependency_id for item in admission.manifest.expectations],
        "permit_or_shadow_state": admission.lifecycle.value,
    }

    contradiction = None
    if relevant:
        opposite = replace(e1.proposition, polarity=False)
        e2 = runtime.ingest_actor_record(
            actor_id="Alice",
            proposition=opposite,
            record_type="trusted_tool_result",
            source="minecraft-observation-adapter",
            revision=2,
        )
        e1_provenance = ProvenanceRecord(e1.provenance_id, "minecraft-visible-observation")
        e2_provenance = ProvenanceRecord(e2.provenance_id, "minecraft-observation-adapter")
        isolated_positive = evaluate_epistemic_admissibility(
            admission.manifest.actor,
            (e1.proposition,),
            EvidenceSnapshot((e1,), (), (e1_provenance,)),
            runtime.policy_binding,
            runtime.profile_binding,
        )
        isolated_opposite = evaluate_epistemic_admissibility(
            admission.manifest.actor,
            (opposite,),
            EvidenceSnapshot((e2,), (), (e2_provenance,)),
            runtime.policy_binding,
            runtime.profile_binding,
        )
        conflict_dependency = _proposition_slot(opposite, "Alice")
        mutation_dependency_ids = {
            "evidence:" + e2.root_id,
            "provenance:" + e2.provenance_id,
            conflict_dependency,
        }
        contradiction = {
            "positive_root_id": e1.root_id,
            "negative_root_id": e2.root_id,
            "same_stable_proposition_key": e1.proposition.key == e2.proposition.key,
            "positive_polarity": e1.proposition.polarity,
            "negative_polarity": e2.proposition.polarity,
            "positive_current": runtime.authority._roots[e1.root_id].current,
            "negative_current": runtime.authority._roots[e2.root_id].current,
            "positive_supersedes": list(e1.supersedes),
            "negative_supersedes": list(e2.supersedes),
            "positive_visibility": list(e1.visible_to),
            "negative_visibility": list(e2.visible_to),
            "positive_root_type": e1.root_type,
            "negative_root_type": e2.root_type,
            "positive_root_independently_supports_positive": isolated_positive.admissible,
            "negative_evidence_revision": e2.revision,
            "negative_root_independently_supports_opposite": isolated_opposite.admissible,
        }
    else:
        unrelated = Proposition(PropositionKey(
            "minecraft", "weather_visible", ("rain",), "current"))
        e2 = runtime.ingest_actor_record(
            actor_id="Alice", proposition=unrelated,
            record_type="direct_observation", source="minecraft-visible-weather", revision=2)
        mutation_dependency_ids = {
            "evidence:" + e2.root_id,
            "provenance:" + e2.provenance_id,
            _proposition_slot(unrelated, "Alice"),
        }

    current = runtime.authority.evaluate(prepared.request.candidate_id)
    assessment = current.assessments[0]
    intersection = semantic_dependency_intersection(r_p["dependency_ids"], mutation_dependency_ids)
    if contradiction is not None:
        contradiction["unresolved"] = all((
            runtime.authority._roots[e1.root_id].current,
            runtime.authority._roots[e2.root_id].current,
            not e1.supersedes,
            not e2.supersedes,
            current.reasons == ("non_defeated.conflict",),
        ))
    r_d = {
        "sequence": barrier.revision(prepared),
        "authority_sequence": _last_authority_sequence(runtime),
        "authority_epoch": runtime.authority.epoch,
        "current_EAdm": current.admissible,
        "reasons": list(current.reasons),
        "validity": {dimension.value: value for dimension, value in assessment.validity},
        "permit_or_shadow_fresh": (
            runtime.authority.shadow_fresh(prepared.request, admission)
            if mode == "dual_dag_advisory"
            else runtime.authority.permit(admission.permit_id).lifecycle.value == "issued"),
        "mutation_dependency_ids": sorted(mutation_dependency_ids),
        "intersecting_dependency_ids": list(intersection),
        "relevant_action_dependency_changed": bool(intersection),
    }

    world_before_execution = world_snapshot()
    detached_target = json.loads(prepared.request.identity_bytes())["target"]
    detached_env_pre = (
        tuple(detached_target[key] for key in ("x", "y", "z")) == world["target"]
        and world["target_present"] and world["diggable"])
    detached_sec_pre = True
    r_e = {
        "sequence": barrier.execution_submission(prepared),
        "authority_sequence_before_execution": _last_authority_sequence(runtime),
        "authority_epoch_before_execution": runtime.authority.epoch,
        **_request_snapshot(prepared),
        "current_EAdm": current.admissible,
        "EnvPre_oracle": detached_env_pre,
        "SecPre_oracle": detached_sec_pre,
    }
    execution_allowed = False
    rejection_reason = None
    try:
        runtime.execute_prepared(prepared)
        execution_allowed = True
    except MinecraftEACError as exc:
        rejection_reason = str(exc)
    attempt = next(iter(runtime.authority.attempt_snapshot()), None)
    r_e.update({
        "execution_allowed": execution_allowed,
        "rejection_reason": rejection_reason,
        "execution_would_block": attempt.would_block if attempt is not None else None,
        "gateway_env_precheck_calls": gateway_calls["env"],
        "gateway_sec_precheck_calls": gateway_calls["sec"],
        "native_callable_reached": bool(native_calls),
    })
    trace = {
        "schema_version": "minecraft-k3b-contradiction/1",
        "scenario": "unresolved_actor_visible_contradiction" if relevant else "unrelated_control",
        "condition": condition,
        "mode": mode,
        "r_p": r_p,
        "contradiction": contradiction,
        "r_d": r_d,
        "r_e": r_e,
        "planner_freeze": planner_freeze,
        "same_prepared_object": barrier.retains(prepared),
        "exact_action_preserved": all(r_p[field] == r_e[field] for field in EXACT_FIELDS),
        "world_state_unchanged": world_at_admission == world_before_execution == world_snapshot(),
        "semantic_bindings": {
            "support_policy": {
                "identity": runtime.authority.policy.identity,
                "version": runtime.authority.policy.version,
                "digest": runtime.authority.policy.digest,
            },
            "source_profile": {
                "identity": runtime.authority.profile.identity,
                "version": runtime.authority.profile.version,
                "digest": runtime.authority.profile.digest,
            },
            "classification_digest": runtime.classification_identity,
        },
        "comparison": {},
        "comparison_decisions": {},
        "comparison_scope": {
            "M0-M3": "research-only checker projections",
            "M4": "existing Authority runtime outcome when mode is Authority",
            "M3_is_independent_mechanism": False,
            "prior_system_reproduction_claimed": False,
            "verdict_scope": "one contradiction and one unrelated controlled fixture",
        },
        "production_runtime_modified": False,
        "read_only_projection": True,
        "bounded": True,
    }
    trace["comparison"], trace["comparison_decisions"] = _comparison_decisions(trace)
    if trace["semantic_bindings"]["support_policy"] != FROZEN_SUPPORT_POLICY:
        raise K3bInvariantError("frozen SupportPolicy changed")
    if trace["semantic_bindings"]["source_profile"] != FROZEN_SOURCE_PROFILE:
        raise K3bInvariantError("frozen SourceProfile changed")
    if trace["semantic_bindings"]["classification_digest"] != FROZEN_CLASSIFICATION_DIGEST:
        raise K3bInvariantError("frozen EPre classification changed")
    _validate_trace(trace)
    if artifact_path is not None:
        atomic_write_json(artifact_path, trace)
    return trace


def run_k3b_experiment(*, artifact_path: str | Path | None = None) -> dict[str, Any]:
    advisory = run_k3b_condition(K3B_ADVISORY_CONTRADICTION)
    authority = run_k3b_condition(K3B_AUTHORITY_CONTRADICTION)
    unrelated = run_k3b_condition(K3B_AUTHORITY_UNRELATED)
    matrix = {
        model: {
            "contradiction": authority["comparison"][model],
            "unrelated": unrelated["comparison"][model],
        }
        for model in ("M0", "M1", "M2", "M3", "M4")
    }
    expected = {
        "M0": {"contradiction": "allow", "unrelated": "allow"},
        "M1": {"contradiction": "allow", "unrelated": "allow"},
        "M2": {"contradiction": "reject", "unrelated": "reject"},
        "M3": {"contradiction": "reject", "unrelated": "allow"},
        "M4": {"contradiction": "reject", "unrelated": "allow"},
    }
    passed = all((
        advisory["r_d"]["current_EAdm"] is False,
        advisory["r_e"]["execution_would_block"] is True,
        advisory["r_e"]["execution_allowed"] is True,
        advisory["r_e"]["native_callable_reached"] is True,
        authority["r_e"]["execution_allowed"] is False,
        authority["r_e"]["rejection_reason"] == "stale",
        authority["r_e"]["native_callable_reached"] is False,
        unrelated["r_d"]["current_EAdm"] is True,
        unrelated["r_e"]["execution_allowed"] is True,
        unrelated["r_e"]["native_callable_reached"] is True,
        matrix == expected,
    ))
    result = {
        "schema_version": "minecraft-k3b-experiment/1",
        "scenario": "contradiction_consistency_experiment",
        "traces": {
            "advisory_contradiction": advisory,
            "authority_contradiction": authority,
            "authority_unrelated": unrelated,
        },
        "observed_matrix": matrix,
        "verdict": "K3B_PASS" if passed else "K3B_FAIL",
        "production_runtime_modified": False,
        "read_only_projection": True,
        "bounded": True,
    }
    if artifact_path is not None:
        atomic_write_json(artifact_path, result)
    return result
