from __future__ import annotations

from collections import Counter
from dataclasses import replace
import io
from pathlib import Path
import subprocess
import tarfile

import pytest

from benchmarks.eac511.equivalence import (compare_paired_pre_gate, compare_pre_gate,
                                           pre_gate_snapshot_digest)
from benchmarks.eac511.artifacts import load_json_object
from benchmarks.eac511.events import normalize_event, validate_event_stream
from benchmarks.eac511.fixtures import tier1_fixtures, validate_tier1_records
from benchmarks.eac511.fixtures import _authority
from benchmarks.eac511.identity import (FROZEN_510, detached_digest, semantic_digest, verify_detached,
                                        verify_frozen_runtime_inputs)
from benchmarks.eac511.matrix import expand_matrix, matrix_cell_digest, paired_cell_equal
from benchmarks.eac511.metrics import calculate_metrics
from benchmarks.eac511.model import Condition, PerturbationFamily, SEEDS, Tier
from benchmarks.eac511.oracle import sanitize_publication
from benchmarks.eac511.perturbations import apply_operator, perturbation_plan
from benchmarks.eac511.protocol import (
    EVENT_PAYLOAD_REQUIRED, EVENT_SCHEMA_PATH, EVENT_TYPES, HYPOTHESES,
    SCENARIO_SCHEMA_PATH,
    event_schema_document,
    load_committed_protocol,
    load_committed_scenarios,
    protocol_document,
    scenario_definitions,
    scenario_schema_document,
    scenario_set_document,
    validate_scenario,
)
from benchmarks.common.eac import EvidenceRoot, Proposition, ProvenanceRecord
from env.runtime_execution import RuntimeExecution
from env.runtime_paths import RuntimePaths
from benchmarks.eac511.statistics import (
    PREREGISTERED_SEED,
    benjamini_hochberg,
    compare_conditions,
    exact_mcnemar,
    paired_bootstrap_ci,
    wilson_interval,
)


def test_frozen_design_artifacts_are_canonical_and_self_consistent() -> None:
    protocol = load_committed_protocol()
    scenarios = load_committed_scenarios()
    assert protocol == protocol_document()
    assert tuple(item.document for item in scenarios) == scenario_definitions()
    assert scenario_set_document()["scenarios"] == list(scenario_definitions())
    verify_detached(protocol)
    verify_detached(scenario_set_document())
    committed_scenario_schema = load_json_object(SCENARIO_SCHEMA_PATH)
    committed_event_schema = load_json_object(EVENT_SCHEMA_PATH)
    assert committed_scenario_schema == scenario_schema_document()
    assert committed_event_schema == event_schema_document()
    verify_detached(committed_scenario_schema)
    verify_detached(committed_event_schema)
    assert protocol["experiment_flags"]["final_execution_authorized"] is False
    assert protocol["experiment_flags"]["judged_execution"] is False
    assert protocol["experiment_flags"]["production"] is False
    assert protocol["frozen_inputs"] == FROZEN_510.as_dict()
    assert verify_frozen_runtime_inputs()["runtime_manifest_digest"] == FROZEN_510.runtime_manifest_digest
    markdown = (Path(__file__).resolve().parents[1] /
                "docs/experiments/eac511/eac_benchmark_protocol_v1.md").read_text(encoding="utf-8")
    markdown_hypotheses = tuple(line for line in markdown.splitlines()
                                if len(line) > 3 and line[0] == "H" and line[1].isdigit()
                                and ": " in line)
    assert markdown_hypotheses == tuple(f"{key}: {text}" for key, text in HYPOTHESES)


def test_scenario_catalog_and_primary_matrix_are_exact() -> None:
    scenarios = load_committed_scenarios()
    assert len(scenarios) == 17
    counts = Counter((item.family, item.tier) for item in scenarios)
    for family in tuple(PerturbationFamily)[:7]:
        assert counts[(family, Tier.TASK)] == 2
    for family in tuple(PerturbationFamily)[7:]:
        assert counts[(family, Tier.INTEGRITY)] == 1

    cells = expand_matrix(scenarios)
    assert len(cells) == 210
    assert {cell.seed for cell in cells} == set(SEEDS)
    assert Counter(cell.condition for cell in cells) == {
        Condition.BASELINE: 70,
        Condition.ADVISORY: 70,
        Condition.AUTHORITY: 70,
    }
    paired = cells[:3]
    assert all(paired_cell_equal(paired[0], item) for item in paired[1:])


def test_scenario_digest_detects_mutation() -> None:
    document = scenario_definitions()[0]
    changed = dict(document)
    changed["title"] = "post-freeze mutation"
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_scenario(changed)
    assert detached_digest(document, "canonical_scenario_sha256") == document["canonical_scenario_sha256"]


def test_p4_changes_only_evaluator_state_and_publication_is_recursive() -> None:
    actor = {"belief": "present", "nested": {"visible": True}}
    evaluator = {"world": "present"}
    actor_after, evaluator_after = apply_operator(PerturbationFamily.P4, actor, evaluator, seed=11)
    assert actor_after == actor
    assert evaluator_after == {"world": "present", "p4_marker": 11}

    public = sanitize_publication({
        "visible": 1,
        "nested": {"ground_truth": "secret", "ok": 2},
        "items": [{"oracle_state": {"secret": True}}, {"ok": 3}],
    })
    assert public == {"visible": 1, "nested": {"ok": 2}, "items": [{}, {"ok": 3}]}
    scenario_public = sanitize_publication(scenario_definitions()[6])
    assert "independent_adequacy_oracle" not in scenario_public
    assert "truth_status" not in scenario_public


def test_perturbation_plan_is_complete_and_deterministic() -> None:
    first = perturbation_plan(23)
    assert first == perturbation_plan(23)
    assert tuple(item.family for item in first) == tuple(PerturbationFamily)
    p4 = first[3]
    assert p4.visibility.value == "NONE"
    assert p4.phase.value == "EVALUATOR_ONLY_ASYNC"
    assert first[7].phase.value == "AFTER_PERMIT_BEFORE_EFFECT"


def _pre_gate_snapshot(scenario=None) -> dict[str, object]:
    scenario = scenario or load_committed_scenarios()[0]
    return {
        "required_evidence": ["root"],
        "epre": dict(scenario.document["affected_epre"]),
        "classification": "dual-class",
        "policy": FROZEN_510.as_dict()["support_policy"],
        "witness": ["root"],
        "eadm": "ADMISSIBLE",
        "candidate": "candidate-v1",
        "task": scenario.document["task_fixture_id"],
        "source_profile": {"identity": "minecraft-eac-primary", "version": 1,
                           "digest": FROZEN_510.source_profile_digest},
        "request": {"candidate_id": "candidate-v1", "attempt_id": "attempt-v1",
                    "action": {"identity": "build", "version": 1, "digest": "b" * 64},
                    "arguments": {"count": 1}, "target": [1, 2, 3]},
        "dependency_manifest": {
            "fingerprint": "c" * 64,
            "actor_scope": {"actor_id": "Alice", "visibility_revision": 1,
                            "scope": ["village"]},
            "expectations": [{"dependency_id": "policy:eac-primary-support:1",
                              "revision": 1, "kind": "policy"}],
            "conflict_watches": ["conflict:minecraft:target_block_present"],
            "policy_binding": {"identity": "eac-primary-support", "version": 1,
                               "digest": FROZEN_510.support_policy_digest},
            "profile_binding": {"identity": "minecraft-eac-primary", "version": 1,
                                "digest": FROZEN_510.source_profile_digest},
        },
        "seed": 11,
        "scenario_digest": scenario.digest,
        "initial_state_digest": "2" * 64,
        "materialized_fixture_digest": "3" * 64,
        "runtime_identity": {
            "execution_revision": FROZEN_510.execution_revision,
            "manifest_digest": FROZEN_510.runtime_manifest_digest,
            "premanifest_identity": FROZEN_510.premanifest_identity,
        },
        "history_prefix_digest": "4" * 64,
        "opportunity_id": "opportunity-v1",
        "opportunity_role": "primary",
    }


def _snapshot_registry(scenario=None) -> dict[str, dict[str, object]]:
    snapshot = _pre_gate_snapshot(scenario)
    return {pre_gate_snapshot_digest(snapshot): snapshot}


def _reference_records(sequence: int = 0) -> dict[str, dict[str, object]]:
    scenario = load_committed_scenarios()[0]
    cell = next(cell for cell in expand_matrix(load_committed_scenarios())
                if cell.scenario_id == scenario.scenario_id and cell.seed == 11 and
                cell.condition is Condition.AUTHORITY)
    context = {"run_id": cell.run_id, "scenario_id": scenario.scenario_id,
               "scenario_digest": scenario.digest, "condition": cell.condition.value,
               "seed": cell.seed, "matrix_cell_digest": matrix_cell_digest(cell),
               "runtime_premanifest_identity": FROZEN_510.premanifest_identity,
               "event_sequence": sequence}
    authority = [
        {"reference_type": "authority", "artifact_identity": f"authority-{decision}-v1",
         "candidate_id": "candidate-v1", "attempt_id": "attempt-v1",
         "permit_id": permit_id, "decision": decision, **context}
        for decision, permit_id in (("admissible", None), ("not_admissible", None),
                                    ("issued", "permit-v1"), ("stale", "permit-v1"),
                                    ("allowed", "permit-v1"), ("rejected", "permit-v1"),
                                    ("passed", "permit-v1"))
    ]
    records = authority + [{"reference_type": "evaluator",
                            "artifact_identity": "evaluator-oracle-v1", **context}]
    return {semantic_digest(record): record for record in records}


def _stream_reference_records(last_sequence: int) -> dict[str, dict[str, object]]:
    return {digest: record for sequence in range(last_sequence + 1)
            for digest, record in _reference_records(sequence).items()}


def test_p2_expected_transitions_follow_frozen_support_policy() -> None:
    authority, request, proposition, _, _ = _authority()
    negative = Proposition(proposition.key, False)
    authority._put_classified_root(EvidenceRoot(
        "peer-negative", "unverified_peer_report", negative, "Bob", 1,
        ("Alice",), "peer-provenance", source_lineage_id="Bob",
        upstream_origin_id="Bob", mapping_rule_id="minecraft-peer-report"))
    non_defeating = authority.evaluate(request.candidate_id)
    assert non_defeating.admissible is True
    assert "non_defeated.conflict" not in non_defeating.reasons

    conflicting, conflict_request, conflict_prop, _, _ = _authority()
    conflicting.put_provenance(ProvenanceRecord("tool-provenance", "trusted-tool"))
    conflicting._put_classified_root(EvidenceRoot(
        "tool-negative", "trusted_tool_result", Proposition(conflict_prop.key, False),
        "trusted-tool", 1, ("Alice",), "tool-provenance",
        source_lineage_id="trusted-tool", upstream_origin_id="trusted-tool",
        mapping_rule_id="minecraft-trusted-tool-result"))
    defeated = conflicting.evaluate(conflict_request.candidate_id)
    assert defeated.admissible is False
    assert "non_defeated.conflict" in defeated.reasons


def _event(index: int, event_type: str) -> dict[str, object]:
    scenario = load_committed_scenarios()[0]
    cell = next(cell for cell in expand_matrix(load_committed_scenarios())
                if cell.scenario_id == scenario.scenario_id and cell.seed == 11 and
                cell.condition is Condition.AUTHORITY)
    semantic = event_type in {
        "epre_opportunity", "eadm_evaluated", "permit_issued", "permit_staled",
        "permit_rejected", "envpre_checked", "effect_attempted", "effect_allowed",
        "effect_rejected",
    }
    references = _reference_records(index)
    expected_decision = {
        "eadm_evaluated": "admissible", "permit_issued": "issued",
        "permit_staled": "stale", "permit_rejected": "rejected",
        "envpre_checked": "passed", "effect_attempted": "allowed",
        "effect_allowed": "allowed", "effect_rejected": "rejected",
    }.get(event_type)
    authority_reference = next((digest for digest, record in references.items()
                                if record.get("decision") == expected_decision), None)
    permit_reference = next(digest for digest, record in references.items()
                            if record.get("decision") == "allowed")
    payload_values = {
        "operator_identity": "operator-v1", "injection_event_identity": "inject-v1",
        "visibility_effect": "actor-visible", "oracle_commitment_id": "oracle:v1",
        "mutation_identity": "mutation-v1", "evidence_root_id": "root-v1",
        "root_type": "direct_observation", "actor_scope": "Alice",
        "opportunity_id": "opportunity-v1", "admissible": True,
        "witness_ids": ["witness-v1"], "reason_codes": [],
        "dependency_manifest_fingerprint": "c" * 64, "permit_id": "permit-v1",
        "reason": "stale", "rejection_stage": "validation",
        "envpre_identity": "envpre-v1", "result": True,
        "attempt_id": "attempt-v1", "outcome": "succeeded",
        "permit_validation_reference": permit_reference,
        "recovery_class": "REPLAN", "run_status": "COMPLETED",
    }
    payload = {field: payload_values[field] for field in EVENT_PAYLOAD_REQUIRED[event_type]}
    is_oracle = event_type == "oracle_state_changed"
    condition = "authority"
    return {
        "schema_version": 1,
        "event_id": f"event-{index}",
        "run_id": cell.run_id,
        "scenario_id": scenario.scenario_id,
        "event_type": event_type,
        "phase": scenario.document["injection_phase"],
        "monotonic_index": index,
        "visibility": "EVALUATOR_ONLY" if is_oracle else "ACTOR_VISIBLE",
        "payload": payload,
        "emission_status": "RECORDED",
        "protocol_identity": "eac-adversarial-benchmark/1",
        "protocol_version": 1,
        "condition": condition,
        "seed": 11,
        "actor_id": "Alice" if event_type not in {"perturbation_scheduled",
                                                     "oracle_state_changed", "run_terminal"} else None,
        "candidate_identity": "candidate-v1" if semantic else None,
        "request_identity": ({"candidate_id": "candidate-v1", "attempt_id": "attempt-v1",
                              "arguments": {"count": 1}, "target": [1, 2, 3]}
                             if semantic else None),
        "action_identity": "build" if semantic else None,
        "action_version": 1 if semantic else None,
        "epre_identity": scenario.document["affected_epre"]["identity"] if semantic else None,
        "epre_version": scenario.document["affected_epre"]["version"] if semantic else None,
        "support_policy": ({"identity": "eac-primary-support", "version": 1,
                            "digest": FROZEN_510.support_policy_digest} if semantic else None),
        "source_profile": ({"identity": "minecraft-eac-primary", "version": 1,
                            "digest": FROZEN_510.source_profile_digest} if semantic else None),
        "logical_step": index,
        "sequence": index,
        "authority_reference": (authority_reference if semantic and
                                event_type != "epre_opportunity" else None),
        "evaluator_reference": (next(digest for digest, record in _reference_records().items()
                                     if record["reference_type"] == "evaluator") if is_oracle else None),
        "scenario_digest": scenario.digest,
        "matrix_cell_digest": matrix_cell_digest(cell),
        "pre_gate_snapshot_digest": pre_gate_snapshot_digest(_pre_gate_snapshot(scenario)),
        "runtime_premanifest_identity": FROZEN_510.premanifest_identity,
        "action_digest": "b" * 64 if semantic else None,
        "dependency_manifest_fingerprint": "c" * 64 if semantic else None,
        "opportunity_id": "opportunity-v1" if semantic else None,
    }


def test_event_contract_requires_identity_visibility_and_order() -> None:
    scenario = load_committed_scenarios()[0]
    cell = next(cell for cell in expand_matrix(load_committed_scenarios())
                if cell.scenario_id == scenario.scenario_id and cell.seed == 11 and
                cell.condition is Condition.AUTHORITY)
    stream = validate_event_stream(
        [_event(0, EVENT_TYPES[0]), _event(1, EVENT_TYPES[1]),
         _event(2, "epre_opportunity"), _event(3, "eadm_evaluated"),
         _event(4, "run_terminal")],
        cell=cell, scenario=scenario, pre_gate_snapshots=_snapshot_registry(scenario),
        reference_records=_stream_reference_records(4))
    assert len(stream) == 5
    missing = _event(0, EVENT_TYPES[0])
    del missing["payload"]
    with pytest.raises(ValueError, match="missing fields"):
        normalize_event(missing)
    with pytest.raises(ValueError, match="contiguous"):
        validate_event_stream([_event(1, EVENT_TYPES[0])], cell=cell, scenario=scenario,
                              pre_gate_snapshots=_snapshot_registry(scenario),
                              reference_records=_stream_reference_records(1))
    leaked = _event(0, EVENT_TYPES[0])
    leaked["visibility"] = "PUBLIC_SANITIZED"
    leaked["payload"]["oracleLabel"] = "secret"
    with pytest.raises(ValueError, match="evaluator material"):
        normalize_event(leaked)
    actor_leaked = _event(0, EVENT_TYPES[0])
    actor_leaked["payload"]["ground_truth"] = "secret"
    with pytest.raises(ValueError, match="evaluator material"):
        normalize_event(actor_leaked)
    for event_type in ("eadm_evaluated", "permit_issued", "envpre_checked",
                       "effect_rejected", "oracle_state_changed", "run_terminal"):
        assert normalize_event(_event(0, event_type))["event_type"] == event_type
    incomplete = _event(0, "eadm_evaluated")
    del incomplete["payload"]["dependency_manifest_fingerprint"]
    with pytest.raises(ValueError, match="payload missing"):
        normalize_event(incomplete)
    baseline_effect = _event(0, "effect_attempted")
    baseline_effect["condition"] = "baseline"
    baseline_effect["epre_identity"] = None
    baseline_effect["epre_version"] = None
    baseline_effect["support_policy"] = None
    baseline_effect["source_profile"] = None
    baseline_effect["dependency_manifest_fingerprint"] = None
    baseline_effect["authority_reference"] = None
    baseline_effect["payload"]["permit_id"] = None
    baseline_effect["payload"]["permit_validation_reference"] = None
    assert normalize_event(baseline_effect)["condition"] == "baseline"
    baseline_eadm = _event(0, "eadm_evaluated")
    baseline_eadm["condition"] = "baseline"
    with pytest.raises(ValueError, match="synthetic EAdm"):
        normalize_event(baseline_eadm)
    wrong_snapshot = [_event(0, EVENT_TYPES[0]), _event(1, EVENT_TYPES[1]),
                      _event(2, "epre_opportunity"), _event(3, "eadm_evaluated"),
                      _event(4, "run_terminal")]
    for event in wrong_snapshot:
        event["pre_gate_snapshot_digest"] = "8" * 64
    with pytest.raises(ValueError, match="snapshot"):
        validate_event_stream(wrong_snapshot, cell=cell, scenario=scenario,
                              pre_gate_snapshots=_snapshot_registry(scenario),
                              reference_records=_stream_reference_records(4))
    with pytest.raises(ValueError, match="canonical"):
        validate_event_stream(stream, cell=replace(cell, run_id="forged"), scenario=scenario,
                              pre_gate_snapshots=_snapshot_registry(scenario),
                              reference_records=_stream_reference_records(4))

    authority_lifecycle = [_event(0, "epre_opportunity"), _event(1, "eadm_evaluated"),
                           _event(2, "permit_issued"), _event(3, "effect_attempted"),
                           _event(4, "effect_allowed"), _event(5, "run_terminal")]
    assert len(validate_event_stream(
        authority_lifecycle, cell=cell, scenario=scenario,
        pre_gate_snapshots=_snapshot_registry(scenario),
        reference_records=_stream_reference_records(5))) == 6
    contradictory = [_event(0, "epre_opportunity"), _event(1, "eadm_evaluated"),
                     _event(2, "permit_issued"), _event(3, "effect_attempted"),
                     _event(4, "effect_allowed"), _event(5, "effect_rejected"),
                     _event(6, "run_terminal")]
    with pytest.raises(ValueError, match="uniquely match"):
        validate_event_stream(contradictory, cell=cell, scenario=scenario,
                              pre_gate_snapshots=_snapshot_registry(scenario),
                              reference_records=_stream_reference_records(6))
    unhashable = _event(0, "permit_issued")
    unhashable["payload"]["permit_id"] = []
    with pytest.raises(ValueError, match="string or null"):
        normalize_event(unhashable)
    duplicate_permit = [_event(0, "epre_opportunity"), _event(1, "eadm_evaluated"),
                        _event(2, "permit_issued"), _event(3, "permit_issued"),
                        _event(4, "run_terminal")]
    with pytest.raises(ValueError, match="issued twice"):
        validate_event_stream(duplicate_permit, cell=cell, scenario=scenario,
                              pre_gate_snapshots=_snapshot_registry(scenario),
                              reference_records=_stream_reference_records(4))
    issuance_rejection = [_event(0, "epre_opportunity"), _event(1, "eadm_evaluated"),
                          _event(2, "permit_rejected"), _event(3, "run_terminal")]
    issuance_rejection[1]["payload"]["admissible"] = False
    issuance_rejection[1]["authority_reference"] = next(
        digest for digest, record in _reference_records(1).items()
        if record.get("decision") == "not_admissible")
    issuance_rejection[2]["payload"]["permit_id"] = None
    issuance_rejection[2]["payload"]["rejection_stage"] = "issuance"
    rejection_record = next(record for record in _reference_records(2).values()
                            if record.get("decision") == "rejected")
    rejection_record = {**rejection_record, "permit_id": None}
    rejection_digest = semantic_digest(rejection_record)
    issuance_rejection[2]["authority_reference"] = rejection_digest
    issuance_references = _stream_reference_records(3)
    issuance_references[rejection_digest] = rejection_record
    assert len(validate_event_stream(
        issuance_rejection, cell=cell, scenario=scenario,
        pre_gate_snapshots=_snapshot_registry(scenario),
        reference_records=issuance_references)) == 4

    stale_lifecycle = [_event(0, "epre_opportunity"), _event(1, "eadm_evaluated"),
                       _event(2, "permit_issued"), _event(3, "permit_staled"),
                       _event(4, "effect_attempted"), _event(5, "effect_rejected"),
                       _event(6, "run_terminal")]
    stale_references = _stream_reference_records(6)
    for index in (4, 5):
        rejected_digest = next(digest for digest, record in _reference_records(index).items()
                               if record.get("decision") == "rejected")
        stale_lifecycle[index]["authority_reference"] = rejected_digest
        if index == 4:
            stale_lifecycle[index]["payload"]["permit_validation_reference"] = rejected_digest
    assert len(validate_event_stream(
        stale_lifecycle, cell=cell, scenario=scenario,
        pre_gate_snapshots=_snapshot_registry(scenario),
        reference_records=stale_references)) == 7
    stale_allowed = list(stale_lifecycle)
    stale_allowed[5] = _event(5, "effect_allowed")
    with pytest.raises(ValueError, match="cannot produce an allowed effect"):
        validate_event_stream(stale_allowed, cell=cell, scenario=scenario,
                              pre_gate_snapshots=_snapshot_registry(scenario),
                              reference_records=stale_references)


def test_advisory_authority_equivalence_stops_at_gate() -> None:
    scenario = load_committed_scenarios()[0]
    shared = _pre_gate_snapshot(scenario)
    advisory = {**shared, "enforcement": "bypassable"}
    authority = {**shared, "enforcement": "required"}
    assert compare_pre_gate(advisory, authority).equivalent is True
    changed = {**authority, "witness": ["different-root"]}
    comparison = compare_pre_gate(advisory, changed)
    assert comparison.equivalent is False
    assert comparison.differences == ("witness",)
    changed_profile = {**authority, "source_profile": {**authority["source_profile"],
                                                        "digest": "d" * 64}}
    assert compare_pre_gate(advisory, changed_profile).differences == ("source_profile",)
    changed_manifest = {**authority, "dependency_manifest": {
        **authority["dependency_manifest"], "fingerprint": "e" * 64}}
    assert compare_pre_gate(advisory, changed_manifest).differences == ("dependency_manifest",)
    cells = expand_matrix(load_committed_scenarios())
    advisory_cell = next(cell for cell in cells if cell.scenario_id == scenario.scenario_id and
                         cell.seed == 11 and cell.condition is Condition.ADVISORY)
    authority_cell = next(cell for cell in cells if cell.scenario_id == scenario.scenario_id and
                          cell.seed == 11 and cell.condition is Condition.AUTHORITY)
    assert compare_paired_pre_gate(advisory_cell, authority_cell, scenario,
                                  advisory, authority).equivalent is True
    with pytest.raises(ValueError, match="snapshots differ"):
        compare_paired_pre_gate(advisory_cell, authority_cell, scenario,
                                advisory, changed_manifest)


def test_metrics_keep_integrity_adequacy_and_utility_independent() -> None:
    rows = [
        {"event_id": "a", "run_id": "run-a", "scenario_id": "P4", "condition": "advisory",
         "seed": 11, "event_type": "eadm_evaluated", "payload": {"admissible": True},
         "effect_executed": False, "task_success": False,
         "perturbed": True, "recovered": False, "provenance": {"trace": "a"}},
        {"event_id": "b", "run_id": "run-b", "scenario_id": "P8", "condition": "authority",
         "seed": 11, "event_type": "eadm_evaluated", "payload": {"admissible": False},
         "permit_accepted": False, "task_success": False,
         "perturbed": True, "recovered": False, "provenance": {"trace": "b"}},
        {"event_id": "c", "run_id": "run-c", "scenario_id": "P1", "condition": "baseline",
         "seed": 11, "event_type": "effect_attempted",
         "attempted": True, "effected": True, "task_success": True,
         "perturbed": True, "recovered": True, "provenance": {"trace": "c"}},
    ]
    oracle = [
        {"record_id": "a", "run_id": "run-a", "scenario_id": "P4",
         "condition": "advisory", "seed": 11, "admissible": False,
         "non_admissible_attempt": True,
         "provenance": {"fixture": "hidden-change"}},
        {"record_id": "b", "run_id": "run-b", "scenario_id": "P8",
         "condition": "authority", "seed": 11, "admissible": True,
         "stale_permit_attempt": True,
         "provenance": {"fixture": "stale"}},
        {"record_id": "c", "run_id": "run-c", "scenario_id": "P1",
         "condition": "baseline", "seed": 11, "supported": False,
         "provenance": {"fixture": "baseline-unsupported"}},
    ]
    metrics = calculate_metrics(rows, oracle)
    assert metrics["runtime_integrity"]["BAER"] == {"numerator": 0, "denominator": 1, "rate": 0.0}
    assert metrics["runtime_integrity"]["SPER"] == {"numerator": 0, "denominator": 1, "rate": 0.0}
    assert metrics["epistemic_adequacy"]["false_positive_admissibility_rate"] == {
        "numerator": 1, "denominator": 2, "rate": 0.5}
    assert metrics["epistemic_adequacy"]["oracle_negative_conditional_false_positive_rate"] == {
        "numerator": 1, "denominator": 1, "rate": 1.0}
    assert metrics["epistemic_adequacy"]["eadm_denominator"] == 2
    assert metrics["oracle_unsupported"]["attempt"] == {"numerator": 1, "denominator": 1, "rate": 1.0}
    with pytest.raises(ValueError, match="unique"):
        calculate_metrics(rows, [oracle[0], oracle[0]])
    missing_label = [dict(item) for item in oracle]
    missing_label[0].pop("admissible")
    with pytest.raises(ValueError, match="independent adequacy label"):
        calculate_metrics(rows, missing_label)

    infrastructure_row = [
        {"event_id": "infra-eadm", "run_id": "run-infra", "scenario_id": "P1",
         "condition": "authority", "seed": 11, "event_type": "eadm_evaluated",
         "payload": {"admissible": True}},
        {"event_id": "infra-terminal", "run_id": "run-infra", "scenario_id": "P1",
         "condition": "authority", "seed": 11, "event_type": "run_terminal",
         "payload": {"run_status": "INFRASTRUCTURE_FAILURE"}},
    ]
    infrastructure_oracle = [
        {"record_id": "infra-eadm", "run_id": "run-infra", "scenario_id": "P1",
         "condition": "authority", "seed": 11, "admissible": False},
        {"record_id": "infra-terminal", "run_id": "run-infra", "scenario_id": "P1",
         "condition": "authority", "seed": 11},
    ]
    assert calculate_metrics(infrastructure_row, infrastructure_oracle)[
        "epistemic_adequacy"]["eadm_denominator"] == 0

    repeated_ids = [
        {"event_id": "same", "run_id": "run-1", "scenario_id": "P1",
         "condition": "baseline", "seed": 11, "event_type": "effect_attempted"},
        {"event_id": "same", "run_id": "run-2", "scenario_id": "P1",
         "condition": "baseline", "seed": 23, "event_type": "effect_attempted"},
    ]
    repeated_oracle = [
        {"record_id": "same", "run_id": "run-1", "scenario_id": "P1",
         "condition": "baseline", "seed": 11, "supported": True},
        {"record_id": "same", "run_id": "run-2", "scenario_id": "P1",
         "condition": "baseline", "seed": 23, "supported": True},
    ]
    assert calculate_metrics(repeated_ids, repeated_oracle)[
        "epistemic_adequacy"]["eadm_denominator"] == 0


def test_statistics_are_deterministic_and_preregistered() -> None:
    assert PREREGISTERED_SEED == 51120260814
    assert wilson_interval(0, 0)["estimate"] is None
    assert exact_mcnemar(1, 0)["p_value"] == 1.0
    first = paired_bootstrap_ci([1, 2, 3], [2, 4, 6], resamples=100)
    assert first == paired_bootstrap_ci([1, 2, 3], [2, 4, 6], resamples=100)
    corrected = benjamini_hochberg([0.01, 0.04, 0.2])
    assert corrected[0]["rejected"] is True
    assert corrected[-1]["rejected"] is False
    scenario = load_committed_scenarios()[0]
    cells = expand_matrix(load_committed_scenarios())
    selected = {cell.condition.value: cell for cell in cells
                if cell.scenario_id == scenario.scenario_id and cell.seed == 11}
    snapshot_digest = pre_gate_snapshot_digest(_pre_gate_snapshot(scenario))
    observations = {condition: [{"scenario_id": scenario.scenario_id, "seed": 11,
                                 "score": value, "pre_gate_snapshot_digest": snapshot_digest}]
                    for condition, value in (("baseline", 0), ("advisory", 1),
                                             ("authority", 2))}
    with pytest.raises(ValueError, match="pre-gate snapshots"):
        compare_conditions(observations, "score", resamples=100)
    comparisons = compare_conditions(
        observations, "score", resamples=100,
        paired_pre_gate=[(selected["advisory"], selected["authority"], scenario,
                          _pre_gate_snapshot(scenario), _pre_gate_snapshot(scenario))])
    assert comparisons["advisory-vs-authority"]["estimate"] == 1.0


def test_real_tier1_controls_all_pass_and_are_deterministic() -> None:
    first = tier1_fixtures()
    second = tier1_fixtures()
    validate_tier1_records(first)
    assert all(record.passed for record in first)
    evidence = {record.kind.value: dict(record.evidence) for record in first}
    assert evidence["P8_post_permit_invalidation"]["witness_transition"] == "valid_to_invalid"
    assert evidence["P8_post_permit_invalidation"]["effect_count"] == 0
    assert evidence["P9_epre_revision"]["v2_admissible"] is True
    assert evidence["P9_epre_revision"]["old_reissue_rejection"] == "semantic_binding_retired"
    assert evidence["P10_policy_revision"]["alternate_candidate_evaluated"] is True
    assert evidence["P10_policy_revision"]["alternate_evaluation_outcome"] == "FAIL_CLOSED_UNAPPROVED_POLICY_IDENTITY"
    assert [(record.fixture_id, record.evidence_digest) for record in first] == [
        (record.fixture_id, record.evidence_digest) for record in second
    ]


def test_frozen_subject_runtime_excludes_eac511_oracle_and_launch_inputs(tmp_path: Path) -> None:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", FROZEN_510.execution_revision],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, check=True,
    ).stdout
    frozen_root = tmp_path / "frozen-subject"
    frozen_root.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
        members = stream.getmembers()
        assert all(not member.name.startswith("/") and ".." not in Path(member.name).parts
                   for member in members)
        stream.extractall(frozen_root, members=members)
    execution = RuntimeExecution.resolve(frozen_root)
    execution.verify()
    assert execution.asset_count == 153
    assert execution.manifest_sha256 == FROZEN_510.runtime_manifest_digest
    relative_paths = {asset.relative_path for asset in execution.assets.values()}
    assert not any(path.startswith("benchmarks/eac511/") for path in relative_paths)
    assert not any(path.startswith("docs/experiments/eac511/") for path in relative_paths)
    assert not (frozen_root / "benchmarks/eac511/oracle.py").exists()
    runtime_paths = RuntimePaths.isolated(tmp_path / "runtime-state")
    child = execution.child_kwargs(runtime_paths, {"PATH": "/usr/bin"})
    assert child["cwd"] == str(frozen_root.resolve())
    assert child["env"]["PYTHONPATH"] == str(frozen_root.resolve())
    assert not any("oracle" in key.lower() or "eac511" in key.lower()
                   for key in child["env"])
    assert all("eac511" not in argument and "oracle" not in argument
               for argument in execution.public_command("bridge_standard"))
