import json

from benchmarks.tdw_mat.comparison import CONDITIONS, main, run_fixture_comparison


def test_fixture_comparison_runs_four_conditions_on_fixed_subset():
    payload = run_fixture_comparison("configs/tdw_mat/subset.json")

    assert payload["performance_claim"] is False
    assert payload["subset_size"] == 4
    assert len(payload["episodes"]) == 16
    assert set(payload["condition_summary"]) == set(CONDITIONS)
    current = payload["condition_summary"]["current_communication"]
    disabled = payload["condition_summary"]["communication_disabled"]
    voi = payload["condition_summary"]["value_of_information"]
    assert current["mean_communication_count"] == 1.0
    assert disabled["mean_communication_count"] == 0.0
    assert voi["mean_communication_count"] == 0.0
    assert current["mean_communication_utility"] == 1.0
    assert current["mean_transport_rate_delta_vs_disabled"] == 0.0
    assert current["mean_step_delta_vs_disabled"] == 1.0
    assert current["feasibility_prediction_precision"] == 0.5
    assert current["feasibility_prediction_recall"] == 0.5
    assert current["false_feasible_action_rate"] == 0.5
    assert current["false_infeasible_action_rate"] == 0.5
    assert current["recovery_after_failure_rate"] == 1.0
    assert current["mean_information_action_to_progress_latency"] == 4.0
    current_episode = next(
        row for row in payload["episodes"] if row["condition"] == "current_communication"
    )
    voi_episode = next(
        row for row in payload["episodes"] if row["condition"] == "value_of_information"
    )
    assert current_episode["communication_decision"]["decision"] == "communicate"
    assert voi_episode["communication_decision"]["decision"] == "act_physically"


def test_comparison_contains_physical_and_information_candidates():
    payload = run_fixture_comparison("configs/tdw_mat/subset.json")
    treatment = next(
        row for row in payload["episodes"]
        if row["condition"] == "current_communication"
    )
    candidates = treatment["dual_dag_artifact"]["action_candidates"]

    assert treatment["dual_dag_artifact"]["used_for_decision"] is True
    assert any(row["state"] == "information_action" for row in candidates)
    assert any(row["action_type"] == "grasp" for row in candidates)
    assert any(row["action_type"] == "send_message" for row in candidates)


def test_comparison_cli_writes_machine_readable_report(tmp_path):
    output = tmp_path / "comparison.json"

    assert main(["--output", str(output)]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["comparison_type"] == "fixture_policy_smoke"
    assert len(payload["episodes"]) == 16
