"""Controlled Phase K1 admission-to-effect traces for one frozen F1 scenario.

This is a deterministic kill-test fixture, not a benchmark runner. It composes
the existing ``prepare_tool`` and ``execute_prepared`` integration APIs and
does not alter the normal runtime path or instantiate a planner, controller,
LLM, queue, worker, or judged Minecraft environment.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from benchmarks.common.eac import Proposition, PropositionKey
from benchmarks.common.eac.model import PermitLifecycle
from benchmarks.minecraft.eac_runtime import (
    MinecraftEACError,
    MinecraftEACRuntime,
    MinecraftPreparedAction,
)
from env.runtime_paths import atomic_write_json

K1_ADVISORY_RELEVANT = "K1-A"
K1_AUTHORITY_RELEVANT = "K1-B"
K1_AUTHORITY_UNRELATED = "K1-C"
K1_CONDITIONS = frozenset((
    K1_ADVISORY_RELEVANT,
    K1_AUTHORITY_RELEVANT,
    K1_AUTHORITY_UNRELATED,
))
FROZEN_SUPPORT_POLICY = {
    "identity": "eac-primary-support",
    "version": 1,
    "digest": "ef34b67ef618ed4b34a9c2720d854e02d8fb6af917a0cbe472daef8cc5603d51",
}
FROZEN_SOURCE_PROFILE = {
    "identity": "minecraft-eac-primary",
    "version": 1,
    "digest": "01f65a8fd4bb68b1631e81d3c8d50f073747b5179995eeb60be3a55fdb6979be",
}
FROZEN_CLASSIFICATION_DIGEST = "7c8bf97b80c96f1d05e8250cb9d89bb21b35c073f49979501090d72f13b56001"


class K1InvariantError(AssertionError):
    pass


@dataclass(frozen=True)
class PlannerFreezeCounters:
    """Explicit counters for the only call sites available to this fixture."""

    planner_calls: int = 0
    llm_calls: int = 0
    controller_redecisions: int = 0
    action_regenerations: int = 0

    def artifact(self) -> dict[str, Any]:
        return {
            "instrumentation_scope": "controlled_k1_fixture",
            "execution_model": "direct_retained_prepared_action",
            "planner_instantiated": False,
            "llm_instantiated": False,
            "controller_instantiated": False,
            "planner_calls": self.planner_calls,
            "llm_calls": self.llm_calls,
            "controller_redecisions": self.controller_redecisions,
            "action_regenerations": self.action_regenerations,
        }


class K1DeterministicSynchronizationPoint:
    """One-shot, fixture-only phase barrier over one prepared action object."""

    def __init__(self) -> None:
        self._prepared: MinecraftPreparedAction | None = None
        self._phase = 0

    def admission(self, prepared: MinecraftPreparedAction) -> int:
        if self._prepared is not None or self._phase != 0:
            raise K1InvariantError("synchronization point admission already consumed")
        self._prepared = prepared
        self._phase = 1
        return self._phase

    def revision(self, prepared: MinecraftPreparedAction) -> int:
        self._require_same(prepared)
        if self._phase != 1:
            raise K1InvariantError("semantic revision is out of order")
        self._phase = 2
        return self._phase

    def execution_submission(self, prepared: MinecraftPreparedAction) -> int:
        self._require_same(prepared)
        if self._phase != 2:
            raise K1InvariantError("execution submission is out of order")
        self._phase = 3
        return self._phase

    def _require_same(self, prepared: MinecraftPreparedAction) -> None:
        if prepared is not self._prepared:
            raise K1InvariantError("prepared action object was reconstructed or substituted")

    def retains(self, prepared: MinecraftPreparedAction) -> bool:
        return prepared is self._prepared


def _request_snapshot(prepared: MinecraftPreparedAction) -> dict[str, Any]:
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


def _witness_roots(decision) -> list[str]:
    return [root.root_id for witness in decision.witnesses for root in witness.roots][:128]


def _last_authority_sequence(runtime: MinecraftEACRuntime) -> int:
    records = runtime.authority.audit_snapshot(limit=256)
    return records[-1].sequence if records else 0


def _artifact_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def semantic_dependency_intersection(
    admission_dependency_ids: list[str] | tuple[str, ...] | set[str],
    mutation_dependency_ids: list[str] | tuple[str, ...] | set[str],
) -> tuple[str, ...]:
    """Return the fixture-visible action/mutation dependency intersection."""
    return tuple(sorted(set(admission_dependency_ids).intersection(mutation_dependency_ids)))


def _validate_trace(trace: dict[str, Any]) -> None:
    if not trace["r_p"]["EAdm"]:
        raise K1InvariantError("exact action was not admissible at r_p")
    if not trace["exact_action_preserved"]:
        raise K1InvariantError("prepared exact action changed between r_p and r_e")
    for field in ("candidate_id", "attempt_id", "exact_request_digest", "action",
                  "arguments", "target"):
        if trace["r_p"][field] != trace["r_e"][field]:
            raise K1InvariantError(f"exact action field changed: {field}")
    if not trace["r_p"]["sequence"] < trace["r_d"]["sequence"] < trace["r_e"]["sequence"]:
        raise K1InvariantError("phase sequence is not monotonic")
    if not trace["r_p"]["authority_sequence"] < trace["r_d"]["authority_sequence"]:
        raise K1InvariantError("authority did not record mutation after admission")
    if not trace["r_p"]["authority_epoch"] < trace["r_d"]["authority_epoch"]:
        raise K1InvariantError("authoritative epistemic revision did not advance")
    counters = trace["planner_freeze"]
    if any(counters[name] != 0 for name in (
        "planner_calls", "llm_calls", "controller_redecisions", "action_regenerations",
    )):
        raise K1InvariantError("planner freeze was violated")
    if trace["r_e"]["EnvPre_oracle"] is not True or trace["r_e"]["SecPre_oracle"] is not True:
        raise K1InvariantError("detached effect-time precondition oracle did not pass")
    if trace["world_state_unchanged"] is not True:
        raise K1InvariantError("Minecraft world fixture changed during planner freeze")
    bindings = trace["semantic_bindings"]
    if bindings["support_policy"] != FROZEN_SUPPORT_POLICY:
        raise K1InvariantError("frozen SupportPolicy identity changed")
    if bindings["source_profile"] != FROZEN_SOURCE_PROFILE:
        raise K1InvariantError("frozen SourceProfile identity changed")
    if bindings["classification_digest"] != FROZEN_CLASSIFICATION_DIGEST:
        raise K1InvariantError("frozen EPre classification identity changed")

    condition = trace["condition"]
    if condition in {K1_ADVISORY_RELEVANT, K1_AUTHORITY_RELEVANT}:
        if trace["r_d"]["current_EAdm"] is not False:
            raise K1InvariantError("relevant supersession did not make current EAdm false")
        if trace["r_d"]["old_root_current"] is not False:
            raise K1InvariantError("superseded positive root remained current")
        if trace["r_d"]["new_root_current"] is not True:
            raise K1InvariantError("opposite-polarity replacement is not current")
        if trace["r_d"]["relevant_action_dependency_changed"] is not True:
            raise K1InvariantError("relevant supersession did not intersect the action manifest")
    elif trace["r_d"]["current_EAdm"] is not True:
        raise K1InvariantError("unrelated mutation changed current EAdm")
    elif trace["r_d"]["relevant_action_dependency_changed"] is not False:
        raise K1InvariantError("unrelated mutation intersected the action manifest")

    if condition == K1_ADVISORY_RELEVANT:
        if not trace["r_e"]["execution_allowed"] or not trace["r_e"]["native_effect_reached"]:
            raise K1InvariantError("Advisory did not execute the non-admissible exact action")
        if trace["r_e"]["execution_would_block"] is not True:
            raise K1InvariantError("Advisory did not record would_block for current EAdm=false")
    elif condition == K1_AUTHORITY_RELEVANT:
        if trace["r_e"]["execution_allowed"] or trace["r_e"]["native_effect_reached"]:
            raise K1InvariantError("Authority allowed the stale exact action")
        if trace["r_e"]["rejection_reason"] != "stale":
            raise K1InvariantError("Authority rejection was not EAC freshness rejection")
    elif not trace["r_e"]["execution_allowed"] or not trace["r_e"]["native_effect_reached"]:
        raise K1InvariantError("unrelated mutation spuriously rejected the action")


def run_k1_condition(condition: str, *, artifact_path: str | Path | None = None) -> dict[str, Any]:
    """Run one bounded K1 fixture and return its read-only trace projection."""
    if condition not in K1_CONDITIONS:
        raise ValueError(f"unknown K1 condition: {condition}")

    relevant = condition != K1_AUTHORITY_UNRELATED
    mode = "dual_dag_advisory" if condition == K1_ADVISORY_RELEVANT else "dual_dag_authority"
    counters = PlannerFreezeCounters()
    synchronization_point = K1DeterministicSynchronizationPoint()
    world = MappingProxyType({"target": (1, 2, 3), "target_present": True, "diggable": True})
    def world_snapshot() -> dict[str, Any]:
        return {
            "target": list(world["target"]),
            "target_present": world["target_present"],
            "diggable": world["diggable"],
        }
    world_at_admission = world_snapshot()
    detached_oracle_calls = {"env": 0, "sec": 0}
    gateway_precheck_calls = {"env": 0, "sec": 0}

    def detached_env_pre_oracle(request) -> bool:
        detached_oracle_calls["env"] += 1
        target = json.loads(request.identity_bytes())["target"]
        return (tuple(target.get(axis) for axis in ("x", "y", "z")) == world["target"]
                and world["target_present"] and world["diggable"])

    def detached_sec_pre_oracle(unused_request) -> bool:
        detached_oracle_calls["sec"] += 1
        return True

    def gateway_env_precheck(request) -> bool:
        gateway_precheck_calls["env"] += 1
        target = json.loads(request.identity_bytes())["target"]
        return (tuple(target.get(axis) for axis in ("x", "y", "z")) == world["target"]
                and world["target_present"] and world["diggable"])

    def gateway_sec_precheck(unused_request) -> bool:
        gateway_precheck_calls["sec"] += 1
        return True

    runtime = MinecraftEACRuntime(
        mode=mode,
        run_id="k1-" + condition.lower(),
        env_prechecks={"MineBlock": gateway_env_precheck},
        sec_prechecks={"MineBlock": gateway_sec_precheck},
    )
    support_root = runtime.ingest_target_observation(
        "Alice", "MineBlock", {"x": 1, "y": 2, "z": 3}, revision=1)
    native_calls: list[dict[str, Any]] = []

    def native_mine(**kwargs):
        native_calls.append(dict(kwargs))
        return {"status": True, "position": [kwargs["x"], kwargs["y"], kwargs["z"]]}

    prepared = runtime.prepare_tool("MineBlock", native_mine, (), {
        "player_name": "Alice", "x": 1, "y": 2, "z": 3,
        "emotion": [], "murmur": "",
    })
    candidate = runtime.authority._candidates[prepared.request.candidate_id]
    admission = prepared.permit
    if mode == "dual_dag_advisory":
        admission = runtime.authority.shadow_permit(prepared.request.candidate_id)
        candidate = runtime.authority._candidates[prepared.request.candidate_id]
    decision = candidate.evaluation
    if decision is None:
        raise K1InvariantError("admission did not evaluate EPre")
    r_p = {
        "sequence": synchronization_point.admission(prepared),
        "authority_sequence": _last_authority_sequence(runtime),
        "authority_epoch": runtime.authority.epoch,
        **_request_snapshot(prepared),
        "EAdm": decision.admissible,
        "witness_root_ids": _witness_roots(decision),
        "dependency_fingerprint": admission.fingerprint,
        "dependency_ids": [item.dependency_id for item in admission.manifest.expectations],
        "permit_or_shadow_id": admission.permit_id,
        "permit_or_shadow_state": admission.lifecycle.value,
    }

    if relevant:
        replacement = runtime.ingest_actor_record(
            actor_id="Alice",
            proposition=replace(candidate.epre[0], polarity=False),
            record_type="direct_observation",
            source="minecraft-visible-observation",
            revision=2,
            supersedes=(support_root.root_id,),
        )
        mutation_type = "opposite_polarity_explicit_supersession"
        superseded_root = support_root.root_id
    else:
        unrelated = Proposition(PropositionKey(
            "minecraft", "weather_visible", ("rain",), "current"))
        replacement = runtime.ingest_actor_record(
            actor_id="Alice",
            proposition=unrelated,
            record_type="direct_observation",
            source="minecraft-visible-weather",
            revision=2,
        )
        mutation_type = "unrelated_actor_visible_update"
        superseded_root = None

    current = runtime.authority.evaluate(prepared.request.candidate_id)
    old_root = runtime.authority._roots[support_root.root_id]
    mutation_dependency_ids = {
        "evidence:" + replacement.root_id,
    }
    if superseded_root is not None:
        mutation_dependency_ids.add("evidence:" + superseded_root)
    manifest_dependency_ids = set(r_p["dependency_ids"])
    intersecting_dependency_ids = semantic_dependency_intersection(
        manifest_dependency_ids, mutation_dependency_ids)
    r_d = {
        "sequence": synchronization_point.revision(prepared),
        "authority_sequence": _last_authority_sequence(runtime),
        "authority_epoch": runtime.authority.epoch,
        "mutation_type": mutation_type,
        "superseding_root": replacement.root_id,
        "superseded_root": superseded_root,
        "polarity": replacement.proposition.polarity,
        "source_stream_revision": replacement.source_stream_revision,
        "current_EAdm": current.admissible,
        "reasons": list(current.reasons),
        "old_root_current": old_root.current,
        "new_root_current": replacement.current,
        "mutation_dependency_ids": sorted(mutation_dependency_ids),
        "intersecting_dependency_ids": list(intersecting_dependency_ids),
        "relevant_action_dependency_changed": bool(intersecting_dependency_ids),
    }
    admission_fresh_at_re = (
        runtime.authority.shadow_fresh(prepared.request, admission)
        if mode == "dual_dag_advisory"
        else runtime.authority.permit(admission.permit_id).lifecycle is PermitLifecycle.ISSUED
    )
    env_oracle_result = detached_env_pre_oracle(prepared.request) is True
    sec_oracle_result = detached_sec_pre_oracle(prepared.request) is True
    world_before_execution = world_snapshot()
    r_e = {
        "sequence": synchronization_point.execution_submission(prepared),
        "authority_sequence_before_execution": _last_authority_sequence(runtime),
        "authority_epoch_before_execution": runtime.authority.epoch,
        **_request_snapshot(prepared),
        "current_EAdm": current.admissible,
        "admission_permit_or_shadow_fresh": admission_fresh_at_re,
        "EnvPre_oracle": env_oracle_result,
        "SecPre_oracle": sec_oracle_result,
    }

    execution_allowed = False
    rejection_reason = None
    result = None
    try:
        result = runtime.execute_prepared(prepared)
        execution_allowed = True
    except MinecraftEACError as exc:
        rejection_reason = str(exc)

    attempt = next(iter(runtime.authority.attempt_snapshot()), None)
    audit = runtime.authority.audit_snapshot(limit=256)
    r_e.update({
        "authority_sequence_after_execution": _last_authority_sequence(runtime),
        "execution_allowed": execution_allowed,
        "rejection_reason": rejection_reason,
        "execution_would_block": attempt.would_block if attempt is not None else None,
        "gateway_env_precheck_calls": gateway_precheck_calls["env"],
        "gateway_sec_precheck_calls": gateway_precheck_calls["sec"],
        "native_effect_reached": bool(native_calls),
        "permit_staled_before_submission": any(record.event == "permit_stale" for record in audit),
        "effect_rejected_stale_audit_event": any(
            record.event == "effect_rejected_stale" for record in audit),
        "execution_rejected_by_gateway": not execution_allowed and rejection_reason == "stale",
    })
    trace = {
        "schema_version": "minecraft-k1-f1-trace/1",
        "scenario": ("f1_opposite_polarity_explicit_supersession" if relevant
                     else "c1_unrelated_actor_visible_mutation"),
        "condition": condition,
        "mode": mode,
        "scheduler": {
            "kind": "controlled_adversarial_synchronization_point",
            "fixture_only": True,
            "natural_runtime_gap_claimed": False,
        },
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
            "epre": {
                "identity": candidate.epre_ref.identity,
                "version": candidate.epre_ref.version,
                "digest": candidate.epre_ref.digest,
            },
        },
        "oracle_binding": {
            "identity": "minecraft-k1-read-only-world-oracle",
            "version": 1,
            "authority_input": False,
            "admission_world": world_at_admission,
            "effect_time_world": world_before_execution,
            "admission_world_digest": _artifact_digest(world_at_admission),
            "effect_time_world_digest": _artifact_digest(world_before_execution),
        },
        "r_p": r_p,
        "r_d": r_d,
        "planner_freeze": counters.artifact(),
        "r_e": r_e,
        "same_prepared_object": synchronization_point.retains(prepared),
        "exact_action_preserved": (
            synchronization_point.retains(prepared)
            and r_p["exact_request_digest"] == r_e["exact_request_digest"]
        ),
        "world_state_unchanged": world_at_admission == world_before_execution == world_snapshot(),
        "detached_oracle_calls": detached_oracle_calls,
        "outcome": (
            "advisory_current_nonadmissible_exact_action_executed"
            if condition == K1_ADVISORY_RELEVANT and execution_allowed
            else "authority_stale_rejected" if condition == K1_AUTHORITY_RELEVANT and not execution_allowed
            else "unrelated_retained" if condition == K1_AUTHORITY_UNRELATED and execution_allowed
            else "unexpected"
        ),
        "native_effect_count": len(native_calls),
        "native_result_status": result.get("status") if isinstance(result, dict) else None,
        "read_only_projection": True,
        "oracle_state_included_in_authority": False,
        "bounded": True,
    }
    _validate_trace(trace)
    if artifact_path is not None:
        atomic_write_json(artifact_path, trace)
    return trace
