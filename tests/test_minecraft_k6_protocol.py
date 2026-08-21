import copy
import json
from pathlib import Path

import pytest

from benchmarks.common.eac import ActionRef, ExactRequest
from benchmarks.minecraft.k6_protocol import (
    CONDITIONS,
    EXACT_FIELDS,
    K6ContractError,
    aggregate_k6_results,
    build_control_cells,
    build_k6_cells,
    build_primary_cells,
    detached_digest,
    expected_action_digest,
    load_k6_inventory,
    load_k6_protocol,
    trace_pairing_digest,
    validate_k6_trace,
)


def _write_with_digest(path, value):
    value = copy.deepcopy(value)
    value["detached_artifact_sha256"] = detached_digest(value)
    path.write_text(json.dumps(value), encoding="utf-8")


def _phase(identity, *, eadm=True, execution=False, permit_fresh=True):
    phase = {
        **identity,
        "current_EAdm": eadm,
        "authority_epoch_before_execution": 2,
        "exact_action_submitted": True,
        "permit_or_shadow_fresh": permit_fresh,
        "EnvPre_oracle": True,
        "SecPre_oracle": True,
        "execution_allowed": execution,
        "rejection_reason": None if execution else "stale",
        "native_callable_reached": execution,
    }
    return phase


def synthetic_trace(cell):
    protocol = load_k6_protocol()
    item = next(item for item in load_k6_inventory() if item.inventory_id == cell.inventory_id)
    identity = {
        "candidate_id": "candidate-1",
        "attempt_id": "attempt-1",
        "action": {"identity": item.action_identity, "version": item.action_version,
                   "digest": expected_action_digest(item)},
        "arguments": item.request(),
        "target": item.request(),
    }
    request = ExactRequest(
        identity["candidate_id"], identity["attempt_id"],
        ActionRef(identity["action"]["identity"], identity["action"]["version"],
                  identity["action"]["digest"]),
        tuple(identity["arguments"].items()), identity["target"],
    )
    import hashlib
    identity["exact_request_digest"] = "sha256:" + hashlib.sha256(request.identity_bytes()).hexdigest()
    relevant = cell.scenario_family in {"S1", "S2", "S3"}
    rp = {**identity, "EAdm": True, "authority_epoch": 1,
          "witness_root_ids": ["root-1"], "dependency_ids": ["evidence:root-1"]}
    rd = {"current_EAdm": not relevant,
          "authority_epoch": 1 if cell.scenario_family == "C2" else 2,
          "reasons": ["non_defeated.conflict"] if cell.scenario_family == "S2" else [],
          "mutation_type": "synthetic", "mutation_dependency_ids": ["evidence:root-1"],
          "intersecting_dependency_ids": ["evidence:root-1"] if relevant else [],
          "relevant_action_dependency_changed": relevant,
          "permit_or_shadow_fresh": not relevant}
    execution = cell.scenario_family in {"C1", "C2"} or cell.condition == "dual_dag_advisory"
    re = _phase(identity, eadm=not relevant, execution=execution, permit_fresh=not relevant)
    if cell.scenario_family == "C2":
        re["authority_epoch_before_execution"] = 1
    unaffected_rp = copy.deepcopy(rp)
    unaffected_rp["candidate_id"] = "candidate-2"
    unaffected_rp["attempt_id"] = "attempt-2"
    unaffected_request = ExactRequest(
        unaffected_rp["candidate_id"], unaffected_rp["attempt_id"],
        ActionRef(unaffected_rp["action"]["identity"], unaffected_rp["action"]["version"],
                  unaffected_rp["action"]["digest"]),
        tuple(unaffected_rp["arguments"].items()), unaffected_rp["target"],
    )
    unaffected_rp["exact_request_digest"] = (
        "sha256:" + hashlib.sha256(unaffected_request.identity_bytes()).hexdigest())
    unaffected_re = _phase(
        {name: copy.deepcopy(unaffected_rp[name]) for name in EXACT_FIELDS},
        eadm=True, execution=True, permit_fresh=True,
    )
    unaffected_rd = {**rd, "current_EAdm": True, "reasons": [],
                     "intersecting_dependency_ids": [],
                     "relevant_action_dependency_changed": False,
                     "permit_or_shadow_fresh": True}
    def mechanisms(unaffected=False):
        dependency_changed = relevant and not unaffected
        epoch_changed = cell.scenario_family != "C2"
        execution_allowed = True if unaffected else execution
        result = {
            "M0": {"decision": "allow", "reason": "admission_epistemically_admissible",
                   "inputs_used": ["r_p.EAdm"], "relevant_action_dependency_changed": None},
            "M1": {"decision": "allow", "reason": "exact_request_unchanged",
                   "inputs_used": [f"r_p/r_e.{field}" for field in EXACT_FIELDS],
                   "relevant_action_dependency_changed": None},
            "M2": {"decision": "reject" if epoch_changed else "allow",
                   "reason": ("global_authority_revision_changed" if epoch_changed
                              else "global_authority_revision_unchanged"),
                   "inputs_used": ["r_p.authority_epoch", "r_e.authority_epoch_before_execution"],
                   "relevant_action_dependency_changed": None},
            "M3": {"decision": "reject" if dependency_changed else "allow",
                   "reason": ("relevant_action_dependency_changed" if dependency_changed
                              else "relevant_action_dependencies_unchanged"),
                   "inputs_used": ["r_d.relevant_action_dependency_changed"],
                   "relevant_action_dependency_changed": dependency_changed},
        }
        result["M4"] = ({
            "decision": "not_applicable", "reason": "existing_authority_not_run_in_advisory_mode",
            "inputs_used": [], "relevant_action_dependency_changed": None,
        } if cell.condition == "dual_dag_advisory" else {
            "decision": "allow" if execution_allowed else "reject",
            "reason": ("existing_authority_allowed" if execution_allowed else "existing_authority_stale"),
            "inputs_used": ["existing_authority_gateway_outcome"],
            "relevant_action_dependency_changed": None,
        })
        return result
    s3 = None if cell.scenario_family != "S3" else {
        "affected_actor": cell.affected_actor,
        "unaffected_actor": "Bob" if cell.affected_actor == "Alice" else "Alice",
        "unaffected_current_EAdm": True,
        "unaffected_r_p": unaffected_rp,
        "unaffected_r_d": unaffected_rd,
        "unaffected_r_e": unaffected_re,
        "unaffected_same_prepared_object": True,
        "unaffected_exact_action_preserved": True,
        "unaffected_mechanism_analysis": mechanisms(unaffected=True),
        "cross_actor_dependency_leak": False,
        "cross_actor_state_change_leak": False,
    }
    trace = {
        "schema_version": "minecraft-k6-cell-trace/1",
        "protocol_digest": protocol["validated_protocol_digest"],
        "inventory_digest": protocol["validated_inventory_digest"],
        "pairing_digest": "c" * 64,
        "cell": {name: getattr(cell, name) for name in (
            "cell_id", "scenario_family", "inventory_id", "condition", "affected_actor", "matrix")},
        "semantic_bindings": protocol["semantic_bindings"],
        "r_p": rp,
        "r_d": rd,
        "r_e": re,
        "actor_scope": {"actor_id": cell.affected_actor, "visible_to": [cell.affected_actor],
                        "private_actor_scope": True},
        "mutation": {
            "hidden_truth_ingested": False,
            "mutation_type": {
                "S1": "opposite_polarity_explicit_supersession",
                "S2": "independent_opposite_trusted_tool_result",
                "S3": "affected_actor_explicit_supersession",
                "C1": "unrelated_weather_visible_update",
                "C2": "evaluator_only_hidden_truth_mutation",
            }[cell.scenario_family],
            "superseded_root_id": "root-1" if cell.scenario_family in {"S1", "S3"} else None,
            "replacement_root_id": None if cell.scenario_family == "C2" else "root-2",
            "contradiction": ({
                "positive_current": True, "negative_current": True,
                "positive_supersedes": [], "negative_supersedes": [], "non_defeated": False,
            } if cell.scenario_family == "S2" else None),
            "supersession": ({
                "actor_id": cell.affected_actor,
                "old_root_id": "root-1",
                "new_root_id": "root-2",
                "old_polarity": True,
                "new_polarity": False,
                "same_tracked_proposition": True,
                "old_revision": 1,
                "new_revision": 2,
                "supersedes": ["root-1"],
                "old_root_current_after": False,
                "new_root_current": True,
                "visibility": [cell.affected_actor],
            } if cell.scenario_family in {"S1", "S3"} else None),
            "evidence_total_before": 1,
            "evidence_total_after": 1 if cell.scenario_family == "C2" else 2,
            "actor_current_EAdm": ({
                cell.affected_actor: False,
                ("Bob" if cell.affected_actor == "Alice" else "Alice"): True,
            } if cell.scenario_family == "S3" else {cell.affected_actor: not relevant}),
            "cross_actor_dependency_leak": False,
            "cross_actor_state_change_leak": False,
        },
        "exact_action": {"same_prepared_object": True, "exact_action_preserved": True},
        "no_reconsideration": {
            "planner_instantiated": False, "model_instantiated": False,
            "controller_instantiated": False, "planner_calls": 0, "model_calls": 0,
            "controller_redecisions": 0, "action_regenerations": 0,
        },
        "s3": s3,
        "mechanism_analysis": mechanisms(),
    }
    trace["pairing_digest"] = trace_pairing_digest(trace)
    return trace


def test_frozen_inventory_matches_all_and_only_epre_action_strata():
    items = load_k6_inventory()
    assert [(item.inventory_id, item.action_identity, item.proposition_predicate) for item in items] == [
        ("I1", "MineBlock", "target_block_present"),
        ("I2", "placeBlock", "placement_target_observed"),
        ("I3", "navigateTo", "destination_observed"),
        ("I4", "attackTarget", "entity_target_observed"),
        ("I5", "handoverBlock", "recipient_observed"),
    ]
    assert items[-1].request()["target_player_name"] == "Charlie"
    assert all(item.declared_env_pre and not item.declared_sec_pre for item in items)


def test_inventory_rejects_semantic_tamper_even_with_recomputed_digest(tmp_path):
    source = json.loads(Path("benchmarks/minecraft/k6_inventory_v1.json").read_text())
    source["items"][0]["proposition"]["predicate"] = "forged"
    path = tmp_path / "inventory.json"
    _write_with_digest(path, source)
    with pytest.raises(K6ContractError, match="order or identity"):
        load_k6_inventory(path)


def test_inventory_rejects_missing_digest_and_reordered_items(tmp_path):
    source = json.loads(Path("benchmarks/minecraft/k6_inventory_v1.json").read_text())
    source["detached_artifact_sha256"] = ""
    path = tmp_path / "missing.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(K6ContractError, match="digest"):
        load_k6_inventory(path)
    source = json.loads(Path("benchmarks/minecraft/k6_inventory_v1.json").read_text())
    source["items"][0], source["items"][1] = source["items"][1], source["items"][0]
    path = tmp_path / "reordered.json"
    _write_with_digest(path, source)
    with pytest.raises(K6ContractError, match="order or identity"):
        load_k6_inventory(path)


def test_protocol_and_cell_census_are_frozen_and_non_iid():
    protocol = load_k6_protocol()
    primary = build_primary_cells()
    controls = build_control_cells()
    assert protocol["study_design"] == {
        "iid_samples": False, "inventory_census": True,
        "primary_cell_count": 40, "control_cell_count": 20,
        "scientific_results_collected_by_this_artifact": False,
    }
    assert len(primary) == 40 and len(controls) == 20 and len(build_k6_cells()) == 60
    assert len({cell.cell_id for cell in primary + controls}) == 60
    assert {cell.condition for cell in primary} == set(CONDITIONS)
    assert sum(cell.scenario_family == "S3" for cell in primary) == 20
    assert {cell.affected_actor for cell in primary if cell.scenario_family == "S3"} == {"Alice", "Bob"}


def test_trace_validator_rejects_exact_action_and_reconsideration_drift():
    cell = build_primary_cells()[0]
    trace = synthetic_trace(cell)
    assert validate_k6_trace(trace, cell=cell) == trace
    changed = copy.deepcopy(trace)
    changed["r_e"]["attempt_id"] = "reconstructed"
    with pytest.raises(K6ContractError, match="identity changed"):
        validate_k6_trace(changed, cell=cell)
    changed = copy.deepcopy(trace)
    changed["no_reconsideration"]["model_calls"] = 1
    with pytest.raises(K6ContractError, match="no-reconsideration"):
        validate_k6_trace(changed, cell=cell)
    changed = copy.deepcopy(trace)
    changed["mutation"]["mutation_type"] = "unrelated_weather_visible_update"
    changed["pairing_digest"] = trace_pairing_digest(changed)
    with pytest.raises(K6ContractError, match="S1 supersession"):
        validate_k6_trace(changed, cell=cell)


def test_exact_fraction_aggregator_is_incomplete_and_has_no_inference_fields():
    primary = build_primary_cells()
    controls = build_control_cells()
    traces = [
        synthetic_trace(next(cell for cell in primary if cell.scenario_family == "S1")),
        synthetic_trace(next(cell for cell in primary if cell.scenario_family == "S3")),
        synthetic_trace(next(cell for cell in controls if cell.scenario_family == "C1")),
    ]
    result = aggregate_k6_results(traces)
    assert result["complete"] is False and result["verdict"] is None
    assert result["iid_samples"] is False and result["inventory_census"] is True
    assert result["confidence_intervals_added"] is False and result["p_values_added"] is False
    assert result["overall"]["relevant_revision_detection"] == {"numerator": 2, "denominator": 2}
    assert result["overall"]["unrelated_retention"] == {"numerator": 1, "denominator": 1}
    assert set(result) >= {"by_family", "by_inventory", "by_condition"}
    assert result["mechanism_isolation"]["overall"]["M3"]["estimands"][
        "relevant_revision_detection"] == {
        "numerator": 2, "denominator": 2,
    }
    assert result["mechanism_isolation"]["overall"]["M3"]["estimands"][
        "unrelated_retention"] == {"numerator": 1, "denominator": 1}


def test_aggregator_rejects_duplicate_cells():
    trace = synthetic_trace(build_primary_cells()[0])
    with pytest.raises(K6ContractError, match="duplicate"):
        aggregate_k6_results([trace, copy.deepcopy(trace)])
