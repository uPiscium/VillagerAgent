import csv
import json

from benchmarks.common.report import (
    aggregate_rows,
    summarize_inputs,
    write_csv_report,
    write_json_report,
)


def test_summarizes_cwah_matrix_and_writes_common_outputs(tmp_path):
    matrix_dir = tmp_path / "cwah_matrix"
    matrix_dir.mkdir()
    _write_json(
        matrix_dir / "matrix_summary.json",
        {
            "aggregate": {"runs": 2},
            "runs": [
                {
                    "run_name": "task_0_seed_1",
                    "task_id": 0,
                    "seed": 1,
                    "passed": True,
                    "metrics": {"task_success": True, "normalized_progress": 1.0, "episode_steps": 2},
                    "action_counts": {"walktowards": 2, "send_message": 1},
                    "event_counts": {"policy_steps": 2, "policy_overrides": 1},
                    "diagnostics": {
                        "failed_action_record_count": 1,
                        "open_failure_record_count": 1,
                        "navigation_loop_count": 1,
                        "result_failure_count": 1,
                        "failed_action_counts": {"walktowards": 1},
                        "failure_reason_counts": {"script_impossible": 1},
                        "open_failure_reason_counts": {"already_open": 1},
                        "policy_override_reason_counts": {"prefer_physical_after_steps": 1},
                    },
                },
                {
                    "run_name": "task_1_seed_1",
                    "task_id": 1,
                    "seed": 1,
                    "passed": False,
                    "metrics": {"task_success": False, "normalized_progress": 0.25, "episode_steps": 4},
                    "action_counts": {"open": 1},
                },
            ],
        },
    )

    rows = summarize_inputs([matrix_dir])

    assert [row["run_name"] for row in rows] == ["task_0_seed_1", "task_1_seed_1"]
    assert rows[0]["benchmark"] == "cwah"
    assert rows[0]["success_rate"] == 1.0
    assert rows[0]["physical_action_count"] == 2
    assert rows[0]["communication_action_count"] == 1
    assert rows[1]["status"] == "failed"
    assert rows[1]["failed_runs"] == 1
    aggregate = aggregate_rows(rows)
    assert aggregate["success_rate"] == 0.5
    assert aggregate["physical_action_count"] == 3
    assert aggregate["communication_action_count"] == 1
    assert aggregate["action_counts"] == {"open": 1, "send_message": 1, "walktowards": 2}
    assert aggregate["policy_override_count"] == 1
    assert aggregate["failed_action_record_count"] == 1
    assert aggregate["open_failure_record_count"] == 1
    assert aggregate["navigation_loop_count"] == 1
    assert aggregate["result_failure_count"] == 1
    assert aggregate["failed_action_counts"] == {"walktowards": 1}
    assert aggregate["failure_reason_counts"] == {"script_impossible": 1}
    assert aggregate["open_failure_reason_counts"] == {"already_open": 1}
    assert aggregate["policy_override_reason_counts"] == {"prefer_physical_after_steps": 1}

    csv_path = tmp_path / "common.csv"
    json_path = tmp_path / "common.json"
    write_csv_report(rows, csv_path)
    write_json_report(rows, json_path)

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        csv_rows = list(csv.DictReader(f))
    assert csv_rows[0]["benchmark"] == "cwah"
    assert csv_rows[0]["action_counts"] == '{"send_message": 1, "walktowards": 2}'
    assert csv_rows[0]["policy_override_count"] == "1"
    assert csv_rows[0]["failed_action_counts"] == '{"walktowards": 1}'
    assert csv_rows[0]["failure_reason_counts"] == '{"script_impossible": 1}'
    assert csv_rows[0]["open_failure_reason_counts"] == '{"already_open": 1}'
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["aggregate"]["runs"] == 2
    assert payload["aggregate"]["failed_runs"] == 1
    assert payload["aggregate"]["action_counts"] == {"open": 1, "send_message": 1, "walktowards": 2}


def test_summarizes_cwah_normalized_summary(tmp_path):
    normalized = tmp_path / "task_0_seed_0" / "normalized"
    normalized.mkdir(parents=True)
    _write_json(
        normalized / "summary.json",
        {
            "benchmark": "cwah",
            "run_config": {"episode_id": "cwah-task_0_seed_0", "task_id": 0, "seed": 0},
            "metrics": {"task_success": False, "normalized_progress": 0.5, "episode_steps": 3},
            "action_counts": {"walktowards": 2, "wait": 1},
        },
    )

    rows = summarize_inputs([normalized])

    assert rows == [
        {
            "benchmark": "cwah",
            "run_name": "cwah-task_0_seed_0",
            "status": "completed",
            "task_id": 0,
            "seed": 0,
            "episodes": 1,
            "successes": 0,
            "success_rate": 0.0,
            "mean_progress": 0.5,
            "mean_steps": 3.0,
            "failed_runs": 0,
            "physical_action_count": 2,
            "communication_action_count": 0,
            "action_counts": '{"wait": 1, "walktowards": 2}',
            "policy_override_count": 0,
            "policy_override_rate": 0.0,
            "failed_action_record_count": 0,
            "open_failure_record_count": 0,
            "navigation_loop_count": 0,
            "result_failure_count": 0,
            "failed_action_counts": "{}",
            "failure_reason_counts": "{}",
            "open_failure_reason_counts": "{}",
            "policy_override_reason_counts": "{}",
            "error_type": "",
            "error_message": "",
        }
    ]


def test_summarizes_existing_craft_run_without_changing_craft_schema(tmp_path):
    run_dir = tmp_path / "craft_run"
    normalized = run_dir / "normalized"
    normalized.mkdir(parents=True)
    _write_json(
        normalized / "summary.json",
        {
            "run_name": "craft_run",
            "status": "completed",
            "condition": "official_baseline",
            "num_games": 2,
            "turns": 5,
            "mean_final_progress": 0.75,
            "completion_rate": 0.5,
            "structures": ["house"],
        },
    )
    with (normalized / "metrics.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["leakage_passed", "physical_action_count", "place_action_count", "clarify_count"])
        writer.writeheader()
        writer.writerow({"leakage_passed": "True", "physical_action_count": "3", "place_action_count": "2", "clarify_count": "1"})

    rows = summarize_inputs([run_dir])

    assert rows[0]["benchmark"] == "craft"
    assert rows[0]["run_name"] == "craft_run"
    assert rows[0]["episodes"] == 2
    assert rows[0]["successes"] == 1
    assert rows[0]["success_rate"] == 0.5
    assert rows[0]["mean_progress"] == 0.75
    assert rows[0]["physical_action_count"] == 3
    assert rows[0]["communication_action_count"] == 1
    assert rows[0]["failed_action_counts"] == "{}"


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
