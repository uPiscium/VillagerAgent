import json

from benchmarks.common.actions import ActionSpec, InformationActionSpec
from benchmarks.common.adapter import BenchmarkAdapter
from benchmarks.tdw_mat.adapter import TDWMATAdapter, TDWMATConfig
from benchmarks.tdw_mat.mock_env import mock_tdw_mat_env_factory
from benchmarks.tdw_mat.smoke import main, run_fixture_smoke


def _adapter() -> TDWMATAdapter:
    adapter = TDWMATAdapter(config=TDWMATConfig(), env_factory=mock_tdw_mat_env_factory)
    adapter.reset(episode_id="tdw-mat-test", seed=2824)
    return adapter


def test_adapter_satisfies_common_contract_and_uses_official_scenario_shape():
    adapter: BenchmarkAdapter = TDWMATAdapter(
        config=TDWMATConfig(), env_factory=mock_tdw_mat_env_factory
    )

    episode = adapter.reset(episode_id="tdw-mat-test", seed=2824)

    assert episode.benchmark == "tdw_mat"
    assert episode.agent_ids == ("agent_0", "agent_1")
    assert episode.metadata == {"scene": "5a", "layout": "0_0", "task": "food"}
    assert adapter.capabilities("agent_0").can_communicate is True


def test_decision_context_projects_symbolic_state_without_sensor_arrays():
    context = _adapter().decision_context("agent_0")
    serialized = repr(context)

    context.validate_agent_facing()
    assert "bread" in serialized
    assert "transport_goal" in serialized
    assert "rgb" not in serialized
    assert "depth" not in serialized
    assert "seg_mask" not in serialized
    assert any(
        node["source_kind"] == "task_goal" and node["visibility"]["public"] is True
        for node in context.visible_epistemic_nodes
    )
    assert any(candidate["state"] == "uncertain_feasibility" for candidate in context.visible_candidates)
    assert any(candidate["state"] == "information_action" for candidate in context.visible_candidates)
    snapshot = _adapter().dual_dag_snapshot("agent_0")
    assert snapshot["benchmark"] == "tdw_mat"
    assert any(node["node_type"] == "observed_fact" for node in snapshot["epistemic_dag"]["nodes"])


def test_message_is_information_action_and_public_reported_claim():
    adapter = _adapter()

    result = adapter.execute_information_action(
        "agent_0",
        InformationActionSpec(
            action_id="send_message:agent_0",
            action_type="send_message",
            parameters={"message": "bread is in the kitchen"},
            information_subtype="send_message",
        ),
    )

    assert result.succeeded is True
    assert result.metrics["communication_count"] == 1
    assert any(record.source_kind == "agent_message" for record in adapter.get_observation("agent_1"))
    assert adapter.get_public_observation()[0].visibility.public is True
    assert any(
        node["node_type"] == "reported_claim"
        for node in adapter.dual_dag_snapshot("agent_1")["epistemic_dag"]["nodes"]
    )
    assert adapter.final_metrics()["goal_relevant_communication_rate"] == 1.0


def test_invalid_physical_attempt_is_counted_as_false_feasible():
    adapter = _adapter()
    invalid = ActionSpec(
        action_id="grasp:agent_0:999:left",
        action_type="grasp",
        parameters={"object_id": 999, "arm": "left"},
    )

    result = adapter.execute_action("agent_0", invalid)
    metrics = adapter.final_metrics()

    assert result.succeeded is False
    assert metrics["invalid_physical_action_count"] == 1
    assert metrics["false_feasible_action_rate"] == 1.0


def test_feasibility_recovery_and_information_latency_metrics_are_reported():
    adapter = _adapter()
    adapter.execute_information_action(
        "agent_0",
        InformationActionSpec(
            action_id="send_message:agent_0",
            action_type="send_message",
            parameters={"message": "bread is in the kitchen"},
            information_subtype="send_message",
        ),
    )
    actions = (
        ActionSpec(
            action_id="known-invalid",
            action_type="grasp",
            parameters={"object_id": 999, "arm": "left", "predicted_feasible": False},
        ),
        ActionSpec(
            action_id="false-infeasible",
            action_type="grasp",
            parameters={"object_id": 10, "arm": "left", "predicted_feasible": False},
        ),
        ActionSpec(
            action_id="false-feasible",
            action_type="grasp",
            parameters={"object_id": 10, "arm": "left", "predicted_feasible": True},
        ),
        ActionSpec(
            action_id="drop-after-failure",
            action_type="drop",
            parameters={"arm": "left", "predicted_feasible": True},
        ),
    )
    for action in actions:
        adapter.execute_action("agent_0", action)

    metrics = adapter.final_metrics()
    assert metrics["feasibility_true_positive"] == 1
    assert metrics["feasibility_false_positive"] == 1
    assert metrics["feasibility_true_negative"] == 1
    assert metrics["feasibility_false_negative"] == 1
    assert metrics["feasibility_prediction_precision"] == 0.5
    assert metrics["feasibility_prediction_recall"] == 0.5
    assert metrics["false_feasible_action_rate"] == 0.5
    assert metrics["false_infeasible_action_rate"] == 0.5
    assert metrics["recovery_after_failure_rate"] == 1.0
    assert metrics["communication_utility"] == 1.0
    assert metrics["information_action_to_progress_latency"] == 4.0
    assert metrics["action_throughput"] == 0.2


def test_fixture_smoke_completes_transport_and_reports_costs():
    payload = run_fixture_smoke()

    assert payload["smoke_type"] == "fixture_contract"
    assert payload["performance_claim"] is False
    assert payload["metrics"]["task_success"] is True
    assert payload["metrics"]["transport_rate"] == 1.0
    assert payload["metrics"]["communication_count"] == 1
    assert payload["metrics"]["physical_action_count"] == 2
    assert payload["metrics"]["physical_execution_frames"] == 10
    assert payload["metrics"]["communication_execution_frames"] == 5
    assert payload["metrics"]["total_execution_frames"] == 15
    assert payload["metrics"]["communication_utility_proxy"] == 1.0
    assert payload["artifact_counts"]["public_events"] == 1
    assert [event["action_type"] for event in payload["trace"]] == [
        "send_message", "grasp", "drop"
    ]
    assert [event["information_action"] for event in payload["trace"]] == [True, False, False]


def test_smoke_cli_writes_machine_readable_report(tmp_path):
    output = tmp_path / "tdw-mat-smoke.json"

    assert main(["--output", str(output)]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["source_scenario"] == {
        "scene": "5a", "layout": "0_0", "task": "food", "seed": 2824
    }
    assert payload["metrics"]["task_success"] is True
