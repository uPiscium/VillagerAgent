import json

import pytest

from benchmarks.common.report import summarize_inputs
from benchmarks.minecraft.matrix import run_minecraft_matrix


def test_minecraft_matrix_dry_run_writes_runs_and_common_summary(tmp_path):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(
        json.dumps([
            {
                "task_type": "meta",
                "task_idx": 0,
                "agent_num": 1,
                "task_goal": "Find the village bell",
                "host": "127.0.0.1",
                "port": 25565,
                "task_name": "bell",
                "smoke_action_log": {
                    "Alice": [{"action": "navigateTo", "result": {"status": True}}]
                },
            },
            {
                "task_type": "meta",
                "task_idx": 1,
                "agent_num": 2,
                "task_goal": "Find chest",
                "host": "127.0.0.1",
                "port": 25565,
                "task_name": "chest",
                "smoke_action_log": {
                    "Bob": [{"action": "talkTo", "result": {"status": True}}]
                },
            },
        ]),
        encoding="utf-8",
    )

    summary = run_minecraft_matrix(
        config_path=config_path,
        output_dir=tmp_path / "matrix",
        run_names=["bell_run", "chest_run"],
        enable_dual_dag_task_selection=True,
        execute_timeout_seconds=600,
    )

    matrix_dir = tmp_path / "matrix"
    assert summary["benchmark"] == "minecraft"
    assert summary["mode"] == "dry_run"
    assert summary["run_count"] == 2
    assert summary["aggregate"]["runs"] == 2
    assert summary["aggregate"]["failed_runs"] == 0
    assert summary["dual_dag_task_selection_enabled"] is True
    assert summary["execute_timeout_seconds"] == 600
    assert (matrix_dir / "matrix_summary.json").exists()
    assert (matrix_dir / "runs" / "bell_run" / "summary.json").exists()
    assert (matrix_dir / "runs" / "chest_run" / "metrics.json").exists()
    assert summary["runs"][0]["common_report"]["benchmark"] == "minecraft"
    assert summary["runs"][0]["execute_timeout_seconds"] == 600
    assert summary["runs"][0]["common_report"]["physical_action_count"] == 1
    assert summary["runs"][1]["common_report"]["communication_action_count"] == 1

    persisted = json.loads((matrix_dir / "matrix_summary.json").read_text(encoding="utf-8"))
    assert persisted["runs"][0]["run_name"] == "bell_run"
    common_rows = summarize_inputs([matrix_dir])
    assert [row["run_name"] for row in common_rows] == ["bell_run", "chest_run"]
    assert [row["benchmark"] for row in common_rows] == ["minecraft", "minecraft"]


def test_minecraft_matrix_accepts_selected_indices_and_default_run_names(tmp_path):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(
        json.dumps([
            _config("first", 0),
            _config("second task", 1),
        ]),
        encoding="utf-8",
    )

    summary = run_minecraft_matrix(
        config_path=config_path,
        output_dir=tmp_path / "matrix",
        config_indices=[1],
    )

    assert summary["run_count"] == 1
    assert summary["runs"][0]["config_index"] == 1
    assert summary["runs"][0]["run_name"] == "config_1_second_task"
    assert (tmp_path / "matrix" / "runs" / "config_1_second_task" / "summary.json").exists()


def test_minecraft_matrix_rejects_mismatched_run_names(tmp_path):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(json.dumps([_config("first", 0), _config("second", 1)]), encoding="utf-8")

    with pytest.raises(ValueError, match="run_names length"):
        run_minecraft_matrix(
            config_path=config_path,
            output_dir=tmp_path / "matrix",
            config_indices=[0, 1],
            run_names=["only_one"],
        )


def _config(task_name, task_idx):
    return {
        "task_type": "meta",
        "task_idx": task_idx,
        "agent_num": 1,
        "task_goal": task_name,
        "host": "127.0.0.1",
        "port": 25565,
        "task_name": task_name,
    }
