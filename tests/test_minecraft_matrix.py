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
    assert summary["dual_dag_runtime_enabled"] is True
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


def test_minecraft_matrix_finalizes_failed_attempt_on_unexpected_error(tmp_path):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(json.dumps([_config("first", 0)]), encoding="utf-8")

    with pytest.raises(IndexError, match="out of range"):
        run_minecraft_matrix(
            config_path=config_path,
            output_dir=tmp_path / "matrix",
            config_indices=[99],
        )

    matrix_dir = tmp_path / "matrix"
    manifest = json.loads((matrix_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert not (matrix_dir / "_COMPLETED").exists()


def test_minecraft_matrix_execute_assigns_distinct_runtime_result_paths(tmp_path, monkeypatch):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(
        json.dumps([_config("first", 0), _config("second", 1)]),
        encoding="utf-8",
    )
    def runtime_result(*args, **kwargs):
        runtime_result_path = kwargs["runtime_result_path"]
        runtime_result_path.parent.parent.joinpath("child_runtime_path.txt").write_text(
            str(runtime_result_path),
            encoding="utf-8",
        )
        return {}

    monkeypatch.setattr("benchmarks.minecraft.experiment._execute_real_runtime", runtime_result)

    run_minecraft_matrix(
        config_path=config_path,
        output_dir=tmp_path / "matrix",
        run_names=["first_run", "second_run"],
        execute=True,
    )

    expected_paths = [
        tmp_path / "matrix" / "runs" / "first_run" / ".runtime" / "runtime_result.json",
        tmp_path / "matrix" / "runs" / "second_run" / ".runtime" / "runtime_result.json",
    ]
    assert [
        path.parent.parent.joinpath("child_runtime_path.txt").read_text(encoding="utf-8")
        for path in expected_paths
    ] == [str(path) for path in expected_paths]


def test_minecraft_matrix_can_compare_task_selection_policies(tmp_path):
    base_config = _config("policy_compare", 0)
    base_config["agent_num"] = 2
    base_config["smoke_tasks"] = [
        {
            "id": "open_locked_door",
            "description": "Open locked door",
            "candidate_agents": ["Alice"],
            "assigned_agents": ["Alice"],
        },
        {"id": "find_chest", "description": "Find chest", "candidate_agents": ["Bob"]},
    ]
    base_config["smoke_action_log"] = {
        "Alice": [{
            "action": "openContainer",
            "kwargs": {"player_name": "Alice", "item_name": "door"},
            "result": {"status": False, "message": "door is locked"},
        }],
        "Bob": [{
            "action": "talkTo",
            "kwargs": {
                "player_name": "Bob",
                "entity_name": "Alice",
                "message": "The chest is north of the door.",
            },
            "result": {"status": True},
        }],
    }
    original = dict(base_config, task_name="policy_original", task_selection_policy="original")
    dual_dag = dict(base_config, task_name="policy_dual_dag", task_selection_policy="dual-dag")
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(json.dumps([original, dual_dag]), encoding="utf-8")

    summary = run_minecraft_matrix(
        config_path=config_path,
        output_dir=tmp_path / "matrix",
        run_names=["original", "dual_dag"],
    )

    assert [run["task_selection_policy"] for run in summary["runs"]] == ["original", "dual-dag"]
    original_summary = json.loads((tmp_path / "matrix" / "runs" / "original" / "summary.json").read_text(encoding="utf-8"))
    dual_dag_summary = json.loads((tmp_path / "matrix" / "runs" / "dual_dag" / "summary.json").read_text(encoding="utf-8"))
    assert original_summary["task_order"] == original_summary["ranked_task_order"]
    assert dual_dag_summary["ranked_task_order"][0]["description"] == "Find chest"


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


def test_minecraft_matrix_rejects_invalid_config_entries(tmp_path):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(json.dumps([_config("first", 0), "bad-entry"]), encoding="utf-8")

    with pytest.raises(ValueError, match="Minecraft config entry at index 1 must be an object"):
        run_minecraft_matrix(config_path=config_path, output_dir=tmp_path / "matrix")


def test_minecraft_matrix_rejects_missing_required_fields(tmp_path):
    config_path = tmp_path / "minecraft_config.json"
    config = _config("first", 0)
    del config["host"]
    config_path.write_text(json.dumps([config]), encoding="utf-8")

    with pytest.raises(ValueError, match=r"config\[0\] missing required field\(s\): host"):
        run_minecraft_matrix(config_path=config_path, output_dir=tmp_path / "matrix")


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
