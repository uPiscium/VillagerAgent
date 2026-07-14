import json
import time

import pytest

from benchmarks.minecraft.experiment import MinecraftExecuteTimeoutError, run_minecraft_experiment
from benchmarks.minecraft.metrics import build_minecraft_metrics
from benchmarks.common.report import summarize_inputs


def test_minecraft_experiment_dry_run_writes_expected_artifacts(tmp_path):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(
        json.dumps({
            "task_type": "meta",
            "task_idx": 0,
            "agent_num": 1,
            "task_goal": "Find the village bell",
            "host": "127.0.0.1",
            "port": 25565,
            "task_name": "issue110_dry_run",
            "api_key": "secret",
        }),
        encoding="utf-8",
    )

    summary = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="issue110",
        enable_dual_dag_task_selection=True,
    )

    output_dir = tmp_path / "result" / "issue110"
    assert summary["mode"] == "dry_run"
    assert summary["output_dir"] == str(output_dir)
    assert summary["dual_dag_runtime_enabled"] is True
    assert summary["dual_dag_task_selection_enabled"] is True
    assert summary["source_of_truth"] == "dual_dag"
    assert summary["mutates_runtime"] is False
    assert summary["artifact_summary"]["task_node_count"] == 1
    assert summary["recommended_task_id"].startswith("minecraft:task:")
    assert (output_dir / "action_log.json").exists()
    assert (output_dir / "task_graph_snapshot.json").exists()
    assert (output_dir / "runtime_dual_dag_snapshot.json").exists()
    assert (output_dir / "dual_dag_artifact.json").exists()
    assert (output_dir / "decision_support.json").exists()
    assert (output_dir / "metrics.json").exists()
    assert (output_dir / "summary.json").exists()
    assert (output_dir / "command.txt").exists()
    assert (output_dir / "config.resolved.json").exists()
    assert (output_dir / "provenance.json").exists()

    launch_config = json.loads((output_dir / "launch_config.json").read_text(encoding="utf-8"))
    assert "api_key" not in launch_config
    provenance = json.loads((output_dir / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["benchmark"] == "minecraft"
    assert provenance["schema_version"] == "1.0.0"
    runtime_snapshot = json.loads((output_dir / "runtime_dual_dag_snapshot.json").read_text(encoding="utf-8"))
    assert runtime_snapshot["source_of_truth"] == "dual_dag"
    assert runtime_snapshot["nodes"][0]["node_type"] == "runtime_task"


def test_minecraft_experiment_sanitizes_run_names(tmp_path):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(
        json.dumps({
            "task_type": "meta",
            "task_idx": 0,
            "agent_num": 1,
            "task_goal": "Find the village bell",
            "host": "127.0.0.1",
            "port": 25565,
            "task_name": "ignored",
        }),
        encoding="utf-8",
    )

    summary = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="minecraft enabled local seed0 issue/109 smoke",
        command_text="python -m benchmarks.minecraft.experiment --config minecraft_config.json",
    )

    assert summary["run_name"] == "minecraft_enabled_local_seed0_issue_109_smoke"
    assert (tmp_path / "result" / summary["run_name"] / "command.txt").exists()


def test_minecraft_experiment_accepts_config_lists(tmp_path):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(
        json.dumps([
            {
                "task_type": "meta",
                "task_idx": 0,
                "agent_num": 1,
                "task_goal": "First task",
                "host": "127.0.0.1",
                "port": 25565,
                "task_name": "first",
            },
            {
                "task_type": "meta",
                "task_idx": 1,
                "agent_num": 2,
                "task_goal": "Second task",
                "host": "127.0.0.1",
                "port": 25565,
                "task_name": "second",
            },
        ]),
        encoding="utf-8",
    )

    summary = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        config_index=1,
    )

    assert summary["run_name"] == "second"
    assert summary["task_idx"] == 1
    graph_snapshot = json.loads(
        (tmp_path / "result" / "second" / "task_graph_snapshot.json").read_text(encoding="utf-8")
    )
    assert graph_snapshot["mutates_runtime"] is False
    assert graph_snapshot["tasks"][0]["description"] == "Second task"


def test_minecraft_experiment_rejects_missing_required_fields(tmp_path):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(json.dumps({"task_type": "meta"}), encoding="utf-8")

    with pytest.raises(ValueError, match=r"missing required field\(s\): task_idx, agent_num, task_goal, host, port, task_name"):
        run_minecraft_experiment(config_path=config_path, output_root=tmp_path / "result")


def test_minecraft_experiment_rejects_config_index_out_of_range(tmp_path):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(json.dumps([_minecraft_config("first")]), encoding="utf-8")

    with pytest.raises(ValueError, match="Minecraft config index out of range: 1"):
        run_minecraft_experiment(config_path=config_path, output_root=tmp_path / "result", config_index=1)


def test_minecraft_experiment_rejects_negative_agent_count(tmp_path):
    config_path = tmp_path / "minecraft_config.json"
    config = _minecraft_config("bad_agents")
    config["agent_num"] = -1
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ValueError, match="config.agent_num must be non-negative"):
        run_minecraft_experiment(config_path=config_path, output_root=tmp_path / "result")


def test_minecraft_experiment_rejects_invalid_smoke_fixture_shape(tmp_path):
    config_path = tmp_path / "minecraft_config.json"
    config = _minecraft_config("bad_smoke")
    config["smoke_tasks"] = [{"id": "missing_description"}]
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ValueError, match=r"config.smoke_tasks\[0\] missing required field: description"):
        run_minecraft_experiment(config_path=config_path, output_root=tmp_path / "result")


def test_minecraft_experiment_records_always_on_task_reordering(tmp_path):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(
        json.dumps({
            "task_type": "meta",
            "task_idx": 0,
            "agent_num": 2,
            "task_goal": "Smoke compare task selection",
            "host": "127.0.0.1",
            "port": 25565,
            "task_name": "issue107",
            "smoke_tasks": [
                {
                    "id": "open_locked_door",
                    "description": "Open locked door",
                    "candidate_agents": ["Alice"],
                    "assigned_agents": ["Alice"],
                },
                {
                    "id": "find_chest",
                    "description": "Find chest",
                    "candidate_agents": ["Bob"],
                },
            ],
            "smoke_action_log": {
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
            },
        }),
        encoding="utf-8",
    )

    disabled = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="disabled",
    )
    enabled = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="enabled",
        enable_dual_dag_task_selection=True,
    )

    assert disabled["dual_dag_runtime_enabled"] is True
    assert disabled["dual_dag_task_selection_enabled"] is True
    assert disabled["source_of_truth"] == "dual_dag"
    assert disabled["ranked_task_order"][0]["description"] == "Find chest"
    assert enabled["ranked_task_order"][0]["description"] == "Find chest"
    assert disabled["task_order"] != disabled["ranked_task_order"]
    assert enabled["task_order"] != enabled["ranked_task_order"]
    assert disabled["ranked_task_order"] == enabled["ranked_task_order"]
    assert enabled["recommended_description"] == "Find chest"


def test_minecraft_metrics_extracts_representative_counts_without_secrets():
    metrics = build_minecraft_metrics(
        summary={
            "run_name": "metrics",
            "mode": "dry_run",
            "recommended_task_id": "minecraft:task:find_chest",
            "selected_task_id": "minecraft:task:find_chest",
            "progress": 0.5,
            "api_key": "secret",
        },
        action_log={
            "Alice": [{
                "action": "openContainer",
                "duration": 2.0,
                "kwargs": {"api_key": "secret"},
                "result": {"status": False, "message": "retry after locked door"},
            }],
            "Bob": [{
                "action": "talkTo",
                "duration": 3.0,
                "result": {"status": True},
            }],
        },
        task_graph_snapshot={
            "tasks": [
                {"description": "Open locked door", "status": "failure"},
                {"description": "Find chest", "status": "success"},
            ]
        },
        decision_support={"mutates_runtime": False},
    )

    assert metrics["schema_version"] == "1.0.0"
    assert metrics["task_completion_rate"] == 0.5
    assert metrics["action_count"] == 2
    assert metrics["failed_action_count"] == 1
    assert metrics["retry_replan_count"] == 1
    assert metrics["time_to_completion"] == 5.0
    assert metrics["recommendation_adopted_count"] == 1
    assert "api_key" not in json.dumps(metrics)


def test_minecraft_execute_preserves_artifacts_on_runtime_error(tmp_path, monkeypatch):
    config_path = _write_minecraft_config(tmp_path)

    def fail_runtime(*args, **kwargs):
        raise RuntimeError("server unavailable")

    monkeypatch.setattr("benchmarks.minecraft.experiment._execute_real_runtime", fail_runtime)

    summary = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="execute_error",
        execute=True,
        execute_timeout_seconds=30,
    )

    output_dir = tmp_path / "result" / "execute_error"
    assert summary["mode"] == "execute"
    assert summary["execute_real_environment"] is True
    assert summary["execute_timeout_seconds"] == 30
    assert summary["error"] == "server unavailable"
    assert summary["error_type"] == "RuntimeError"
    assert summary["timed_out"] is False
    assert (output_dir / "summary.json").exists()
    assert (output_dir / "metrics.json").exists()
    metrics = json.loads((output_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["error"] == "server unavailable"


def test_minecraft_execute_timeout_preserves_artifacts(tmp_path, monkeypatch):
    config_path = _write_minecraft_config(tmp_path)

    def slow_runtime(*args, **kwargs):
        time.sleep(1)

    monkeypatch.setattr("benchmarks.minecraft.experiment._execute_real_runtime", slow_runtime)

    summary = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="execute_timeout",
        execute=True,
        execute_timeout_seconds=0.01,
    )

    output_dir = tmp_path / "result" / "execute_timeout"
    assert summary["error_type"] == "timeout"
    assert summary["timed_out"] is True
    assert "timed out after 0.01 seconds" in summary["error"]
    assert (output_dir / "action_log.json").exists()
    persisted = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert persisted["timed_out"] is True
    common_rows = summarize_inputs([output_dir])
    assert common_rows[0]["error_type"] == "timeout"


def test_minecraft_execute_timeout_survives_contextmanager_generator_error(tmp_path, monkeypatch):
    config_path = _write_minecraft_config(tmp_path)

    def contextmanager_swallowed_timeout(*args, **kwargs):
        try:
            time.sleep(1)
        except MinecraftExecuteTimeoutError as exc:
            raise RuntimeError("generator didn't yield") from exc

    monkeypatch.setattr(
        "benchmarks.minecraft.experiment._execute_real_runtime",
        contextmanager_swallowed_timeout,
    )

    summary = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="execute_contextmanager_timeout",
        execute=True,
        execute_timeout_seconds=0.01,
    )

    assert summary["error_type"] == "timeout"
    assert summary["timed_out"] is True
    assert "timed out after 0.01 seconds" in summary["error"]


def _write_minecraft_config(tmp_path):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(
        json.dumps({
            "task_type": "meta",
            "task_idx": 0,
            "agent_num": 1,
            "task_goal": "Find the village bell",
            "host": "127.0.0.1",
            "port": 25565,
            "task_name": "bounded_execute",
        }),
        encoding="utf-8",
    )
    return config_path


def _minecraft_config(task_name):
    return {
        "task_type": "meta",
        "task_idx": 0,
        "agent_num": 1,
        "task_goal": task_name,
        "host": "127.0.0.1",
        "port": 25565,
        "task_name": task_name,
    }
