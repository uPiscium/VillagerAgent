import copy

import pytest

from benchmarks.minecraft.k10_fixture import (
    construct_k10_trial, real_submission_count, validate_paired_construction,
)
from benchmarks.minecraft.k10_protocol import build_k10_cells, EXACT_FIELDS


def test_every_k10_cell_is_construction_only():
    before = real_submission_count()
    trials = [construct_k10_trial(cell) for cell in build_k10_cells()]
    assert real_submission_count() == before
    assert all(not any(trial.native_calls.values()) for trial in trials)
    assert all(trial.r_p["EAdm"] is True for trial in trials)
    assert all((trial.r_d["current_EAdm"] is False if trial.cell.matrix == "primary" else
                trial.r_d["current_EAdm"] is True) for trial in trials)
    assert all(trial.selected_request_digest.startswith("sha256:") for trial in trials)


@pytest.mark.parametrize("family", ("S1", "S2", "S3", "C1", "C2"))
def test_family_mutation_contracts(family):
    trials = [construct_k10_trial(c) for c in build_k10_cells() if c.scenario_family == family]
    assert trials
    expected = {
        "S1": "opposite_polarity_explicit_supersession",
        "S2": "independent_opposite_trusted_tool_result",
        "S3": "affected_actor_explicit_supersession",
        "C1": "unrelated_weather_visible_update",
        "C2": "evaluator_only_hidden_truth_mutation",
    }[family]
    assert all(t.mutation_state["mutation_type"] == expected for t in trials)
    assert all(t.mutation_state["hidden_truth_ingested"] is False for t in trials)


def test_all_sixty_pairs_validate_and_tampering_is_rejected():
    cells = build_k10_cells()
    for cell in cells:
        if cell.condition != "dual_dag_advisory":
            continue
        other = next(c for c in cells if c.cell_id == cell.cell_id.replace(
            "dual_dag_advisory", "dual_dag_authority"))
        advisory, authority = construct_k10_trial(cell), construct_k10_trial(other)
        validate_paired_construction(advisory, authority)
        authority.mutation_state = copy.deepcopy(authority.mutation_state)
        authority.mutation_state["hidden_truth_ingested"] = True
        with pytest.raises(ValueError):
            validate_paired_construction(advisory, authority)


def test_construction_does_not_call_execute_prepared(monkeypatch):
    from benchmarks.minecraft import eac_runtime
    def forbidden(*args, **kwargs):
        raise AssertionError("construction crossed execute_prepared")
    monkeypatch.setattr(eac_runtime.MinecraftEACRuntime, "execute_prepared", forbidden)
    trial = construct_k10_trial(build_k10_cells()[0])
    assert not any(trial.native_calls.values())
    assert trial.same_prepared_object
    assert all(trial.r_p[name] is not None for name in EXACT_FIELDS)


def test_c2_mutates_only_detached_evaluator_truth():
    cell = next(cell for cell in build_k10_cells()
                if cell.scenario_family == "C2"
                and cell.condition == "dual_dag_authority")
    trial = construct_k10_trial(cell)
    assert trial.detached_evaluator_truth is not None
    assert trial.detached_evaluator_truth.snapshot() == {"hidden_target_available": False}
    assert trial.mutation_state["evaluator_truth_before"] == {"hidden_target_available": True}
    assert trial.mutation_state["evaluator_truth_after"] == {"hidden_target_available": False}
    assert trial.mutation_state["authority_epoch_before"] == trial.mutation_state["authority_epoch_after"]
    assert trial.mutation_state["evidence_total_before"] == trial.mutation_state["evidence_total_after"]
    assert trial.mutation_state["evaluator_truth_authority_input"] is False
    assert trial.mutation_state["evaluator_truth_precondition_input"] is False
    assert not any(trial.native_calls.values())
