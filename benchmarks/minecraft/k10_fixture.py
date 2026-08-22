"""Non-executing construction fixtures for the frozen Minecraft K10 census."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any

from benchmarks.common.eac import Proposition, PropositionKey
from benchmarks.common.eac.authority import _proposition_slot
from benchmarks.common.eac.canonical import canonical_bytes
from benchmarks.common.eac.model import PermitLifecycle
from benchmarks.minecraft.eac_runtime import MinecraftEACError, MinecraftEACRuntime
from benchmarks.minecraft.k6_fixture import (
    _intersection, _mechanism_artifacts, _snapshot, _content_digest,
)
from benchmarks.minecraft.k10_protocol import (
    ACTORS, CONDITIONS, EXACT_FIELDS, SELECTION_MANIFEST_DIGEST, K10CellSpec, build_k10_cells,
    load_k10_inventory, load_k10_protocol, validate_k10_trace,
)

_REAL_SUBMISSIONS = 0


def real_submission_count() -> int:
    return _REAL_SUBMISSIONS


@dataclass
class DetachedEvaluatorTruth:
    """Evaluator-only state that is never passed to authority or precondition callbacks."""
    hidden_target_available: bool

    def snapshot(self) -> dict[str, bool]:
        return {"hidden_target_available": self.hidden_target_available}

    def set_hidden_target_available(self, value: bool) -> None:
        if type(value) is not bool:
            raise TypeError("detached evaluator truth must be boolean")
        self.hidden_target_available = value


@dataclass
class K10Trial:
    cell: K10CellSpec
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
    selected_request_digest: str
    previously_unsubmitted: dict[str, Any]
    semantic_bindings: dict[str, Any]
    initial_evidence_roots: dict[str, str]
    detached_oracle_state: dict[str, Any]
    detached_evaluator_truth: DetachedEvaluatorTruth | None
    _submitted: bool = False

    @property
    def native_calls(self):
        return {actor: tuple(calls) for actor, calls in self._native_calls.items()}

    @property
    def same_prepared_object(self) -> bool:
        return all(self._prepared_actions[a] is self._prepared_objects[i]
                   for i, a in enumerate(self._prepared_actions))

    def _fresh(self, actor: str) -> bool:
        action = self._prepared_actions[actor]
        permit = self._admissions[actor]
        if self._runtime.mode == "dual_dag_advisory":
            return self._runtime.authority.shadow_fresh(action.request, permit)
        return self._runtime.authority.permit(permit.permit_id).lifecycle is PermitLifecycle.ISSUED

    def _validate_pre_submission(self) -> None:
        """Reject every protocol, identity, and retained-state drift before effects."""
        try:
            protocol = load_k10_protocol()
            frozen = {c.cell_id: c for c in build_k10_cells()}
            if frozen.get(self.cell.cell_id) != self.cell or self._runtime.mode != self.cell.condition:
                raise RuntimeError("K10 trial identity changed before submission")
            if self.semantic_bindings != protocol["semantic_bindings"]:
                raise RuntimeError("K10 semantic bindings changed before submission")
            if self.selected_request_digest != next(i.canonical_request_digest for i in load_k10_inventory()
                                                    if i.inventory_id == self.cell.inventory_id):
                raise RuntimeError("K10 selected request binding changed before submission")
            if not self.same_prepared_object or self.r_p != self.snapshots["r_p"][self.cell.affected_actor]:
                raise RuntimeError("K10 prepared action was reconstructed or substituted")
            if self.r_d != self.snapshots["r_d"][self.cell.affected_actor]:
                raise RuntimeError("K10 affected phase snapshots changed before submission")
            if any(self.counters[name] != 0 for name in ("planner_calls", "model_calls",
                       "controller_redecisions", "action_regenerations")):
                raise RuntimeError("K10 no-reconsideration invariant changed before submission")
            if any(self.counters[name] is not False for name in ("planner_instantiated",
                       "model_instantiated", "controller_instantiated")):
                raise RuntimeError("K10 no-reconsideration invariant changed before submission")
            if self.r_p["EAdm"] is not True or self.mutation_state["hidden_truth_ingested"] is not False:
                raise RuntimeError("K10 pre-submission contract changed")
            for actor, action in self._prepared_actions.items():
                if _snapshot(action) != {k: self.snapshots["r_p"][actor][k] for k in EXACT_FIELDS}:
                    raise RuntimeError("K10 exact request changed before submission")
                if self._runtime.authority.evaluate(action.request.candidate_id).admissible is not self.snapshots["r_d"][actor]["current_EAdm"]:
                    raise RuntimeError("K10 semantic state changed before submission")
                if self._fresh(actor) is not self.snapshots["r_d"][actor]["permit_or_shadow_fresh"]:
                    raise RuntimeError("K10 permit freshness changed before submission")
        except (KeyError, StopIteration, TypeError) as exc:
            raise RuntimeError("K10 pre-submission validation failed closed") from exc

    def submit(self) -> dict[str, Any]:
        global _REAL_SUBMISSIONS
        if self._submitted:
            raise RuntimeError("K10 trial submission is one-shot")
        self._validate_pre_submission()
        _REAL_SUBMISSIONS += 1
        self._submitted = True
        protocol = load_k10_protocol()
        action = self._prepared_actions[self.cell.affected_actor]
        before = self._runtime.authority.epoch
        permit_fresh = self._fresh(self.cell.affected_actor)
        allowed, rejection = False, None
        try:
            self._runtime.execute_prepared(action)
            allowed = True
        except MinecraftEACError as exc:
            rejection = str(exc)
        re = {**_snapshot(action), "current_EAdm": self.r_d["current_EAdm"],
              "authority_epoch_before_execution": before, "exact_action_submitted": True,
              "permit_or_shadow_fresh": permit_fresh,
              "EnvPre_oracle": True, "SecPre_oracle": True, "execution_allowed": allowed,
              "rejection_reason": rejection,
              "native_callable_reached": bool(self._native_calls[self.cell.affected_actor])}
        s3 = None
        if self.cell.scenario_family == "S3":
            affected = self.cell.affected_actor
            other = "Bob" if affected == "Alice" else "Alice"
            other_action = self._prepared_actions[other]
            other_rp = self.snapshots["r_p"][other]
            other_rd = self.snapshots["r_d"][other]
            other_before = len(self._native_calls[other])
            other_epoch = self._runtime.authority.epoch
            other_fresh = self._fresh(other)
            other_allowed, other_rejection = False, None
            try:
                self._runtime.execute_prepared(other_action)
                other_allowed = True
            except MinecraftEACError as exc:
                other_rejection = str(exc)
            other_re = {
                **_snapshot(other_action), "current_EAdm": other_rd["current_EAdm"],
                "authority_epoch_before_execution": other_epoch,
                "exact_action_submitted": True, "permit_or_shadow_fresh": other_fresh,
                "EnvPre_oracle": True, "SecPre_oracle": True,
                "execution_allowed": other_allowed, "rejection_reason": other_rejection,
                "native_callable_reached": len(self._native_calls[other]) > other_before,
            }
            s3 = {
                "affected_actor": affected, "unaffected_actor": other,
                "unaffected_current_EAdm": other_rd["current_EAdm"],
                "unaffected_r_p": other_rp, "unaffected_r_d": other_rd,
                "unaffected_r_e": other_re,
                "unaffected_same_prepared_object": self._prepared_actions[other] is other_action,
                "unaffected_exact_action_preserved": all(
                    other_rp[field] == other_re[field] for field in EXACT_FIELDS),
                "unaffected_mechanism_analysis": _mechanism_artifacts(
                    other_rp, other_rd, other_re, self.cell.condition),
                "cross_actor_dependency_leak": self.mutation_state["cross_actor_dependency_leak"],
                "cross_actor_state_change_leak": self.mutation_state["cross_actor_state_change_leak"],
            }
        trace = {"schema_version": "minecraft-k10-cell-trace/1",
                  "protocol_digest": protocol["validated_protocol_digest"],
                  "candidate_pool_digest": protocol["validated_candidate_pool_digest"],
                  "inventory_digest": protocol["validated_inventory_digest"],
                  "result_schema_digest": protocol["validated_result_schema_digest"],
                  "selection_manifest_digest": SELECTION_MANIFEST_DIGEST,
                 "pairing_digest": self.pairing_digest,
                 "cell": {k: getattr(self.cell, k) for k in ("cell_id", "scenario_family", "inventory_id", "condition", "affected_actor", "matrix")},
                 "semantic_bindings": self.semantic_bindings, "selected_request_digest": self.selected_request_digest,
                 "previously_unsubmitted": self.previously_unsubmitted, "r_p": self.r_p,
                 "r_d": self.r_d, "r_e": re,
                 "actor_scope": {"actor_id": self.cell.affected_actor, "visible_to": [self.cell.affected_actor], "private_actor_scope": True},
                 "mutation": self.mutation_state,
                 "exact_action": {"same_prepared_object": True, "exact_action_preserved": all(self.r_p[k] == re[k] for k in EXACT_FIELDS)},
                  "no_reconsideration": self.counters, "s3": s3,
                 "mechanism_analysis": _mechanism_artifacts(self.r_p, self.r_d, re, self.cell.condition)}
        return validate_k10_trace(trace, cell=self.cell)


def construct_k10_trial(cell: K10CellSpec) -> K10Trial:
    protocol = load_k10_protocol()
    frozen = {c.cell_id: c for c in build_k10_cells()}
    if frozen.get(cell.cell_id) != cell:
        raise ValueError("K10 cell is outside the frozen 120-cell matrix")
    item = next((i for i in load_k10_inventory() if i.inventory_id == cell.inventory_id), None)
    if item is None:
        raise ValueError("unknown K10 inventory item")
    calls = {actor: [] for actor in (ACTORS if cell.scenario_family == "S3" else (cell.affected_actor,))}
    gateway_calls = {"env": 0, "sec": 0}
    def env(_request):
        gateway_calls["env"] += 1
        return True
    def sec(_request):
        gateway_calls["sec"] += 1
        return True
    run_id = f"k10-{cell.scenario_family.lower()}-{cell.inventory_id.lower()}-{cell.affected_actor.lower()}"
    runtime = MinecraftEACRuntime(mode=cell.condition, run_id=run_id,
                                  env_prechecks={item.action_identity: env}, sec_prechecks={item.action_identity: sec})
    actors = tuple(calls)
    roots = {a: runtime.ingest_target_observation(a, item.action_identity, item.request()) for a in actors}
    def native(**kwargs):
        calls.setdefault(kwargs.get("player_name", "unknown"), []).append(dict(kwargs)); return {"status": True}
    prepared = {}
    for actor in actors:
        kwargs = {**item.request(), "player_name": actor, "emotion": [], "murmur": ""}
        prepared[actor] = runtime.prepare_tool(item.action_identity, native, (), kwargs)
    permits = {a: (runtime.authority.shadow_permit(prepared[a].request.candidate_id)
                   if cell.condition == "dual_dag_advisory" else prepared[a].permit) for a in actors}
    rp_all, witness, deps = {}, {}, {}
    for actor, action in prepared.items():
        decision = runtime.authority.evaluate(action.request.candidate_id)
        witness[actor] = {r.root_id for w in decision.witnesses for r in w.roots}
        deps[actor] = {e.dependency_id for e in permits[actor].manifest.expectations}
        rp_all[actor] = {**_snapshot(action), "EAdm": decision.admissible, "authority_epoch": runtime.authority.epoch,
                         "witness_root_ids": sorted(witness[actor]), "dependency_ids": sorted(deps[actor])}
    before_epoch, before_evidence = runtime.authority.epoch, runtime._evidence_total
    replacement = superseded = None
    actor_current = {a: True for a in actors}
    if cell.scenario_family in {"S1", "S3"}:
        replacement = runtime.ingest_actor_record(actor_id=cell.affected_actor,
            proposition=replace(roots[cell.affected_actor].proposition, polarity=False), record_type="direct_observation",
            source="minecraft-visible-observation", revision=2 if cell.scenario_family == "S1" else 3,
            supersedes=(roots[cell.affected_actor].root_id,))
        superseded = roots[cell.affected_actor]
        if cell.scenario_family == "S3": actor_current[cell.affected_actor] = False
    elif cell.scenario_family == "S2":
        replacement = runtime.ingest_actor_record(actor_id=cell.affected_actor,
            proposition=replace(roots[cell.affected_actor].proposition, polarity=False), record_type="trusted_tool_result",
            source="minecraft-observation-adapter", revision=2)
    elif cell.scenario_family == "C1":
        replacement = runtime.ingest_actor_record(actor_id=cell.affected_actor,
            proposition=Proposition(PropositionKey("minecraft", "weather_visible", ("rain",), "current")),
            record_type="direct_observation", source="minecraft-visible-weather", revision=2)
    mutation_ids = set()
    if replacement:
        mutation_ids.update({"evidence:" + replacement.root_id, _proposition_slot(replacement.proposition, cell.affected_actor)})
        if superseded: mutation_ids.add("evidence:" + superseded.root_id)
    rd_all = {}
    for actor, action in prepared.items():
        decision = runtime.authority.evaluate(action.request.candidate_id)
        actor_current[actor] = decision.admissible
        inter = _intersection(deps[actor], mutation_ids)
        rd_all[actor] = {"current_EAdm": decision.admissible, "authority_epoch": runtime.authority.epoch,
                         "reasons": list(decision.reasons), "mutation_type": {
                             "S1":"opposite_polarity_explicit_supersession", "S2":"independent_opposite_trusted_tool_result",
                             "S3":"affected_actor_explicit_supersession", "C1":"unrelated_weather_visible_update",
                             "C2":"evaluator_only_hidden_truth_mutation"}[cell.scenario_family],
                         "mutation_dependency_ids": sorted(mutation_ids), "intersecting_dependency_ids": list(inter),
                         "relevant_action_dependency_changed": bool(inter), "permit_or_shadow_fresh": (
                             runtime.authority.shadow_fresh(action.request, permits[actor]) if cell.condition == "dual_dag_advisory"
                             else runtime.authority.permit(permits[actor].permit_id).lifecycle is PermitLifecycle.ISSUED)}
    detached_evaluator = None
    truth_before = truth_after = None
    if cell.scenario_family == "C2":
        detached_evaluator = DetachedEvaluatorTruth(True)
        truth_before = detached_evaluator.snapshot()
        detached_evaluator.set_hidden_target_available(False)
        truth_after = detached_evaluator.snapshot()
    other_actor = next((actor for actor in actors if actor != cell.affected_actor), None)
    cross_dependency_leak = False
    cross_state_change_leak = False
    if other_actor is not None:
        cross_dependency_leak = (
            roots[other_actor].root_id in witness[cell.affected_actor]
            or roots[cell.affected_actor].root_id in witness[other_actor]
            or bool(rd_all[other_actor]["intersecting_dependency_ids"])
        )
        cross_state_change_leak = (
            rd_all[other_actor]["current_EAdm"] is not True
            or rd_all[other_actor]["permit_or_shadow_fresh"] is not True
        )
    mutation = {"hidden_truth_ingested": False, "declared_sec_pre": False,
                "fixture_synthetic_semantic_mutation": cell.scenario_family in {"S1","S2","S3"},
                "mutation_type": rd_all[cell.affected_actor]["mutation_type"], "authority_epoch_before": before_epoch,
                "authority_epoch_after": runtime.authority.epoch, "evidence_total_before": before_evidence,
                "evidence_total_after": runtime._evidence_total, "superseded_root_id": superseded.root_id if superseded else None,
                "replacement_root_id": replacement.root_id if replacement else None, "contradiction": (
                    {"positive_current":True,"negative_current":True,"positive_supersedes":[],"negative_supersedes":[],"non_defeated":False}
                    if cell.scenario_family == "S2" else None), "supersession": None, "actor_current_EAdm": actor_current,
                 "cross_actor_dependency_leak": cross_dependency_leak,
                 "cross_actor_state_change_leak": cross_state_change_leak,
                "evaluator_truth_before": truth_before, "evaluator_truth_after": truth_after,
                "evaluator_truth_before_digest": _content_digest(truth_before) if truth_before else None,
                "evaluator_truth_after_digest": _content_digest(truth_after) if truth_after else None,
                "evaluator_truth_changed": truth_before != truth_after if cell.scenario_family == "C2" else False,
                "evaluator_truth_authority_input": False, "evaluator_truth_precondition_input": False,
                 "gateway_calls": gateway_calls}
    if superseded:
        mutation["supersession"] = {"actor_id": cell.affected_actor, "old_root_id": superseded.root_id,
            "new_root_id": replacement.root_id, "old_polarity": True, "new_polarity": False,
            "same_tracked_proposition": True, "old_revision": superseded.source_stream_revision,
            "new_revision": replacement.source_stream_revision, "supersedes": [superseded.root_id],
            "old_root_current_after": False, "new_root_current": True, "visibility": [cell.affected_actor]}
    counters = {"planner_instantiated":False,"model_instantiated":False,"controller_instantiated":False,
                "planner_calls":0,"model_calls":0,"controller_redecisions":0,"action_regenerations":0}
    projection = {
        "scenario_family": cell.scenario_family, "inventory_id": cell.inventory_id,
        "affected_actor": cell.affected_actor, "matrix": cell.matrix,
        "r_p": rp_all, "r_d": rd_all,
        "mutation": {key: mutation[key] for key in (
            "mutation_type", "superseded_root_id", "replacement_root_id",
            "contradiction", "supersession", "actor_current_EAdm",
            "cross_actor_dependency_leak", "cross_actor_state_change_leak",
            "hidden_truth_ingested", "evaluator_truth_before", "evaluator_truth_after",
            "evaluator_truth_before_digest", "evaluator_truth_after_digest",
            "evaluator_truth_changed", "evaluator_truth_authority_input",
            "evaluator_truth_precondition_input",
        )},
    }
    pairing = hashlib.sha256(canonical_bytes(projection)).hexdigest()
    attestation = {"attested":True,"definition":"previously effect-boundary-unsubmitted",
                   "selected_request_digest":item.canonical_request_digest,
                   "historical_audit_digest":protocol["validated_historical_audit_digest"]}
    return K10Trial(cell, runtime, prepared, tuple(prepared.values()), permits, {"r_p":rp_all,"r_d":rd_all}, counters,
                    mutation, rp_all[cell.affected_actor], rd_all[cell.affected_actor], calls, pairing,
                    item.canonical_request_digest, attestation, protocol["semantic_bindings"],
                     {a:r.root_id for a,r in roots.items()},
                     {"EnvPre_oracle": True, "SecPre_oracle": True}, detached_evaluator)


def validate_paired_construction(advisory: K10Trial, authority: K10Trial) -> None:
    if {advisory.cell.condition, authority.cell.condition} != set(CONDITIONS):
        raise ValueError("K10 paired construction requires Advisory and Authority")
    if (advisory.cell.scenario_family, advisory.cell.inventory_id, advisory.cell.affected_actor, advisory.cell.matrix) != (
            authority.cell.scenario_family, authority.cell.inventory_id, authority.cell.affected_actor, authority.cell.matrix):
        raise ValueError("K10 paired construction cell identities differ")
    for name in ("selected_request_digest", "semantic_bindings", "pairing_digest", "previously_unsubmitted",
                 "detached_oracle_state", "detached_evaluator_truth", "snapshots", "counters", "mutation_state"):
        if getattr(advisory, name) != getattr(authority, name):
            raise ValueError(f"K10 paired construction differs: {name}")
    if advisory.r_p != authority.r_p or advisory.r_d != authority.r_d:
        raise ValueError("K10 paired construction phase projection differs")
    if advisory.initial_evidence_roots != authority.initial_evidence_roots:
        raise ValueError("K10 semantic roots differ")
    for actor in advisory._prepared_actions:
        if _snapshot(advisory._prepared_actions[actor]) != _snapshot(authority._prepared_actions[actor]):
            raise ValueError("K10 candidate/attempt structure differs")
