import pytest

from benchmarks.common.eac.canonical import canonical_sha256
from benchmarks.minecraft.k6_fixture import construct_k6_trial, validate_paired_construction
from benchmarks.minecraft.k6_protocol import (
    build_control_cells,
    build_primary_cells,
    validate_k6_trace,
)


def _primary(family, inventory, condition="dual_dag_authority", actor="Alice"):
    return next(cell for cell in build_primary_cells()
                if cell.scenario_family == family and cell.inventory_id == inventory
                and cell.condition == condition and cell.affected_actor == actor)


def _control(family, inventory="I1", condition="dual_dag_authority"):
    return next(cell for cell in build_control_cells()
                if cell.scenario_family == family and cell.inventory_id == inventory
                and cell.condition == condition)


INVENTORY_IDS = ("I1", "I2", "I3", "I4", "I5")
PAIR_CASES = (
    [(family, inventory, "Alice") for family in ("S1", "S2") for inventory in INVENTORY_IDS]
    + [("S3", inventory, actor) for inventory in INVENTORY_IDS for actor in ("Alice", "Bob")]
    + [(family, inventory, "Alice") for family in ("C1", "C2") for inventory in INVENTORY_IDS]
)


@pytest.mark.parametrize("family", ("S1", "S2"))
@pytest.mark.parametrize("inventory", ("I1", "I2", "I3", "I4", "I5"))
def test_all_single_actor_primary_strata_are_constructible_without_submission(family, inventory):
    trial = construct_k6_trial(_primary(family, inventory))
    assert not any(trial.native_calls.values())
    assert trial.r_p["EAdm"] is True
    assert trial.r_d["current_EAdm"] is False
    assert trial.same_prepared_object is True
    assert trial.mutation_state["hidden_truth_ingested"] is False
    if family == "S1":
        assert trial.mutation_state["superseded_root_id"] is not None
    else:
        assert trial.mutation_state["superseded_root_id"] is None
        assert trial.mutation_state["contradiction"] == {
            "positive_current": True, "negative_current": True,
            "positive_supersedes": [], "negative_supersedes": [], "non_defeated": False,
        }


@pytest.mark.parametrize("inventory", ("I1", "I2", "I3", "I4", "I5"))
@pytest.mark.parametrize("actor", ("Alice", "Bob"))
def test_all_s3_strata_and_actor_directions_are_constructible(inventory, actor):
    trial = construct_k6_trial(_primary("S3", inventory, actor=actor))
    other = "Bob" if actor == "Alice" else "Alice"
    assert not any(trial.native_calls.values())
    assert trial.r_p["EAdm"] is True and trial.r_d["current_EAdm"] is False
    assert trial.mutation_state["actor_current_EAdm"] == {actor: False, other: True}
    assert trial.mutation_state["cross_actor_dependency_leak"] is False
    assert trial.mutation_state["cross_actor_state_change_leak"] is False


@pytest.mark.parametrize("inventory", ("I1", "I2", "I3", "I4", "I5"))
def test_controls_are_constructible_and_hidden_truth_is_not_ingested(inventory):
    c1 = construct_k6_trial(_control("C1", inventory))
    c2 = construct_k6_trial(_control("C2", inventory))
    assert c1.r_d["current_EAdm"] is True
    assert c1.r_d["relevant_action_dependency_changed"] is False
    assert c2.r_d["current_EAdm"] is True
    assert c2.r_d["authority_epoch"] == c2.r_p["authority_epoch"]
    assert c2.mutation_state["evidence_total_after"] == c2.mutation_state["evidence_total_before"]
    assert c2.mutation_state["hidden_truth_ingested"] is False
    assert c2.mutation_state["evaluator_truth_before"] != c2.mutation_state["evaluator_truth_after"]
    assert c2.mutation_state["evaluator_truth_changed"] is True
    assert c2.mutation_state["evaluator_truth_before_digest"] == canonical_sha256(
        c2.mutation_state["evaluator_truth_before"]).removeprefix("sha256:")
    assert c2.mutation_state["evaluator_truth_after_digest"] == canonical_sha256(
        c2.mutation_state["evaluator_truth_after"]).removeprefix("sha256:")
    assert c2.mutation_state["evaluator_truth_authority_input"] is False
    assert c2.mutation_state["evaluator_truth_precondition_input"] is False
    assert not any(c1.native_calls.values()) and not any(c2.native_calls.values())


@pytest.mark.parametrize("condition,expected_native", (
    ("dual_dag_advisory", True),
    ("dual_dag_authority", False),
))
def test_bounded_representative_s1_submission_validates(condition, expected_native):
    trial = construct_k6_trial(_primary("S1", "I1", condition=condition))
    trace = trial.submit()
    assert validate_k6_trace(trace, cell=trial.cell) == trace
    assert trace["r_e"]["native_callable_reached"] is expected_native
    assert trace["exact_action"] == {
        "same_prepared_object": True, "exact_action_preserved": True,
    }
    assert trace["semantic_bindings"]["epre_classification"]["digest"]


@pytest.mark.parametrize("family,inventory,actor", PAIR_CASES)
def test_full_census_paired_construction_is_identical_before_enforcement(
    family, inventory, actor,
):
    if family in {"C1", "C2"}:
        advisory_cell = _control(family, inventory, condition="dual_dag_advisory")
        authority_cell = _control(family, inventory, condition="dual_dag_authority")
    else:
        advisory_cell = _primary(
            family, inventory, condition="dual_dag_advisory", actor=actor)
        authority_cell = _primary(
            family, inventory, condition="dual_dag_authority", actor=actor)
    advisory = construct_k6_trial(advisory_cell)
    authority = construct_k6_trial(authority_cell)
    assert not any(advisory.native_calls.values()) and not any(authority.native_calls.values())
    validate_paired_construction(advisory, authority)


def test_c2_pairing_rejects_different_hidden_evaluator_transition():
    advisory = construct_k6_trial(_control(
        "C2", "I1", condition="dual_dag_advisory"))
    authority = construct_k6_trial(_control(
        "C2", "I1", condition="dual_dag_authority"))
    advisory.mutation_state["evaluator_truth_after"] = {
        "hidden_target_available": "different",
    }
    with pytest.raises(ValueError, match="paired evaluator truth differs"):
        validate_paired_construction(advisory, authority)
    assert not any(advisory.native_calls.values()) and not any(authority.native_calls.values())


def test_bounded_representative_s2_and_s3_submissions_validate():
    s2 = construct_k6_trial(_primary("S2", "I4"))
    assert validate_k6_trace(s2.submit(), cell=s2.cell)["r_d"]["current_EAdm"] is False
    for actor in ("Alice", "Bob"):
        s3 = construct_k6_trial(_primary("S3", "I5", actor=actor))
        trace = s3.submit()
        assert validate_k6_trace(trace, cell=s3.cell) == trace
        assert trace["s3"]["unaffected_current_EAdm"] is True
        assert trace["s3"]["unaffected_r_e"]["execution_allowed"] is True
        assert trace["s3"]["unaffected_r_e"]["native_callable_reached"] is True


def test_bounded_control_submissions_remain_usable():
    for family in ("C1", "C2"):
        trial = construct_k6_trial(_control(family))
        trace = trial.submit()
        assert trace["r_d"]["current_EAdm"] is True
        assert trace["r_e"]["execution_allowed"] is True
        assert trace["r_e"]["native_callable_reached"] is True


def test_reconstructed_prepared_action_is_rejected_before_native_submission():
    first = construct_k6_trial(_primary("S1", "I1", condition="dual_dag_advisory"))
    second = construct_k6_trial(_primary("S1", "I1", condition="dual_dag_advisory"))
    first._prepared_actions["Alice"] = second._prepared_actions["Alice"]
    with pytest.raises(RuntimeError, match="reconstructed or substituted"):
        first.submit()
    assert not any(first.native_calls.values())


def test_reconsideration_drift_is_rejected_before_native_submission():
    trial = construct_k6_trial(_primary("S1", "I1", condition="dual_dag_advisory"))
    trial.counters["model_calls"] = 1
    with pytest.raises(RuntimeError, match="no-reconsideration"):
        trial.submit()
    assert not any(trial.native_calls.values())
