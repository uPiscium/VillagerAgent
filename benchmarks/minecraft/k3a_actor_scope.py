"""Phase K3a same-proposition actor-scoped selective invalidation fixture."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from benchmarks.common.eac.model import PermitLifecycle
from benchmarks.minecraft.eac_runtime import (
    MinecraftEACError,
    MinecraftEACRuntime,
    MinecraftPreparedAction,
)
from benchmarks.minecraft.k1_f1 import (
    FROZEN_CLASSIFICATION_DIGEST,
    FROZEN_SOURCE_PROFILE,
    FROZEN_SUPPORT_POLICY,
    semantic_dependency_intersection,
)
from benchmarks.minecraft.k2_dependency_ablation import (
    GlobalRevisionInput,
    SemanticDependencySignalInput,
    evaluate_m2,
    evaluate_m3,
)
from env.runtime_paths import atomic_write_json

ACTORS = ("Alice", "Bob")
EXACT_FIELDS = ("candidate_id", "attempt_id", "exact_request_digest", "action", "arguments", "target")


class K3aInvariantError(AssertionError):
    pass


class K3aSynchronizationPoint:
    """Fixture-only three-phase barrier retaining two exact prepared actions."""

    def __init__(self) -> None:
        self._prepared: dict[str, MinecraftPreparedAction] = {}
        self._phase = 0
        self._submitted: set[str] = set()

    def admission(self, prepared: dict[str, MinecraftPreparedAction]) -> int:
        if self._phase != 0 or self._prepared:
            raise K3aInvariantError("multi-actor admission already recorded")
        if set(prepared) != set(ACTORS):
            raise K3aInvariantError("both actor actions are required")
        self._prepared = dict(prepared)
        self._phase = 1
        return self._phase

    def revision(self) -> int:
        if self._phase != 1:
            raise K3aInvariantError("actor revision is out of order")
        self._phase = 2
        return self._phase

    def execution_submission(self, actor: str, prepared: MinecraftPreparedAction) -> int:
        if self._phase != 2 or actor in self._submitted:
            raise K3aInvariantError("actor execution submission is out of order")
        if self._prepared.get(actor) is not prepared:
            raise K3aInvariantError("prepared actor action was reconstructed or substituted")
        self._submitted.add(actor)
        return 3

    def retains(self, actor: str, prepared: MinecraftPreparedAction) -> bool:
        return self._prepared.get(actor) is prepared


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


def _permit_state(runtime: MinecraftEACRuntime, prepared: MinecraftPreparedAction) -> str:
    return runtime.authority.permit(prepared.permit.permit_id).lifecycle.value


def _last_authority_sequence(runtime: MinecraftEACRuntime) -> int:
    records = runtime.authority.audit_snapshot(limit=256)
    return records[-1].sequence if records else 0


def _validate_case(trace: dict[str, Any]) -> None:
    revised_actor = trace["revised_actor"]
    unaffected_actor = next(actor for actor in ACTORS if actor != revised_actor)
    if not trace["common_proposition"]["same_tracked_proposition"]:
        raise K3aInvariantError("actors did not use the same tracked proposition")
    if not trace["world_state_unchanged"]:
        raise K3aInvariantError("world fixture changed")
    if not trace["r_p_sequence"] < trace["r_d_sequence"] < trace["r_e_sequence"]:
        raise K3aInvariantError("K3a phase sequence is not monotonic")
    revision = trace["semantic_revision"]
    if not all((
        revision["old_polarity"] is True,
        revision["new_polarity"] is False,
        revision["same_stable_proposition_key"] is True,
        revision["new_source_stream_revision"] > revision["old_source_stream_revision"],
        revision["supersedes"] == [revision["old_root_id"]],
        revision["old_root_current_after"] is False,
        revision["new_root_current"] is True,
        revision["visibility"] == [revised_actor],
    )):
        raise K3aInvariantError("actor revision is not the required explicit supersession")
    if trace["execution_order"] != [revised_actor, unaffected_actor]:
        raise K3aInvariantError("affected action was not submitted first")
    if trace["actors"][revised_actor]["r_e"]["submission_ordinal"] != 1:
        raise K3aInvariantError("affected submission ordinal is invalid")
    if trace["actors"][unaffected_actor]["r_e"]["submission_ordinal"] != 2:
        raise K3aInvariantError("unaffected submission ordinal is invalid")
    for name in ("planner_calls", "llm_calls", "controller_redecisions", "action_regenerations"):
        if trace["planner_freeze"][name] != 0:
            raise K3aInvariantError("planner freeze was violated")
    for actor in ACTORS:
        record = trace["actors"][actor]
        if not record["r_p"]["EAdm"]:
            raise K3aInvariantError(f"{actor} was not admissible at r_p")
        if not record["exact_action_preserved"] or not record["same_prepared_object"]:
            raise K3aInvariantError(f"{actor} exact action changed")
        if any(record["r_p"][field] != record["r_e"][field] for field in EXACT_FIELDS):
            raise K3aInvariantError(f"{actor} exact request fields changed")
        if not record["r_e"]["EnvPre_oracle"] or not record["r_e"]["SecPre_oracle"]:
            raise K3aInvariantError(f"{actor} detached legality oracle failed")


def _case_success(trace: dict[str, Any]) -> bool:
    revised_actor = trace["revised_actor"]
    unaffected_actor = trace["unaffected_actor"]
    affected = trace["actors"][revised_actor]
    unaffected = trace["actors"][unaffected_actor]
    expected_selective = {revised_actor: "reject", unaffected_actor: "allow"}
    return all((
        affected["r_d"]["current_EAdm"] is False,
        affected["r_d"]["permit_state"] == "stale",
        affected["r_d"]["support_root_current"] is False,
        affected["r_e"]["execution_allowed"] is False,
        affected["r_e"]["native_callable_reached"] is False,
        unaffected["r_d"]["current_EAdm"] is True,
        unaffected["r_d"]["permit_state"] == "issued",
        unaffected["r_d"]["support_root_current"] is True,
        unaffected["r_e"]["execution_allowed"] is True,
        unaffected["r_e"]["native_callable_reached"] is True,
        trace["cross_actor_dependency_leak"] is False,
        trace["cross_actor_invalidation_leak"] is False,
        trace["comparisons"]["global_revision"] == {
            revised_actor: "reject", unaffected_actor: "reject"},
        trace["comparisons"]["semantic_dependency"] == expected_selective,
        trace["comparisons"]["existing_authority"] == expected_selective,
    ))


def run_k3a_case(revised_actor: str, *, artifact_path: str | Path | None = None) -> dict[str, Any]:
    """Run one symmetric K3a case in one shared Authority runtime."""
    if revised_actor not in ACTORS:
        raise ValueError("revised actor must be Alice or Bob")
    unaffected_actor = next(actor for actor in ACTORS if actor != revised_actor)
    planner_freeze = {
        "instrumentation_scope": "controlled_k3a_fixture",
        "execution_model": "direct_retained_prepared_actions",
        "planner_instantiated": False,
        "llm_instantiated": False,
        "controller_instantiated": False,
        "planner_calls": 0,
        "llm_calls": 0,
        "controller_redecisions": 0,
        "action_regenerations": 0,
    }
    barrier = K3aSynchronizationPoint()
    world = MappingProxyType({"target": (1, 2, 3), "target_present": True, "diggable": True})

    def world_snapshot() -> dict[str, Any]:
        return {
            "target": list(world["target"]),
            "target_present": world["target_present"],
            "diggable": world["diggable"],
        }

    world_at_admission = world_snapshot()
    candidate_actor: dict[str, str] = {}
    gateway_calls = {actor: {"env": 0, "sec": 0} for actor in ACTORS}

    def request_actor(request) -> str:
        return candidate_actor[request.candidate_id]

    def env_precheck(request) -> bool:
        actor = request_actor(request)
        gateway_calls[actor]["env"] += 1
        target = json.loads(request.identity_bytes())["target"]
        return (tuple(target[axis] for axis in ("x", "y", "z")) == world["target"]
                and world["target_present"] and world["diggable"])

    def sec_precheck(request) -> bool:
        gateway_calls[request_actor(request)]["sec"] += 1
        return True

    runtime = MinecraftEACRuntime(
        mode="dual_dag_authority",
        run_id="k3a-" + revised_actor.lower(),
        env_prechecks={"MineBlock": env_precheck},
        sec_prechecks={"MineBlock": sec_precheck},
    )
    roots = {
        actor: runtime.ingest_target_observation(
            actor, "MineBlock", {"x": 1, "y": 2, "z": 3})
        for actor in ACTORS
    }
    initial_evidence_snapshot = {
        actor: {
            "root_id": roots[actor].root_id,
            "visible_to": list(roots[actor].visible_to),
            "polarity": roots[actor].proposition.polarity,
            "current": roots[actor].current,
        }
        for actor in ACTORS
    }
    native_calls = {actor: 0 for actor in ACTORS}

    def native_mine(**kwargs):
        native_calls[kwargs["player_name"]] += 1
        return {"status": True, "position": [kwargs["x"], kwargs["y"], kwargs["z"]]}

    prepared = {
        actor: runtime.prepare_tool("MineBlock", native_mine, (), {
            "player_name": actor, "x": 1, "y": 2, "z": 3,
            "emotion": [], "murmur": "",
        })
        for actor in ACTORS
    }
    candidate_actor.update({
        prepared[actor].request.candidate_id: actor for actor in ACTORS
    })
    r_p_sequence = barrier.admission(prepared)
    admission_epoch = runtime.authority.epoch
    rp: dict[str, dict[str, Any]] = {}
    admission_manifest_ids: dict[str, set[str]] = {}
    witness_roots: dict[str, set[str]] = {}
    for actor in ACTORS:
        decision = runtime.authority.evaluate(prepared[actor].request.candidate_id)
        roots_used = {
            root.root_id for witness in decision.witnesses for root in witness.roots
        }
        witness_roots[actor] = roots_used
        admission_manifest_ids[actor] = {
            item.dependency_id for item in prepared[actor].permit.manifest.expectations
        }
        rp[actor] = {
            **_request_snapshot(prepared[actor]),
            "EAdm": decision.admissible,
            "witness_root_ids": sorted(roots_used),
            "dependency_fingerprint": prepared[actor].permit.fingerprint,
            "permit_state": _permit_state(runtime, prepared[actor]),
        }
    rp_authority_sequence = _last_authority_sequence(runtime)

    revised_root = roots[revised_actor]
    replacement = runtime.ingest_actor_record(
        actor_id=revised_actor,
        proposition=replace(revised_root.proposition, polarity=False),
        record_type="direct_observation",
        source="minecraft-visible-observation",
        revision=3,
        supersedes=(revised_root.root_id,),
    )
    r_d_sequence = barrier.revision()
    mutation_dependency_ids = {
        "evidence:" + revised_root.root_id,
        "evidence:" + replacement.root_id,
    }
    intersections = {
        actor: semantic_dependency_intersection(
            admission_manifest_ids[actor], mutation_dependency_ids)
        for actor in ACTORS
    }
    rd: dict[str, dict[str, Any]] = {}
    current_decisions = {}
    for actor in ACTORS:
        current = runtime.authority.evaluate(prepared[actor].request.candidate_id)
        current_decisions[actor] = current
        rd[actor] = {
            "changed_evidence_root": replacement.root_id,
            "superseded_root": revised_root.root_id,
            "evidence_visibility": list(replacement.visible_to),
            "affected_actor": revised_actor,
            "current_EAdm": current.admissible,
            "permit_state": _permit_state(runtime, prepared[actor]),
            "support_root_current": runtime.authority._roots[roots[actor].root_id].current,
            "intersecting_dependency_ids": list(intersections[actor]),
            "relevant_action_dependency_changed": bool(intersections[actor]),
        }
    rd_authority_sequence = _last_authority_sequence(runtime)

    world_before_execution = world_snapshot()
    detached_oracles = {
        actor: {
            "EnvPre": (
                tuple(rp[actor]["target"][axis] for axis in ("x", "y", "z")) == world["target"]
                and world["target_present"] and world["diggable"]),
            "SecPre": True,
        }
        for actor in ACTORS
    }
    re: dict[str, dict[str, Any]] = {}
    execution_order = (revised_actor, unaffected_actor)
    for submission_ordinal, actor in enumerate(execution_order, start=1):
        barrier.execution_submission(actor, prepared[actor])
        snapshot = _request_snapshot(prepared[actor])
        permit_fresh = _permit_state(runtime, prepared[actor]) == PermitLifecycle.ISSUED.value
        authority_sequence_before_execution = _last_authority_sequence(runtime)
        authority_epoch_before_execution = runtime.authority.epoch
        allowed = False
        rejection_reason = None
        try:
            runtime.execute_prepared(prepared[actor])
            allowed = True
        except MinecraftEACError as exc:
            rejection_reason = str(exc)
        re[actor] = {
            **snapshot,
            "current_EAdm": current_decisions[actor].admissible,
            "permit_fresh": permit_fresh,
            "authority_sequence_before_execution": authority_sequence_before_execution,
            "authority_epoch_before_execution": authority_epoch_before_execution,
            "submission_ordinal": submission_ordinal,
            "EnvPre_oracle": detached_oracles[actor]["EnvPre"],
            "SecPre_oracle": detached_oracles[actor]["SecPre"],
            "execution_allowed": allowed,
            "rejection_reason": rejection_reason,
            "gateway_env_precheck_calls": gateway_calls[actor]["env"],
            "gateway_sec_precheck_calls": gateway_calls[actor]["sec"],
            "native_callable_reached": native_calls[actor] == 1,
        }

    global_comparison = {
        actor: ("allow" if evaluate_m2(GlobalRevisionInput(
            admission_epoch, re[actor]["authority_epoch_before_execution"])).allow else "reject")
        for actor in ACTORS
    }
    semantic_comparison = {
        actor: ("allow" if evaluate_m3(SemanticDependencySignalInput(
            bool(intersections[actor]))).allow else "reject")
        for actor in ACTORS
    }
    authority_comparison = {
        actor: "allow" if re[actor]["execution_allowed"] else "reject" for actor in ACTORS
    }
    cross_actor_dependency_leak = (
        roots[unaffected_actor].root_id in witness_roots[revised_actor]
        or roots[revised_actor].root_id in witness_roots[unaffected_actor]
    )
    cross_actor_invalidation_leak = (
        not rd[unaffected_actor]["current_EAdm"]
        or rd[unaffected_actor]["permit_state"] != "issued"
        or bool(intersections[unaffected_actor])
    )
    actor_records = {}
    for actor in ACTORS:
        actor_records[actor] = {
            "actor_id": actor,
            "r_p": rp[actor],
            "r_d": rd[actor],
            "r_e": re[actor],
            "same_prepared_object": barrier.retains(actor, prepared[actor]),
            "exact_action_preserved": all(
                rp[actor][field] == re[actor][field] for field in EXACT_FIELDS),
        }
    trace = {
        "schema_version": "minecraft-k3a-actor-scope/1",
        "scenario": "same_proposition_actor_private_selective_invalidation",
        "revised_actor": revised_actor,
        "unaffected_actor": unaffected_actor,
        "common_proposition": {
            "namespace": revised_root.proposition.key.namespace,
            "predicate": revised_root.proposition.key.predicate,
            "arguments": list(revised_root.proposition.key.arguments),
            "temporal_scope": revised_root.proposition.key.temporal_scope,
            "same_tracked_proposition": (
                roots["Alice"].proposition.key == roots["Bob"].proposition.key),
        },
        "initial_evidence": {
            actor: initial_evidence_snapshot[actor] for actor in ACTORS
        },
        "semantic_revision": {
            "affected_actor": revised_actor,
            "old_root_id": revised_root.root_id,
            "new_root_id": replacement.root_id,
            "old_polarity": revised_root.proposition.polarity,
            "new_polarity": replacement.proposition.polarity,
            "same_stable_proposition_key": (
                revised_root.proposition.key == replacement.proposition.key),
            "old_source_stream_revision": revised_root.source_stream_revision,
            "new_source_stream_revision": replacement.source_stream_revision,
            "supersedes": list(replacement.supersedes),
            "old_root_current_after": runtime.authority._roots[revised_root.root_id].current,
            "new_root_current": runtime.authority._roots[replacement.root_id].current,
            "visibility": list(replacement.visible_to),
        },
        "actors": actor_records,
        "r_p_sequence": r_p_sequence,
        "r_p_authority_sequence": rp_authority_sequence,
        "r_d_sequence": r_d_sequence,
        "r_d_authority_sequence": rd_authority_sequence,
        "r_e_sequence": 3,
        "planner_freeze": planner_freeze,
        "execution_order": list(execution_order),
        "comparisons": {
            "global_revision": global_comparison,
            "semantic_dependency": semantic_comparison,
            "existing_authority": authority_comparison,
            "semantic_checker_is_new_mechanism": False,
            "semantic_signal_scope": "load-bearing admission witness dependency intersection",
        },
        "cross_actor_dependency_leak": cross_actor_dependency_leak,
        "cross_actor_invalidation_leak": cross_actor_invalidation_leak,
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
        "production_runtime_modified": False,
        "read_only_projection": True,
        "bounded": True,
    }
    if trace["semantic_bindings"]["support_policy"] != FROZEN_SUPPORT_POLICY:
        raise K3aInvariantError("frozen SupportPolicy changed")
    if trace["semantic_bindings"]["source_profile"] != FROZEN_SOURCE_PROFILE:
        raise K3aInvariantError("frozen SourceProfile changed")
    if trace["semantic_bindings"]["classification_digest"] != FROZEN_CLASSIFICATION_DIGEST:
        raise K3aInvariantError("frozen EPre classification changed")
    trace["case_success"] = _case_success(trace)
    _validate_case(trace)
    if artifact_path is not None:
        atomic_write_json(artifact_path, trace)
    return trace


def run_k3a_selective_invalidation(*, artifact_path: str | Path | None = None) -> dict[str, Any]:
    """Run both symmetric actor-private revisions and classify K3a."""
    alice = run_k3a_case("Alice")
    bob = run_k3a_case("Bob")
    dependency_leaks = sum(case["cross_actor_dependency_leak"] for case in (alice, bob))
    invalidation_leaks = sum(case["cross_actor_invalidation_leak"] for case in (alice, bob))
    successful_cases = sum(case["case_success"] for case in (alice, bob))
    same_proposition = all(case["common_proposition"]["same_tracked_proposition"]
                           for case in (alice, bob))
    verdict = ("BLOCKED" if not same_proposition else
               "K3A_PASS" if successful_cases == 2 and dependency_leaks == invalidation_leaks == 0
               else "K3A_FAIL")
    result = {
        "schema_version": "minecraft-k3a-selective-invalidation/1",
        "scenario": "symmetric_same_proposition_actor_scope",
        "cases": {"Alice": alice, "Bob": bob},
        "metrics": {
            "symmetric_case_count": 2,
            "successful_case_count": successful_cases,
            "dependency_leak_count": dependency_leaks,
            "dependency_leak_rate": dependency_leaks / 2,
            "invalidation_leak_count": invalidation_leaks,
            "invalidation_leak_rate": invalidation_leaks / 2,
        },
        "verdict": verdict,
        "production_runtime_modified": False,
        "read_only_projection": True,
        "bounded": True,
    }
    if artifact_path is not None:
        atomic_write_json(artifact_path, result)
    return result
