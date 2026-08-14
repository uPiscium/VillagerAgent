from __future__ import annotations

from collections import Counter

import pytest

from benchmarks.eac511.equivalence import compare_pre_gate
from benchmarks.eac511.events import normalize_event, validate_event_stream
from benchmarks.eac511.fixtures import tier1_fixtures, validate_tier1_records
from benchmarks.eac511.identity import (FROZEN_510, detached_digest, verify_detached,
                                        verify_frozen_runtime_inputs)
from benchmarks.eac511.matrix import expand_matrix, paired_cell_equal
from benchmarks.eac511.metrics import calculate_metrics
from benchmarks.eac511.model import Condition, PerturbationFamily, SEEDS, Tier
from benchmarks.eac511.oracle import sanitize_publication
from benchmarks.eac511.perturbations import apply_operator, perturbation_plan
from benchmarks.eac511.protocol import (
    EVENT_TYPES,
    event_schema_document,
    load_committed_protocol,
    load_committed_scenarios,
    protocol_document,
    scenario_definitions,
    scenario_schema_document,
    scenario_set_document,
    validate_scenario,
)
from benchmarks.eac511.statistics import (
    PREREGISTERED_SEED,
    benjamini_hochberg,
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
    verify_detached(scenario_schema_document())
    verify_detached(event_schema_document())
    assert protocol["experiment_flags"]["final_execution_authorized"] is False
    assert protocol["experiment_flags"]["judged_execution"] is False
    assert protocol["experiment_flags"]["production"] is False
    assert protocol["frozen_inputs"] == FROZEN_510.as_dict()
    assert verify_frozen_runtime_inputs()["runtime_manifest_digest"] == FROZEN_510.runtime_manifest_digest


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


def _event(index: int, event_type: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "event_id": f"event-{index}",
        "run_id": "run-1",
        "scenario_id": "P8-post-permit-invalidation",
        "event_type": event_type,
        "phase": "AFTER_PERMIT_BEFORE_EFFECT",
        "monotonic_index": index,
        "visibility": "ACTOR_VISIBLE",
        "payload": {},
        "emission_status": "RECORDED",
    }


def test_event_contract_requires_identity_visibility_and_order() -> None:
    stream = validate_event_stream([_event(0, EVENT_TYPES[0]), _event(1, EVENT_TYPES[1])])
    assert len(stream) == 2
    missing = _event(0, EVENT_TYPES[0])
    del missing["payload"]
    with pytest.raises(ValueError, match="missing fields"):
        normalize_event(missing)
    with pytest.raises(ValueError, match="contiguous"):
        validate_event_stream([_event(1, EVENT_TYPES[0])])
    leaked = _event(0, EVENT_TYPES[0])
    leaked["visibility"] = "PUBLIC_SANITIZED"
    leaked["payload"] = {"oracleLabel": "secret"}
    with pytest.raises(ValueError, match="evaluator material"):
        normalize_event(leaked)
    actor_leaked = _event(0, EVENT_TYPES[0])
    actor_leaked["payload"] = {"ground_truth": "secret"}
    with pytest.raises(ValueError, match="evaluator material"):
        normalize_event(actor_leaked)


def test_advisory_authority_equivalence_stops_at_gate() -> None:
    shared = {
        "required_evidence": ["root"],
        "epre": {"identity": "mine", "version": 1},
        "classification": "dual-class",
        "policy": FROZEN_510.as_dict()["support_policy"],
        "witness": ["root"],
        "eadm": "ADMISSIBLE",
        "candidate": "mineblock-1",
        "task": "fixture-1",
    }
    advisory = {**shared, "enforcement": "bypassable"}
    authority = {**shared, "enforcement": "required"}
    assert compare_pre_gate(advisory, authority).equivalent is True
    changed = {**authority, "witness": ["different-root"]}
    comparison = compare_pre_gate(advisory, changed)
    assert comparison.equivalent is False
    assert comparison.differences == ("witness",)


def test_metrics_keep_integrity_adequacy_and_utility_independent() -> None:
    rows = [
        {"event_id": "a", "scenario_id": "P4", "effect_executed": False,
         "eadm": True, "task_success": False,
         "perturbed": True, "recovered": False, "provenance": {"trace": "a"}},
        {"event_id": "b", "scenario_id": "P8", "permit_accepted": False,
         "eadm": False, "task_success": False,
         "perturbed": True, "recovered": False, "provenance": {"trace": "b"}},
    ]
    oracle = [
        {"record_id": "a", "scenario_id": "P4", "admissible": False,
         "non_admissible_attempt": True,
         "provenance": {"fixture": "hidden-change"}},
        {"record_id": "b", "scenario_id": "P8", "admissible": False,
         "stale_permit_attempt": True,
         "provenance": {"fixture": "stale"}},
    ]
    metrics = calculate_metrics(rows, oracle)
    assert metrics["runtime_integrity"]["BAER"] == {"numerator": 0, "denominator": 1, "rate": 0.0}
    assert metrics["runtime_integrity"]["SPER"] == {"numerator": 0, "denominator": 1, "rate": 0.0}
    assert metrics["epistemic_adequacy"]["false_positive_rate"]["numerator"] == 1
    assert metrics["task_utility"]["success"]["rate"] == 0.0
    assert metrics["task_utility"]["recovery"]["rate"] == 0.0
    with pytest.raises(ValueError, match="unique"):
        calculate_metrics(rows, [oracle[0], oracle[0]])


def test_statistics_are_deterministic_and_preregistered() -> None:
    assert PREREGISTERED_SEED == 51120260814
    assert wilson_interval(0, 0)["estimate"] is None
    assert exact_mcnemar(1, 0)["p_value"] == 1.0
    first = paired_bootstrap_ci([1, 2, 3], [2, 4, 6], resamples=100)
    assert first == paired_bootstrap_ci([1, 2, 3], [2, 4, 6], resamples=100)
    corrected = benjamini_hochberg([0.01, 0.04, 0.2])
    assert corrected[0]["rejected"] is True
    assert corrected[-1]["rejected"] is False


def test_real_tier1_controls_all_pass_and_are_deterministic() -> None:
    first = tier1_fixtures()
    second = tier1_fixtures()
    validate_tier1_records(first)
    assert all(record.passed for record in first)
    assert [(record.fixture_id, record.evidence_digest) for record in first] == [
        (record.fixture_id, record.evidence_digest) for record in second
    ]
