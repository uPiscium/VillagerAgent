import json
import time
from pathlib import Path

import pytest

from benchmarks.common.run_artifacts import (
    COMPLETION_MARKER_FILE,
    RunDirectoryExistsError,
)
from benchmarks.minecraft.experiment import (
    MinecraftExecuteTimeoutError,
    _terminate_runtime_process,
    run_minecraft_experiment,
    task_graph_from_runtime_task_dag_snapshot,
)
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
    assert summary["runtime_task_store"] == "runtime_task_dag"
    assert summary["source_of_truth"] == "runtime_task_dag"
    assert summary["task_state_source"] == "config_fixture"
    assert summary["runtime_selection_policy"] == "dual-dag"
    assert summary["runtime_selected_task_ids"] == []
    assert summary["posthoc_ranked_task_order"] == summary["ranked_task_order"]
    assert summary["mutates_environment"] is False
    assert summary["artifact_generation_mutates_runtime"] is False
    assert summary["task_selection_mutates_order"] is True
    assert summary["task_order_changed"] is False
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
    artifact = json.loads((output_dir / "dual_dag_artifact.json").read_text(encoding="utf-8"))
    decision_support = json.loads((output_dir / "decision_support.json").read_text(encoding="utf-8"))
    assert runtime_snapshot["source_of_truth"] == "runtime_task_dag"
    assert runtime_snapshot["snapshot_source"] == "config_fixture"
    assert runtime_snapshot["nodes"][0]["node_type"] == "runtime_task"
    assert artifact["task_state_source"] == "config_fixture"
    assert decision_support["task_state_source"] == "config_fixture"


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


def test_minecraft_experiment_records_task_selection_policy_ablation(tmp_path):
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
        run_name="original",
        task_selection_policy="original",
    )
    enabled = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="dual_dag",
        task_selection_policy="dual-dag",
    )

    assert disabled["dual_dag_runtime_enabled"] is True
    assert disabled["dual_dag_task_selection_enabled"] is False
    assert disabled["task_selection_policy"] == "original"
    assert disabled["source_of_truth"] == "runtime_task_dag"
    assert disabled["ranked_task_order"][0]["description"] == "Open locked door"
    assert enabled["dual_dag_task_selection_enabled"] is True
    assert enabled["task_selection_policy"] == "dual-dag"
    assert enabled["ranked_task_order"][0]["description"] == "Find chest"
    assert disabled["task_order"] == disabled["ranked_task_order"]
    assert enabled["task_order"] != enabled["ranked_task_order"]
    assert disabled["task_selection_mutates_order"] is False
    assert disabled["task_order_changed"] is False
    assert enabled["task_selection_mutates_order"] is True
    assert enabled["task_order_changed"] is True
    assert enabled["recommended_description"] == "Find chest"


def test_minecraft_experiment_rejects_unknown_task_selection_policy(tmp_path):
    config_path = _write_minecraft_config(tmp_path)

    with pytest.raises(ValueError, match="Unsupported task_selection_policy"):
        run_minecraft_experiment(
            config_path=config_path,
            output_root=tmp_path / "result",
            task_selection_policy="bad-policy",
        )


def test_minecraft_metrics_extracts_representative_counts_without_secrets():
    metrics = build_minecraft_metrics(
        summary={
            "run_name": "metrics",
            "mode": "dry_run",
            "recommended_task_id": "minecraft:task:find_chest",
            "selected_task_id": "minecraft:task:find_chest",
            "progress": 0.5,
            "mutates_environment": False,
            "artifact_generation_mutates_runtime": False,
            "task_selection_mutates_order": True,
            "task_order_changed": True,
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
    assert metrics["mutates_environment"] is False
    assert metrics["artifact_generation_mutates_runtime"] is False
    assert metrics["task_selection_mutates_order"] is True
    assert metrics["task_order_changed"] is True
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
    assert summary["mutates_environment"] is True
    assert summary["artifact_generation_mutates_runtime"] is False
    assert summary["execute_timeout_seconds"] == 30
    assert summary["error"] == "server unavailable"
    assert summary["error_type"] == "RuntimeError"
    assert summary["timed_out"] is False
    assert summary["runtime_process_isolated"] is True
    assert summary["runtime_process_exit_code"] == 0
    assert (output_dir / "summary.json").exists()
    assert (output_dir / "metrics.json").exists()
    metrics = json.loads((output_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["error"] == "server unavailable"
    assert metrics["mutates_environment"] is True


def test_minecraft_execute_uses_real_runtime_snapshot(tmp_path, monkeypatch):
    config_path = _write_minecraft_config(tmp_path)

    def runtime_result(*args, **kwargs):
        return _runtime_result_snapshot(status="success")

    monkeypatch.setattr("benchmarks.minecraft.experiment._execute_real_runtime", runtime_result)

    summary = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="execute_real_snapshot",
        execute=True,
    )

    output_dir = tmp_path / "result" / "execute_real_snapshot"
    runtime_snapshot = json.loads((output_dir / "runtime_dual_dag_snapshot.json").read_text(encoding="utf-8"))
    assert summary["snapshot_source"] == "real_runtime"
    assert runtime_snapshot["snapshot_source"] == "real_runtime"
    assert runtime_snapshot["nodes"][0]["lifecycle"]["status"] == "success"
    assert summary["runtime_process_isolated"] is True
    assert summary["runtime_process_exit_code"] == 0
    assert summary["runtime_process_terminated"] is False


def test_runtime_task_snapshot_adapter_restores_tasks_edges_and_lifecycle_metadata():
    snapshot = _runtime_result_snapshot(status="running")["runtime_task_dag_snapshot"]
    snapshot["nodes"].append({
        "node_id": "runtime:task:second",
        "node_type": "runtime_task",
        "content": {
            "description": "Second runtime task",
            "metadata": {"kind": "follow_up"},
            "milestones": ["done"],
            "reflect": {"ok": True},
        },
        "lifecycle": {
            "status": "unknown",
            "candidate_agents": ["Bob"],
            "active_agents": [],
            "last_assigned_agents": [],
            "required_agent_count": 1,
        },
        "derived": {"dependency_ready": False, "blocked_by_tasks": ["runtime:task:mock"]},
        "provenance": {"source": "test_replan"},
    })
    snapshot["edges"] = [{
        "source_id": "runtime:task:mock",
        "target_id": "runtime:task:second",
        "edge_type": "precedes_task",
        "metadata": {},
    }]

    tasks, graph = task_graph_from_runtime_task_dag_snapshot(snapshot)

    assert [(task.id, task.description, task.status) for task in tasks] == [
        ("mock", "Runtime task", "running"),
        ("second", "Second runtime task", "unknown"),
    ]
    assert tasks[0].candidate_list == ["Alice"]
    assert tasks[0]._agent == ["Alice"]
    assert tasks[0].number == 1
    assert tasks[0].content["runtime_snapshot"]["last_assigned_agents"] == ["Alice"]
    assert tasks[1].content["runtime_snapshot"]["provenance"] == {"source": "test_replan"}
    assert [(start.id, end.id) for start, end in graph.edge] == [("mock", "second")]


def test_minecraft_execute_builds_task_artifacts_from_real_runtime_state(tmp_path, monkeypatch):
    config_path = _write_minecraft_config(tmp_path)

    monkeypatch.setattr(
        "benchmarks.minecraft.experiment._execute_real_runtime",
        lambda *args, **kwargs: _runtime_result_snapshot(status="success"),
    )

    summary = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="runtime_artifact_source",
        execute=True,
    )

    output_dir = tmp_path / "result" / "runtime_artifact_source"
    artifact = json.loads((output_dir / "dual_dag_artifact.json").read_text(encoding="utf-8"))
    decision_support = json.loads((output_dir / "decision_support.json").read_text(encoding="utf-8"))
    task_nodes = [node for node in artifact["nodes"] if node["node_type"] == "minecraft_task"]
    assert artifact["task_state_source"] == "real_runtime"
    assert [node["content"]["description"] for node in task_nodes] == ["Runtime task"]
    assert task_nodes[0]["content"]["status"] == "success"
    assert task_nodes[0]["content"]["last_assigned_agents"] == ["Alice"]
    assert task_nodes[0]["content"]["metadata"]["runtime_snapshot"]["last_assigned_agents"] == ["Alice"]
    assert decision_support["task_state_source"] == "real_runtime"
    assert [candidate["description"] for candidate in decision_support["candidates"]] == ["Runtime task"]
    assert summary["task_state_source"] == "real_runtime"
    assert summary["task_order"][0]["description"] == "Runtime task"
    assert summary["runtime_selection_policy"] == "dual-dag"
    assert summary["runtime_selected_task_ids"] == []
    assert summary["posthoc_ranked_task_order"][0]["description"] == "Runtime task"
    assert summary["selected_task_id"] == ""
    assert any(edge["edge_type"] == "task_invokes_action" for edge in artifact["edges"])
    metrics = json.loads((output_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["task_completion_rate"] == 1.0


def test_minecraft_execute_uses_recorded_runtime_selection_history(tmp_path, monkeypatch):
    config_path = _write_minecraft_config(tmp_path)
    runtime_result = _runtime_result_snapshot(status="success")
    runtime_result["runtime_selected_task_ids"] = ["runtime:task:mock"]
    monkeypatch.setattr(
        "benchmarks.minecraft.experiment._execute_real_runtime",
        lambda *args, **kwargs: runtime_result,
    )

    summary = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="runtime_selection_history",
        execute=True,
    )

    assert summary["runtime_selected_task_ids"] == ["runtime:task:mock"]
    assert summary["selected_task_id"] == "runtime:task:mock"
    assert summary["selected_description"] == "Runtime task"


def test_minecraft_execute_failure_uses_partial_runtime_snapshot(tmp_path, monkeypatch):
    config_path = _write_minecraft_config(tmp_path)

    def fail_with_partial(*args, **kwargs):
        runtime_result_path = Path(kwargs["runtime_result_path"])
        runtime_result_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_result_path.write_text(json.dumps(_runtime_result_snapshot(status="running")), encoding="utf-8")
        raise RuntimeError("server unavailable")

    monkeypatch.setattr("benchmarks.minecraft.experiment._execute_real_runtime", fail_with_partial)

    summary = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="execute_partial_error",
        execute=True,
    )

    output_dir = tmp_path / "result" / "execute_partial_error"
    runtime_snapshot = json.loads((output_dir / "runtime_dual_dag_snapshot.json").read_text(encoding="utf-8"))
    assert summary["error_type"] == "RuntimeError"
    assert summary["snapshot_source"] == "real_runtime"
    assert runtime_snapshot["nodes"][0]["lifecycle"]["status"] == "running"


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
    assert summary["runtime_process_isolated"] is True
    assert summary["runtime_process_terminated"] is True
    assert "timed out after 0.01 seconds" in summary["error"]
    assert (output_dir / "action_log.json").exists()
    persisted = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert persisted["timed_out"] is True
    common_rows = summarize_inputs([output_dir])
    assert common_rows[0]["error_type"] == "timeout"


def test_minecraft_execute_timeout_stops_child_activity(tmp_path, monkeypatch):
    config_path = _write_minecraft_config(tmp_path)
    late_marker = tmp_path / "late_child_activity.txt"

    def slow_then_write(*args, **kwargs):
        time.sleep(0.2)
        late_marker.write_text("still running", encoding="utf-8")
        return {}

    monkeypatch.setattr("benchmarks.minecraft.experiment._execute_real_runtime", slow_then_write)

    summary = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="child_stops",
        execute=True,
        execute_timeout_seconds=0.01,
    )
    time.sleep(0.3)

    assert summary["timed_out"] is True
    assert summary["runtime_process_terminated"] is True
    assert not late_marker.exists()


def test_runtime_process_termination_uses_kill_fallback():
    process = _StubbornProcess()

    metadata = _terminate_runtime_process(process, grace_seconds=0.01)

    assert process.calls == ["terminate", ("join", 0.01), "kill", ("join", None)]
    assert metadata == {
        "exit_code": -9,
        "terminated": True,
        "killed": True,
    }


def test_minecraft_execute_timeout_uses_partial_runtime_snapshot(tmp_path, monkeypatch):
    config_path = _write_minecraft_config(tmp_path)

    def slow_runtime_with_partial(*args, **kwargs):
        runtime_result_path = Path(kwargs["runtime_result_path"])
        runtime_result_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_result_path.write_text(json.dumps(_runtime_result_snapshot(status="running")), encoding="utf-8")
        time.sleep(1)

    monkeypatch.setattr("benchmarks.minecraft.experiment._execute_real_runtime", slow_runtime_with_partial)

    summary = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="execute_timeout_partial",
        execute=True,
        execute_timeout_seconds=0.01,
    )

    output_dir = tmp_path / "result" / "execute_timeout_partial"
    runtime_snapshot = json.loads((output_dir / "runtime_dual_dag_snapshot.json").read_text(encoding="utf-8"))
    assert summary["timed_out"] is True
    assert summary["snapshot_source"] == "real_runtime"
    assert runtime_snapshot["nodes"][0]["lifecycle"]["status"] == "running"


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


def test_minecraft_execute_uses_unique_per_run_result_paths_and_cleans_up(tmp_path, monkeypatch):
    config_path = _write_minecraft_config(tmp_path)

    def runtime_result(*args, **kwargs):
        runtime_result_path = Path(kwargs["runtime_result_path"])
        (runtime_result_path.parent.parent / "child_runtime_path.txt").write_text(
            str(runtime_result_path),
            encoding="utf-8",
        )
        return _runtime_result_snapshot(status="success")

    monkeypatch.setattr("benchmarks.minecraft.experiment._execute_real_runtime", runtime_result)

    first = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="run_a",
        execute=True,
    )
    second = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="run_b",
        execute=True,
    )

    expected_paths = [
        tmp_path / "result" / "run_a" / ".runtime" / "runtime_result.json",
        tmp_path / "result" / "run_b" / ".runtime" / "runtime_result.json",
    ]
    assert [
        Path(first["output_dir"]).joinpath("child_runtime_path.txt").read_text(encoding="utf-8"),
        Path(second["output_dir"]).joinpath("child_runtime_path.txt").read_text(encoding="utf-8"),
    ] == [str(path) for path in expected_paths]
    assert first["runtime_result_retained"] is False
    assert second["runtime_result_retained"] is False
    assert all(not path.exists() for path in expected_paths)
    assert (tmp_path / "result" / "run_a" / "runtime_dual_dag_snapshot.json").exists()
    assert (tmp_path / "result" / "run_b" / "runtime_dual_dag_snapshot.json").exists()


def test_minecraft_execute_ignores_stale_global_runtime_result(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config_path = _write_minecraft_config(tmp_path)
    stale_path = tmp_path / ".cache" / "minecraft_runtime_result.json"
    stale_path.parent.mkdir(parents=True)
    stale_path.write_text(json.dumps(_runtime_result_snapshot(status="running")), encoding="utf-8")
    monkeypatch.setattr(
        "benchmarks.minecraft.experiment._execute_real_runtime",
        lambda *args, **kwargs: {},
    )

    summary = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="ignore_stale",
        execute=True,
    )

    assert summary["snapshot_source"] == "config_fixture"
    assert stale_path.exists()


def test_minecraft_execute_ignores_partial_tmp_result(tmp_path, monkeypatch):
    config_path = _write_minecraft_config(tmp_path)

    def write_partial_tmp(*args, **kwargs):
        runtime_result_path = Path(kwargs["runtime_result_path"])
        runtime_result_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_result_path.with_suffix(".json.tmp").write_text(
            json.dumps(_runtime_result_snapshot(status="running")),
            encoding="utf-8",
        )
        raise RuntimeError("interrupted write")

    monkeypatch.setattr("benchmarks.minecraft.experiment._execute_real_runtime", write_partial_tmp)

    summary = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="partial_tmp",
        execute=True,
    )

    assert summary["snapshot_source"] == "config_fixture"
    assert summary["error_type"] == "RuntimeError"
    assert not (tmp_path / "result" / "partial_tmp" / ".runtime").exists()


def test_minecraft_execute_can_retain_internal_result_explicitly(tmp_path, monkeypatch):
    config_path = _write_minecraft_config(tmp_path)

    def persist_result(*args, **kwargs):
        from start_with_config import _write_runtime_result

        _write_runtime_result(
            str(kwargs["runtime_result_path"]),
            _runtime_result_snapshot(status="success"),
        )
        return {}

    monkeypatch.setattr("benchmarks.minecraft.experiment._execute_real_runtime", persist_result)

    summary = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="retained",
        execute=True,
        retain_runtime_result=True,
    )

    runtime_result_path = tmp_path / "result" / "retained" / ".runtime" / "runtime_result.json"
    assert summary["runtime_result_retained"] is True
    assert runtime_result_path.exists()
    assert not runtime_result_path.with_suffix(".json.tmp").exists()
    assert json.loads(runtime_result_path.read_text(encoding="utf-8"))["runtime_task_dag_snapshot"]


def test_minecraft_run_rejects_reuse_and_allows_explicit_overwrite(tmp_path):
    config_path = _write_minecraft_config(tmp_path)
    first = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="rerun",
    )

    with pytest.raises(RunDirectoryExistsError, match="not empty"):
        run_minecraft_experiment(
            config_path=config_path,
            output_root=tmp_path / "result",
            run_name="rerun",
        )

    second = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="rerun",
        overwrite=True,
    )
    run_dir = tmp_path / "result" / "rerun"

    assert second["attempt_id"] != first["attempt_id"]
    assert (run_dir / COMPLETION_MARKER_FILE).read_text(encoding="utf-8").strip() == second["attempt_id"]
    assert json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))["attempt_id"] == second["attempt_id"]
    assert json.loads((run_dir / "artifact_manifest.json").read_text(encoding="utf-8"))["status"] == "completed"


def test_minecraft_failed_run_has_no_completion_marker(tmp_path, monkeypatch):
    config_path = _write_minecraft_config(tmp_path)
    monkeypatch.setattr(
        "benchmarks.minecraft.experiment._execute_real_runtime",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("runtime failed")),
    )

    summary = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="failed_bundle",
        execute=True,
    )
    run_dir = tmp_path / "result" / "failed_bundle"
    manifest = json.loads((run_dir / "artifact_manifest.json").read_text(encoding="utf-8"))

    assert summary["error_type"] == "RuntimeError"
    assert manifest["attempt_id"] == summary["attempt_id"]
    assert manifest["status"] == "failed"
    assert not (run_dir / COMPLETION_MARKER_FILE).exists()


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


class _StubbornProcess:
    def __init__(self):
        self.calls = []
        self.exitcode = None
        self._alive = True

    def terminate(self):
        self.calls.append("terminate")

    def join(self, timeout=None):
        self.calls.append(("join", timeout))
        if timeout is None:
            self._alive = False
            self.exitcode = -9

    def is_alive(self):
        return self._alive

    def kill(self):
        self.calls.append("kill")


def _runtime_result_snapshot(status="success"):
    return {
        "score": {"progress": 1.0 if status == "success" else 0.5},
        "action_log": {"Alice": [{"action": "mock", "result": {"status": True}}]},
        "runtime_task_dag_snapshot": {
            "schema_version": "1.0.0",
            "runtime": "runtime_task_dag_store",
            "source_of_truth": "runtime_task_dag",
            "summary": {"task_node_count": 1, "task_edge_count": 0, "terminal_state": status},
            "nodes": [{
                "node_id": "runtime:task:mock",
                "node_type": "runtime_task",
                "content": {"description": "Runtime task", "metadata": {}, "milestones": [], "reflect": None},
                "lifecycle": {
                    "status": status,
                    "candidate_agents": ["Alice"],
                    "active_agents": ["Alice"] if status == "running" else [],
                    "last_assigned_agents": ["Alice"],
                    "required_agent_count": 1,
                },
                "derived": {"dependency_ready": True, "blocked_by_tasks": []},
                "provenance": {"source": "test"},
            }],
            "edges": [],
            "schema": {},
        },
        "task_graph_snapshot": {
            "mutates_runtime": False,
            "tasks": [{"description": "Runtime task", "status": status}],
            "edges": [],
        },
        "error": None,
    }


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
