import csv
import json

from benchmarks.common.report import (
    aggregate_rows,
    summarize_inputs,
    write_csv_report,
    write_json_report,
)
from benchmarks.minecraft.experiment import run_minecraft_experiment


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
    assert payload["schema_version"] == 2
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
            "attempt_id": "",
            "status": "completed",
            "task_id": 0,
            "seed": 0,
            "evaluation_unit": "episode",
            "episodes": 1,
            "successes": 0,
            "success_rate": 0.0,
            "task_count": None,
            "completed_task_count": None,
            "task_completion_rate": None,
            "mean_progress": 0.5,
            "progress_available": True,
            "mean_steps": 3.0,
            "steps_available": True,
            "failed_runs": 0,
            "action_log_available": True,
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
    (normalized / "turns.jsonl").write_text(
        '{"turn": 1}\n{"turn": 2}\n',
        encoding="utf-8",
    )

    rows = summarize_inputs([run_dir])

    assert rows[0]["benchmark"] == "craft"
    assert rows[0]["run_name"] == "craft_run"
    assert rows[0]["episodes"] == 2
    assert rows[0]["successes"] == 1
    assert rows[0]["success_rate"] == 0.5
    assert rows[0]["mean_progress"] == 0.75
    assert rows[0]["mean_steps"] == 1.0
    assert rows[0]["physical_action_count"] == 3
    assert rows[0]["communication_action_count"] == 1
    assert rows[0]["failed_action_counts"] == "{}"


def test_summarizes_minecraft_dry_run_artifacts(tmp_path):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(
        json.dumps({
            "task_type": "meta",
            "task_idx": 3,
            "agent_num": 2,
            "task_goal": "Find the village bell",
            "host": "127.0.0.1",
            "port": 25565,
            "task_name": "minecraft_common_report",
            "smoke_tasks": [
                {
                    "id": "find_bell",
                    "description": "Find the village bell",
                    "assigned_agents": ["Alice"],
                }
            ],
            "smoke_action_log": {
                "Alice": [
                    {
                        "action": "navigateTo",
                        "duration": 1.5,
                        "result": {"status": False, "message": "path blocked"},
                    }
                ],
                "Bob": [
                    {
                        "action": "talkTo",
                        "duration": 0.5,
                        "result": {"status": True},
                    }
                ],
            },
        }),
        encoding="utf-8",
    )
    run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="minecraft_report",
    )

    rows = summarize_inputs([tmp_path / "result" / "minecraft_report"])

    assert rows == [
        {
            "benchmark": "minecraft",
            "run_name": "minecraft_report",
            "attempt_id": rows[0]["attempt_id"],
            "status": "completed",
            "task_id": 3,
            "seed": "",
            "evaluation_unit": "run",
            "episodes": 1,
            "successes": 0,
            "success_rate": 0.0,
            "task_count": 1,
            "completed_task_count": 0,
            "task_completion_rate": 0.0,
            "mean_progress": None,
            "progress_available": False,
            "mean_steps": 2.0,
            "steps_available": True,
            "failed_runs": 0,
            "action_log_available": True,
            "physical_action_count": 1,
            "communication_action_count": 1,
            "action_counts": '{"navigateTo": 1, "talkTo": 1}',
            "policy_override_count": 0,
            "policy_override_rate": 0.0,
            "failed_action_record_count": 1,
            "open_failure_record_count": 0,
            "navigation_loop_count": 0,
            "result_failure_count": 1,
            "failed_action_counts": '{"navigateTo": 1}',
            "failure_reason_counts": "{}",
            "open_failure_reason_counts": "{}",
            "policy_override_reason_counts": "{}",
            "error_type": "",
            "error_message": "",
        }
    ]
    assert rows[0]["attempt_id"]


def test_summarizes_minecraft_summary_file_without_craft_fallback(tmp_path):
    run_minecraft_experiment(
        config_path=_minecraft_config(tmp_path),
        output_root=tmp_path / "result",
        run_name="minecraft_summary_file",
    )

    rows = summarize_inputs([tmp_path / "result" / "minecraft_summary_file" / "summary.json"])

    assert rows[0]["benchmark"] == "minecraft"
    assert rows[0]["run_name"] == "minecraft_summary_file"
    assert rows[0]["action_log_available"] is False
    assert rows[0]["mean_steps"] is None
    assert rows[0]["physical_action_count"] is None


def test_minecraft_run_success_is_separate_from_task_completion(tmp_path):
    run_dir = tmp_path / "minecraft_multi_task"
    run_dir.mkdir()
    _write_json(
        run_dir / "summary.json",
        {
            "run_name": "minecraft_multi_task",
            "mode": "execute",
            "artifact_summary": {},
        },
    )
    _write_json(
        run_dir / "metrics.json",
        {
            "task_count": 3,
            "completed_task_count": 2,
            "task_completion_rate": 2 / 3,
            "action_count": 0,
            "progress": 0.5,
        },
    )
    _write_json(run_dir / "action_log.json", {})

    row = summarize_inputs([run_dir])[0]

    assert row["episodes"] == 1
    assert row["successes"] == 0
    assert row["success_rate"] == 0.0
    assert row["task_count"] == 3
    assert row["completed_task_count"] == 2
    assert row["task_completion_rate"] == 2 / 3


def test_missing_minecraft_metrics_remain_unavailable(tmp_path):
    run_dir = tmp_path / "minecraft_missing_metrics"
    run_dir.mkdir()
    _write_json(
        run_dir / "summary.json",
        {
            "run_name": "minecraft_missing_metrics",
            "mode": "execute",
            "artifact_summary": {},
        },
    )
    _write_json(
        run_dir / "metrics.json",
        {
            "task_count": 0,
            "completed_task_count": 0,
            "task_completion_rate": None,
            "action_count": None,
            "progress": None,
        },
    )

    row = summarize_inputs([run_dir])[0]

    assert row["successes"] is None
    assert row["success_rate"] is None
    assert row["mean_progress"] is None
    assert row["progress_available"] is False
    assert row["mean_steps"] is None
    assert row["steps_available"] is False
    assert row["action_log_available"] is False
    assert row["physical_action_count"] is None
    assert row["action_counts"] is None


def test_aggregate_weights_means_by_evaluation_units():
    rows = [
        {
            "benchmark": "craft",
            "episodes": 1,
            "successes": 1,
            "mean_progress": 1.0,
            "mean_steps": 10.0,
            "failed_runs": 0,
        },
        {
            "benchmark": "craft",
            "episodes": 3,
            "successes": 0,
            "mean_progress": 0.0,
            "mean_steps": 20.0,
            "failed_runs": 0,
        },
    ]

    aggregate = aggregate_rows(rows)

    assert aggregate["success_rate"] == 0.25
    assert aggregate["mean_progress"] == 0.25
    assert aggregate["progress_available_episodes"] == 4
    assert aggregate["mean_steps"] == 17.5
    assert aggregate["steps_available_episodes"] == 4


def test_mixed_benchmark_aggregate_keeps_metrics_separate():
    rows = [
        {"benchmark": "cwah", "episodes": 1, "successes": 1, "mean_progress": 1.0, "failed_runs": 0},
        {"benchmark": "craft", "episodes": 2, "successes": 0, "mean_progress": 0.5, "failed_runs": 0},
    ]

    aggregate = aggregate_rows(rows)

    assert aggregate["episodes"] is None
    assert aggregate["success_rate"] is None
    assert aggregate["mean_progress"] is None
    assert aggregate["by_benchmark"]["cwah"]["success_rate"] == 1.0
    assert aggregate["by_benchmark"]["craft"]["success_rate"] == 0.0


def test_legacy_minecraft_matrix_row_is_upgraded_to_run_semantics(tmp_path):
    matrix_dir = tmp_path / "legacy_matrix"
    matrix_dir.mkdir()
    _write_json(
        matrix_dir / "matrix_summary.json",
        {
            "benchmark": "minecraft",
            "runs": [
                {
                    "metrics": {
                        "task_count": 2,
                        "completed_task_count": 2,
                        "task_completion_rate": 1.0,
                        "progress": None,
                        "action_count": 3,
                    },
                    "common_report": {
                        "benchmark": "minecraft",
                        "run_name": "legacy",
                        "episodes": 1,
                        "successes": 2,
                        "success_rate": 1.0,
                        "mean_progress": 0.0,
                        "mean_steps": 3.0,
                        "action_counts": '{"navigateTo": 3}',
                    },
                }
            ],
        },
    )

    row = summarize_inputs([matrix_dir])[0]

    assert row["evaluation_unit"] == "run"
    assert row["episodes"] == 1
    assert row["successes"] == 1
    assert row["task_count"] == 2
    assert row["completed_task_count"] == 2
    assert row["mean_progress"] is None


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def _minecraft_config(tmp_path):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(
        json.dumps({
            "task_type": "meta",
            "task_idx": 0,
            "agent_num": 1,
            "task_goal": "Find the village bell",
            "host": "127.0.0.1",
            "port": 25565,
            "task_name": "minecraft_summary_file",
        }),
        encoding="utf-8",
    )
    return config_path
