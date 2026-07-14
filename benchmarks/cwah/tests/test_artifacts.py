import csv
import json

from benchmarks.cwah.artifacts import build_summary, write_normalized_artifacts


def test_build_summary_counts_actions_and_overrides():
    summary = build_summary(
        run_config={"env": "mock", "episode_id": "ep", "task_id": 0, "seed": 1},
        events=[
            {"event": "episode_started"},
            {
                "event": "policy_step",
                "decision": {"action_id": "send_message:agent_0", "message": "hello"},
                "result": {"metrics": {"communication_count": 1}},
            },
            {
                "event": "policy_step",
                "decision": {
                    "policy_override": {"reason": "prefer_physical_after_steps", "action_id": "walktowards:agent_0:20"},
                    "failed_action_recorded": {"action_id": "walktowards:agent_0:20", "error": "EXECUTION_GENERAL: Script is impossible to execute"},
                    "open_failure_recorded": {"action_id": "open:agent_0:30", "target_id": "30", "error": "AssertionError: Error: Object already open"},
                    "navigation_loop_recorded": {"action_signature": "walktowards:20:", "count": 12, "threshold": 12},
                },
                "result": {"succeeded": False, "metrics": {"communication_count": 0}},
            },
            {"event": "episode_completed"},
        ],
        metrics={"task_success": False, "normalized_progress": 0.5, "episode_steps": 2},
    )

    assert summary["event_counts"] == {"total_events": 4, "policy_steps": 2, "policy_overrides": 1}
    assert summary["action_counts"] == {"send_message": 1, "walktowards": 1}
    assert summary["diagnostics"] == {
        "policy_override_reason_counts": {"prefer_physical_after_steps": 1},
        "failed_action_record_count": 1,
        "failed_action_counts": {"walktowards": 1},
        "failure_reason_counts": {"script_impossible": 1},
        "open_failure_record_count": 1,
        "open_failure_reason_counts": {"already_open": 1},
        "navigation_loop_count": 1,
        "result_failure_count": 1,
    }


def test_write_normalized_artifacts(tmp_path):
    dual_dag_snapshot = {
        "schema_version": 1,
        "benchmark": "cwah",
        "epistemic_dag": {"nodes": [], "edges": []},
        "action_candidate_dag": {"nodes": [], "edges": []},
    }
    write_normalized_artifacts(
        artifact_dir=tmp_path,
        run_config={"env": "mock", "episode_id": "ep", "task_id": 0, "seed": 1},
        events=[
            {"event": "episode_started"},
            {"event": "policy_step", "decision": {"action_id": "walktowards:agent_0:20"}, "result": {"metrics": {}}},
            {"event": "episode_completed"},
        ],
        metrics={"task_success": True, "normalized_progress": 1.0, "episode_steps": 1},
        dual_dag_snapshot=dual_dag_snapshot,
    )

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    turns = (tmp_path / "turns.jsonl").read_text(encoding="utf-8").splitlines()
    with (tmp_path / "metrics.csv").open(encoding="utf-8", newline="") as f:
        metrics_rows = list(csv.DictReader(f))

    assert summary["benchmark"] == "cwah"
    assert summary["action_counts"] == {"walktowards": 1}
    assert len(turns) == 3
    assert metrics_rows[0]["physical_actions"] == "1"
    assert metrics_rows[0]["communication_actions"] == "0"
    assert metrics_rows[0]["policy_override_rate"] == "0.0"
    assert metrics_rows[0]["failed_action_records"] == "0"
    assert metrics_rows[0]["open_failure_records"] == "0"
    assert metrics_rows[0]["navigation_loop_count"] == "0"
    assert metrics_rows[0]["result_failures"] == "0"
    assert json.loads((tmp_path / "dual_dag_artifact.json").read_text(encoding="utf-8")) == dual_dag_snapshot
