"""Bounded, controlled K6 trial fixtures.

This module deliberately stops at the controlled revision point during
construction.  ``submit`` is the only operation which crosses the native
effect boundary; it submits the exact object retained by the trial.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any

from benchmarks.common.eac import Proposition, PropositionKey
from benchmarks.common.eac.authority import _proposition_slot
from benchmarks.common.eac.canonical import canonical_bytes
from benchmarks.common.eac.model import PermitLifecycle
from benchmarks.minecraft.eac_runtime import MinecraftEACError, MinecraftEACRuntime
from benchmarks.minecraft.k2_dependency_ablation import (
    AdmissionOnlyInput, ExactRequestOnlyInput, ExistingAuthorityInput,
    GlobalRevisionInput, SemanticDependencySignalInput,
    evaluate_m0, evaluate_m1, evaluate_m2, evaluate_m3, evaluate_m4,
)
from benchmarks.minecraft.k6_protocol import (
    EXACT_FIELDS, K6CellSpec,
    build_k6_cells, load_k6_inventory, load_k6_protocol, validate_k6_trace,
)


def _snapshot(prepared) -> dict[str, Any]:
    request = prepared.request
    identity = json.loads(request.identity_bytes().decode("utf-8"))
    return {
        "candidate_id": request.candidate_id,
        "attempt_id": request.attempt_id,
        "exact_request_digest": "sha256:" + hashlib.sha256(request.identity_bytes()).hexdigest(),
        "action": {"identity": request.action.identity, "version": request.action.version,
                   "digest": request.action.digest},
        "arguments": identity["arguments"],
        "target": identity["target"],
    }


def _decision_artifact(decision) -> dict[str, Any]:
    return decision.artifact()


def _mechanism_artifacts(rp, rd, re, mode) -> dict[str, dict[str, Any]]:
    models = {
        "M0": _decision_artifact(evaluate_m0(AdmissionOnlyInput(rp["EAdm"]))),
        "M1": _decision_artifact(evaluate_m1(ExactRequestOnlyInput(
            tuple(rp[field] for field in EXACT_FIELDS),
            tuple(re[field] for field in EXACT_FIELDS)))),
        "M2": _decision_artifact(evaluate_m2(GlobalRevisionInput(
            rp["authority_epoch"], re["authority_epoch_before_execution"]))),
        "M3": _decision_artifact(evaluate_m3(SemanticDependencySignalInput(
            rd["relevant_action_dependency_changed"]))),
    }
    if mode == "dual_dag_authority":
        models["M4"] = _decision_artifact(evaluate_m4(ExistingAuthorityInput(
            re["execution_allowed"], re["rejection_reason"])))
    else:
        models["M4"] = {
            "decision": "not_applicable",
            "reason": "existing_authority_not_run_in_advisory_mode",
            "inputs_used": [],
            "relevant_action_dependency_changed": None,
        }
    return models


def _intersection(left, right) -> tuple[str, ...]:
    return tuple(sorted(set(left).intersection(right)))


def _content_digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


@dataclass
class K6Trial:
    cell: K6CellSpec
    _runtime: MinecraftEACRuntime
    _prepared_actions: dict[str, Any]
    _prepared_objects: tuple[Any, ...]
    _admissions: dict[str, Any]
    snapshots: dict[str, dict[str, Any]]
    counters: dict[str, Any]
    mutation_state: dict[str, Any]
    r_p: dict[str, Any]
    r_d: dict[str, Any]
    _native_calls: dict[str, list[dict[str, Any]]]
    pairing_digest: str
    _submitted: bool = False

    @property
    def declared_sec_pre(self) -> bool:
        return False

    @property
    def native_calls(self):
        return {actor: tuple(calls) for actor, calls in self._native_calls.items()}

    @property
    def same_prepared_object(self) -> bool:
        actors = tuple(self._prepared_actions)
        return all(self._prepared_actions[actor] is self._prepared_objects[index]
                   for index, actor in enumerate(actors))

    def _permit_or_shadow_fresh(self, actor: str) -> bool:
        action = self._prepared_actions[actor]
        admission = self._admissions[actor]
        if self._runtime.mode == "dual_dag_advisory":
            return self._runtime.authority.shadow_fresh(action.request, admission)
        return self._runtime.authority.permit(admission.permit_id).lifecycle is PermitLifecycle.ISSUED

    def _validate_pre_submission(self) -> None:
        load_k6_protocol()
        frozen = {candidate.cell_id: candidate for candidate in build_k6_cells()}
        if frozen.get(self.cell.cell_id) != self.cell or self._runtime.mode != self.cell.condition:
            raise RuntimeError("K6 trial identity changed before submission")
        if (self._runtime.classification_identity
                != load_k6_protocol()["semantic_bindings"]["epre_classification"]["digest"]
                or self._runtime.authority.policy.digest
                != load_k6_protocol()["semantic_bindings"]["support_policy"]["digest"]
                or self._runtime.authority.profile.digest
                != load_k6_protocol()["semantic_bindings"]["source_profile"]["digest"]):
            raise RuntimeError("K6 semantic bindings changed before submission")
        if not self.same_prepared_object:
            raise RuntimeError("K6 prepared action was reconstructed or substituted")
        if (self.r_p != self.snapshots["r_p"][self.cell.affected_actor]
                or self.r_d != self.snapshots["r_d"][self.cell.affected_actor]):
            raise RuntimeError("K6 affected phase snapshots changed before submission")
        for actor, action in self._prepared_actions.items():
            if _snapshot(action) != {name: self.snapshots["r_p"][actor][name] for name in EXACT_FIELDS}:
                raise RuntimeError("K6 exact request changed before submission")
            current = self._runtime.authority.evaluate(action.request.candidate_id)
            if current.admissible is not self.snapshots["r_d"][actor]["current_EAdm"]:
                raise RuntimeError("K6 semantic state changed after the controlled mutation")
            if self._permit_or_shadow_fresh(actor) is not self.snapshots["r_d"][actor]["permit_or_shadow_fresh"]:
                raise RuntimeError("K6 permit freshness changed before submission")
        if any(self.counters[name] != 0 for name in (
            "planner_calls", "model_calls", "controller_redecisions", "action_regenerations",
        )) or any(self.counters[name] is not False for name in (
            "planner_instantiated", "model_instantiated", "controller_instantiated",
        )):
            raise RuntimeError("K6 no-reconsideration invariant changed before submission")
        if self.r_p["EAdm"] is not True:
            raise RuntimeError("K6 action was not admissible at preparation")
        expected_mutation = {
            "S1": "opposite_polarity_explicit_supersession",
            "S2": "independent_opposite_trusted_tool_result",
            "S3": "affected_actor_explicit_supersession",
            "C1": "unrelated_weather_visible_update",
            "C2": "evaluator_only_hidden_truth_mutation",
        }[self.cell.scenario_family]
        if (self.mutation_state["mutation_type"] != expected_mutation
                or self.mutation_state["hidden_truth_ingested"] is not False):
            raise RuntimeError("K6 mutation contract changed before submission")
        if self.cell.scenario_family in {"S1", "S3"}:
            supersession = self.mutation_state.get("supersession")
            if (not isinstance(supersession, dict)
                    or supersession.get("old_polarity") is not True
                    or supersession.get("new_polarity") is not False
                    or supersession.get("same_tracked_proposition") is not True
                    or supersession.get("supersedes") != [supersession.get("old_root_id")]):
                raise RuntimeError("K6 explicit supersession changed before submission")
        if self.cell.scenario_family == "S2":
            contradiction = self.mutation_state.get("contradiction")
            if (not isinstance(contradiction, dict)
                    or contradiction.get("positive_current") is not True
                    or contradiction.get("negative_current") is not True
                    or contradiction.get("positive_supersedes") != []
                    or contradiction.get("negative_supersedes") != []):
                raise RuntimeError("K6 unresolved contradiction changed before submission")
        if self.cell.scenario_family == "C2" and (
            self.mutation_state["authority_epoch_before"]
            != self.mutation_state["authority_epoch_after"]
            or self.mutation_state["evidence_total_before"]
            != self.mutation_state["evidence_total_after"]
            or self.mutation_state["evaluator_truth_before"]
            == self.mutation_state["evaluator_truth_after"]
            or self.mutation_state["evaluator_truth_changed"] is not True
            or self.mutation_state["evaluator_truth_before_digest"]
            != _content_digest(self.mutation_state["evaluator_truth_before"])
            or self.mutation_state["evaluator_truth_after_digest"]
            != _content_digest(self.mutation_state["evaluator_truth_after"])
            or self.mutation_state["evaluator_truth_authority_input"] is not False
            or self.mutation_state["evaluator_truth_precondition_input"] is not False
        ):
            raise RuntimeError("K6 hidden evaluator truth transition is invalid")

    def submit(self) -> dict[str, Any]:
        # Fail closed on protocol/inventory/schema drift before crossing the
        # native effect boundary.
        protocol = load_k6_protocol()
        if self._submitted:
            raise RuntimeError("K6 trial submission is one-shot")
        self._validate_pre_submission()
        self._submitted = True
        mode = self._runtime.mode
        prepared = self._prepared_actions[self.cell.affected_actor]
        actor_order = tuple(self._prepared_actions)
        before_epoch = self._runtime.authority.epoch
        permit_fresh = self._permit_or_shadow_fresh(self.cell.affected_actor)
        detached_env = True
        detached_sec = True
        allowed = False
        rejection = None
        try:
            self._runtime.execute_prepared(prepared)
            allowed = True
        except MinecraftEACError as exc:
            rejection = str(exc)
        rp = self.r_p
        re = {
            **_snapshot(prepared),
            "current_EAdm": self.r_d["current_EAdm"],
            "authority_epoch_before_execution": before_epoch,
            "exact_action_submitted": True,
            "permit_or_shadow_fresh": permit_fresh,
            "EnvPre_oracle": detached_env,
            "SecPre_oracle": detached_sec,
            "execution_allowed": allowed,
            "rejection_reason": rejection,
            "native_callable_reached": bool(self._native_calls[self.cell.affected_actor]),
        }
        s3 = None
        if self.cell.scenario_family == "S3":
            affected = self.cell.affected_actor
            other = "Bob" if affected == "Alice" else "Alice"
            other_action = self._prepared_actions[other]
            other_rp = self.snapshots["r_p"][other]
            other_rd = self.snapshots["r_d"][other]
            other_before = self._native_calls[other][:]
            other_epoch_before = self._runtime.authority.epoch
            other_permit_fresh = self._permit_or_shadow_fresh(other)
            other_allowed = False
            other_rejection = None
            try:
                self._runtime.execute_prepared(other_action)
                other_allowed = True
            except MinecraftEACError as exc:
                other_rejection = str(exc)
            other_re = {
                **_snapshot(other_action),
                "current_EAdm": other_rd["current_EAdm"],
                "authority_epoch_before_execution": other_epoch_before,
                "exact_action_submitted": True,
                "permit_or_shadow_fresh": other_permit_fresh,
                "EnvPre_oracle": True,
                "SecPre_oracle": True,
                "execution_allowed": other_allowed,
                "rejection_reason": other_rejection,
                "native_callable_reached": len(self._native_calls[other]) > len(other_before),
            }
            s3 = {
                "affected_actor": affected, "unaffected_actor": other,
                "unaffected_current_EAdm": self.mutation_state["actor_current_EAdm"][other],
                "unaffected_r_p": other_rp,
                "unaffected_r_d": other_rd,
                "unaffected_r_e": other_re,
                "unaffected_same_prepared_object": self._prepared_actions[other] is other_action,
                "unaffected_exact_action_preserved": all(
                    other_rp[field] == other_re[field] for field in EXACT_FIELDS),
                "unaffected_mechanism_analysis": _mechanism_artifacts(
                    other_rp, other_rd, other_re, mode),
                "cross_actor_dependency_leak": self.mutation_state["cross_actor_dependency_leak"],
                "cross_actor_state_change_leak": self.mutation_state["cross_actor_state_change_leak"],
            }
            # The unaffected action is intentionally submitted as part of the
            # same bounded S3 trial, after the affected action.
            re["native_callable_reached"] = bool(self._native_calls[affected])
        models = _mechanism_artifacts(rp, self.r_d, re, mode)
        trace = {
            "schema_version": "minecraft-k6-cell-trace/1",
            "protocol_digest": protocol["validated_protocol_digest"],
            "inventory_digest": protocol["validated_inventory_digest"],
            "pairing_digest": self.pairing_digest,
            "cell": {name: getattr(self.cell, name) for name in
                      ("cell_id", "scenario_family", "inventory_id", "condition", "affected_actor", "matrix")},
            "semantic_bindings": protocol["semantic_bindings"],
            "r_p": rp,
            "r_d": self.r_d,
            "r_e": re,
            "actor_scope": {
                "actor_id": self.cell.affected_actor,
                "visible_to": [self.cell.affected_actor],
                "private_actor_scope": True,
            },
            "mutation": self.mutation_state,
            "exact_action": {"same_prepared_object": self.same_prepared_object,
                              "exact_action_preserved": all(rp[f] == re[f] for f in EXACT_FIELDS)},
            "no_reconsideration": self.counters,
            "s3": s3,
            "mechanism_analysis": models,
        }
        return validate_k6_trace(trace, cell=self.cell)


def construct_k6_trial(cell: K6CellSpec) -> K6Trial:
    """Construct one K6 cell through preparation and controlled mutation only."""
    # Construction is also fail-closed: a cell may not be prepared from
    # placeholders, stale classification, or unbound protocol artifacts.
    load_k6_protocol()
    frozen_cells = {candidate.cell_id: candidate for candidate in build_k6_cells()}
    if frozen_cells.get(cell.cell_id) != cell:
        raise ValueError("K6 cell is outside the frozen primary/control matrix")
    item = next((x for x in load_k6_inventory() if x.inventory_id == cell.inventory_id), None)
    if item is None:
        raise ValueError(f"unknown K6 inventory item: {cell.inventory_id}")
    mode = cell.condition
    gateway_calls = {"env": 0, "sec": 0}
    def gateway_env_pre(_request):
        gateway_calls["env"] += 1
        return True
    def gateway_sec_pre(_request):
        gateway_calls["sec"] += 1
        return True
    paired_run_id = "-".join((
        "k6", cell.scenario_family.lower(), cell.inventory_id.lower(),
        cell.affected_actor.lower(),
    ))
    runtime = MinecraftEACRuntime(
        mode=mode, run_id=paired_run_id,
        env_prechecks={item.action_identity: gateway_env_pre},
        sec_prechecks={item.action_identity: gateway_sec_pre},
    )
    actors = ("Alice", "Bob") if cell.scenario_family == "S3" else (cell.affected_actor,)
    roots: dict[str, Any] = {}
    for actor in actors:
        roots[actor] = runtime.ingest_target_observation(actor, item.action_identity, item.request())
    calls = {actor: [] for actor in actors}
    def native(**kwargs):
        actor = kwargs.get("player_name", "unknown")
        calls.setdefault(actor, []).append(dict(kwargs))
        return {"status": True}
    prepared = {}
    for actor in actors:
        kwargs = dict(item.request())
        kwargs.update(player_name=actor, emotion=[], murmur="")
        prepared[actor] = runtime.prepare_tool(item.action_identity, native, (), kwargs)
    permits = {}
    for actor, action in prepared.items():
        permits[actor] = (runtime.authority.shadow_permit(action.request.candidate_id)
                          if mode == "dual_dag_advisory" else action.permit)
    actor_rp = {}
    admission_witness_roots = {}
    admission_dependency_ids = {}
    for actor, action in prepared.items():
        actor_decision = runtime.authority.evaluate(action.request.candidate_id)
        admission_witness_roots[actor] = {
            root.root_id for witness in actor_decision.witnesses for root in witness.roots
        }
        admission_dependency_ids[actor] = {
            expectation.dependency_id for expectation in permits[actor].manifest.expectations
        }
        actor_rp[actor] = {
            **_snapshot(action),
            "EAdm": actor_decision.admissible,
            "authority_epoch": runtime.authority.epoch,
            "witness_root_ids": sorted(admission_witness_roots[actor]),
            "dependency_ids": sorted(admission_dependency_ids[actor]),
        }
    affected = prepared[cell.affected_actor]
    rp = actor_rp[cell.affected_actor]
    before_epoch, before_evidence = runtime.authority.epoch, runtime._evidence_total
    mutation_type = ""
    mutation_ids: set[str] = set()
    actor_current = {actor: True for actor in actors}
    superseded = None
    replacement = None
    evaluator_truth_before = None
    evaluator_truth_after = None
    evaluator_truth_changed = False
    if cell.scenario_family == "S1":
        replacement = runtime.ingest_actor_record(actor_id=cell.affected_actor,
            proposition=replace(roots[cell.affected_actor].proposition, polarity=False),
            record_type="direct_observation", source="minecraft-visible-observation",
            revision=2, supersedes=(roots[cell.affected_actor].root_id,))
        superseded = roots[cell.affected_actor]
        mutation_type = "opposite_polarity_explicit_supersession"
    elif cell.scenario_family == "S2":
        replacement = runtime.ingest_actor_record(actor_id=cell.affected_actor,
            proposition=replace(roots[cell.affected_actor].proposition, polarity=False),
            record_type="trusted_tool_result", source="minecraft-observation-adapter", revision=2)
        mutation_type = "independent_opposite_trusted_tool_result"
    elif cell.scenario_family == "S3":
        replacement = runtime.ingest_actor_record(actor_id=cell.affected_actor,
            proposition=replace(roots[cell.affected_actor].proposition, polarity=False),
            record_type="direct_observation", source="minecraft-visible-observation",
            revision=3, supersedes=(roots[cell.affected_actor].root_id,))
        superseded = roots[cell.affected_actor]
        mutation_type = "affected_actor_explicit_supersession"
        actor_current[cell.affected_actor] = False
    elif cell.scenario_family == "C1":
        weather = Proposition(PropositionKey("minecraft", "weather_visible", ("rain",), "current"))
        replacement = runtime.ingest_actor_record(actor_id=cell.affected_actor, proposition=weather,
            record_type="direct_observation", source="minecraft-visible-weather", revision=2)
        mutation_type = "unrelated_weather_visible_update"
    elif cell.scenario_family == "C2":
        evaluator_truth_before = {"hidden_target_available": True}
        evaluator_truth_after = {"hidden_target_available": False}
        evaluator_truth_changed = evaluator_truth_before != evaluator_truth_after
        mutation_type = "evaluator_only_hidden_truth_mutation"
    else:
        raise ValueError(f"unknown K6 scenario family: {cell.scenario_family}")
    if replacement is not None:
        mutation_ids.update(("evidence:" + replacement.root_id,))
        if superseded is not None:
            mutation_ids.add("evidence:" + superseded.root_id)
        mutation_ids.add(_proposition_slot(replacement.proposition, cell.affected_actor))
    actor_rd = {}
    intersections = {}
    for actor, action in prepared.items():
        actor_decision = runtime.authority.evaluate(action.request.candidate_id)
        actor_current[actor] = actor_decision.admissible
        intersections[actor] = _intersection(admission_dependency_ids[actor], mutation_ids)
        permit_or_shadow_fresh = (
            runtime.authority.shadow_fresh(action.request, permits[actor])
            if mode == "dual_dag_advisory"
            else runtime.authority.permit(permits[actor].permit_id).lifecycle is PermitLifecycle.ISSUED
        )
        actor_rd[actor] = {
            "current_EAdm": actor_decision.admissible,
            "authority_epoch": runtime.authority.epoch,
            "reasons": list(actor_decision.reasons),
            "mutation_type": mutation_type,
            "mutation_dependency_ids": sorted(mutation_ids),
            "intersecting_dependency_ids": list(intersections[actor]),
            "relevant_action_dependency_changed": bool(intersections[actor]),
            "permit_or_shadow_fresh": permit_or_shadow_fresh,
        }
    r_d = actor_rd[cell.affected_actor]
    other_actor = next((actor for actor in actors if actor != cell.affected_actor), None)
    cross_actor_dependency_leak = False
    cross_actor_state_change_leak = False
    if other_actor is not None:
        cross_actor_dependency_leak = (
            roots[other_actor].root_id in admission_witness_roots[cell.affected_actor]
            or roots[cell.affected_actor].root_id in admission_witness_roots[other_actor]
            or bool(intersections[other_actor])
        )
        unaffected_action = prepared[other_actor]
        unaffected_fresh = (
            runtime.authority.shadow_fresh(unaffected_action.request, permits[other_actor])
            if mode == "dual_dag_advisory"
            else runtime.authority.permit(permits[other_actor].permit_id).lifecycle.value == "issued"
        )
        cross_actor_state_change_leak = not actor_current[other_actor] or not unaffected_fresh
    contradiction = None
    if cell.scenario_family == "S2":
        contradiction = {
            "positive_current": runtime.authority._roots[roots[cell.affected_actor].root_id].current,
            "negative_current": runtime.authority._roots[replacement.root_id].current,
            "positive_supersedes": list(roots[cell.affected_actor].supersedes),
            "negative_supersedes": list(replacement.supersedes),
            "non_defeated": next(
                value for dimension, value in runtime.authority.evaluate(
                    affected.request.candidate_id).assessments[0].validity
                if dimension.value == "non_defeated"),
        }
    supersession = None
    if superseded is not None:
        supersession = {
            "actor_id": cell.affected_actor,
            "old_root_id": superseded.root_id,
            "new_root_id": replacement.root_id,
            "old_polarity": superseded.proposition.polarity,
            "new_polarity": replacement.proposition.polarity,
            "same_tracked_proposition": superseded.proposition.key == replacement.proposition.key,
            "old_revision": superseded.source_stream_revision,
            "new_revision": replacement.source_stream_revision,
            "supersedes": list(replacement.supersedes),
            "old_root_current_after": runtime.authority._roots[superseded.root_id].current,
            "new_root_current": runtime.authority._roots[replacement.root_id].current,
            "visibility": list(replacement.visible_to),
        }
    mutation = {"hidden_truth_ingested": False, "declared_sec_pre": False,
                 "fixture_synthetic_semantic_mutation": cell.scenario_family in {"S1", "S2", "S3"},
                 "mutation_type": mutation_type,
                "authority_epoch_before": before_epoch, "authority_epoch_after": runtime.authority.epoch,
                "evidence_total_before": before_evidence, "evidence_total_after": runtime._evidence_total,
                "superseded_root_id": superseded.root_id if superseded else None,
                 "replacement_root_id": replacement.root_id if replacement else None,
                 "contradiction": contradiction,
                 "supersession": supersession,
                 "actor_current_EAdm": actor_current,
                 "cross_actor_dependency_leak": cross_actor_dependency_leak,
                 "cross_actor_state_change_leak": cross_actor_state_change_leak,
                 "evaluator_truth_before": evaluator_truth_before,
                 "evaluator_truth_after": evaluator_truth_after,
                 "evaluator_truth_before_digest": (
                     _content_digest(evaluator_truth_before)
                     if evaluator_truth_before is not None else None),
                 "evaluator_truth_after_digest": (
                     _content_digest(evaluator_truth_after)
                     if evaluator_truth_after is not None else None),
                 "evaluator_truth_changed": evaluator_truth_changed,
                 "evaluator_truth_authority_input": False,
                 "evaluator_truth_precondition_input": False,
                 "gateway_calls": gateway_calls}
    counters = {"planner_instantiated": False, "model_instantiated": False,
                "controller_instantiated": False, "planner_calls": 0, "model_calls": 0,
                "controller_redecisions": 0, "action_regenerations": 0}
    pairing_projection = {
        "scenario_family": cell.scenario_family,
        "inventory_id": cell.inventory_id,
        "affected_actor": cell.affected_actor,
        "matrix": cell.matrix,
        "r_p": actor_rp,
        "r_d": actor_rd,
        "mutation": {
            key: mutation[key] for key in (
                "mutation_type", "superseded_root_id", "replacement_root_id",
                "contradiction", "supersession", "actor_current_EAdm", "cross_actor_dependency_leak",
                "cross_actor_state_change_leak", "hidden_truth_ingested",
                "evaluator_truth_before", "evaluator_truth_after",
                "evaluator_truth_before_digest", "evaluator_truth_after_digest",
                "evaluator_truth_changed", "evaluator_truth_authority_input",
                "evaluator_truth_precondition_input",
            )
        },
    }
    pairing_digest = hashlib.sha256(canonical_bytes(pairing_projection)).hexdigest()
    return K6Trial(cell, runtime, prepared, tuple(prepared.values()), permits,
                    {"r_p": actor_rp, "r_d": actor_rd}, counters, mutation, rp,
                    r_d, calls, pairing_digest)


def validate_paired_construction(advisory: K6Trial, authority: K6Trial) -> None:
    """Require paired modes to differ only in the frozen enforcement condition."""
    left, right = advisory.cell, authority.cell
    if {left.condition, right.condition} != {"dual_dag_advisory", "dual_dag_authority"}:
        raise ValueError("K6 paired construction requires Advisory and Authority")
    if (left.scenario_family, left.inventory_id, left.affected_actor, left.matrix) != (
        right.scenario_family, right.inventory_id, right.affected_actor, right.matrix,
    ):
        raise ValueError("K6 paired construction cell identities differ")
    if any(advisory.r_p[field] != authority.r_p[field] for field in EXACT_FIELDS):
        raise ValueError("K6 paired construction exact requests differ")
    for field in (
        "current_EAdm", "reasons", "mutation_type", "mutation_dependency_ids",
        "intersecting_dependency_ids", "relevant_action_dependency_changed",
    ):
        if advisory.r_d[field] != authority.r_d[field]:
            raise ValueError(f"K6 paired construction semantic mutation differs: {field}")
    for actor in advisory.snapshots["r_p"]:
        if advisory.snapshots["r_p"][actor] != authority.snapshots["r_p"][actor]:
            raise ValueError(f"K6 paired construction admission differs: {actor}")
        if advisory.snapshots["r_d"][actor] != authority.snapshots["r_d"][actor]:
            raise ValueError(f"K6 paired construction mutation differs: {actor}")
    if advisory.pairing_digest != authority.pairing_digest:
        raise ValueError("K6 paired construction digest differs")
    for field in (
        "evaluator_truth_before", "evaluator_truth_after",
        "evaluator_truth_before_digest", "evaluator_truth_after_digest",
        "evaluator_truth_changed", "evaluator_truth_authority_input",
        "evaluator_truth_precondition_input",
    ):
        if advisory.mutation_state[field] != authority.mutation_state[field]:
            raise ValueError(f"K6 paired evaluator truth differs: {field}")
    if (advisory._runtime.classification_identity != authority._runtime.classification_identity
            or advisory._runtime.authority.policy != authority._runtime.authority.policy
            or advisory._runtime.authority.profile != authority._runtime.authority.profile):
        raise ValueError("K6 paired construction semantic bindings differ")
    if advisory.counters != authority.counters:
        raise ValueError("K6 paired construction reconsideration counters differ")
