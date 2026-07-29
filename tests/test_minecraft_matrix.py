import json

import pytest

from benchmarks.common.report import summarize_inputs
from benchmarks.minecraft.matrix import main, run_minecraft_matrix
from benchmarks.minecraft.run_lock import MinecraftTargetLock


@pytest.fixture(autouse=True)
def _isolate_minecraft_lock_root(tmp_path, monkeypatch):
    monkeypatch.setenv("VILLAGER_MINECRAFT_LOCK_ROOT", str(tmp_path / "target-locks"))


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
    assert persisted["runs"][0]["provenance"].endswith("bell_run/provenance.json")
    matrix_provenance = json.loads((matrix_dir / "provenance.json").read_text(encoding="utf-8"))
    assert len(matrix_provenance["effective_settings"]["run_plan"]) == 2
    assert matrix_provenance["lifecycle"]["status"] == "success"
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
    configs = [
        dict(_config("first", 0), required_artifacts=[]),
        dict(_config("second", 1), required_artifacts=[]),
    ]
    config_path.write_text(
        json.dumps(configs),
        encoding="utf-8",
    )
    def runtime_result(*args, **kwargs):
        runtime_result_path = kwargs["runtime_result_path"]
        kwargs["runtime_root"].joinpath("child_runtime_path.txt").write_text(
            str(runtime_result_path),
            encoding="utf-8",
        )
        return {
            "bridge_cleanup": {"cleanup_complete": True, "processes": {}},
        }

    monkeypatch.setattr("benchmarks.minecraft.experiment._execute_real_runtime", runtime_result)

    summary = run_minecraft_matrix(
        config_path=config_path,
        output_dir=tmp_path / "matrix",
        run_names=["first_run", "second_run"],
        execute=True,
        execute_timeout_seconds=30,
    )

    expected_paths = [
        tmp_path / "matrix" / "runs" / run["run_name"] / run["runtime_result_path"]
        for run in summary["runs"]
    ]
    assert [
        (
            tmp_path
            / "matrix"
            / "runs"
            / run["run_name"]
            / run["runtime_root"]
            / "child_runtime_path.txt"
        ).read_text(encoding="utf-8")
        for run in summary["runs"]
    ] == [str(path) for path in expected_paths]


@pytest.mark.parametrize(
    "unsafe_update",
    [
        {"runtime_target_safe_to_reuse": False},
        {"bridge_cleanup": {}},
        {"bridge_cleanup": {"cleanup_complete": False, "processes": {}}},
        {"bridge_cleanup": {"cleanup_complete": True, "processes": {"Alice": {"alive_after_kill": True}}}},
    ],
)
def test_minecraft_matrix_aborts_and_skips_remaining_unsafe_target_runs(
    tmp_path,
    monkeypatch,
    unsafe_update,
):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(
        json.dumps([_config("first", 0), _config("second", 1), _config("third", 2)]),
        encoding="utf-8",
    )
    calls = []

    def run_single(**kwargs):
        calls.append(kwargs["run_name"])
        summary = _matrix_run_summary(tmp_path, kwargs["run_name"])
        summary.update(unsafe_update)
        return summary

    _mock_matrix_run_dependencies(monkeypatch, run_single)

    summary = run_minecraft_matrix(
        config_path=config_path,
        output_dir=tmp_path / "matrix",
        run_names=["first", "second", "third"],
        execute=True,
        execute_timeout_seconds=30,
    )

    assert calls == ["first"]
    assert summary["aborted"] is True
    assert summary["abort_reason"] == "unsafe_runtime_target"
    assert summary["unsafe_run"]["matrix_index"] == 0
    assert summary["skipped_runs"] == 2
    assert [run["status"] for run in summary["runs"][1:]] == ["skipped", "skipped"]
    assert all(run["reason"] == "unsafe_runtime_target" for run in summary["runs"][1:])


def test_minecraft_matrix_continues_after_safe_task_failure(tmp_path, monkeypatch):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(
        json.dumps([_config("first", 0), _config("second", 1)]),
        encoding="utf-8",
    )
    calls = []

    def run_single(**kwargs):
        calls.append(kwargs["run_name"])
        summary = _matrix_run_summary(tmp_path, kwargs["run_name"])
        if len(calls) == 1:
            summary["error"] = "task failed"
            summary["artifact_admission"] = {"passed": False}
        return summary

    _mock_matrix_run_dependencies(monkeypatch, run_single)

    summary = run_minecraft_matrix(
        config_path=config_path,
        output_dir=tmp_path / "matrix",
        run_names=["first", "second"],
        execute=True,
        execute_timeout_seconds=30,
    )

    assert calls == ["first", "second"]
    assert summary["aborted"] is False
    assert summary["failed_runs"] == 1
    assert summary["completed_runs"] == 1


def test_minecraft_matrix_rejects_unknown_runtime_process_evidence(tmp_path, monkeypatch):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(
        json.dumps([_config("first", 0), _config("second", 1)]),
        encoding="utf-8",
    )
    calls = []

    def run_single(**kwargs):
        calls.append(kwargs["run_name"])
        summary = _matrix_run_summary(tmp_path, kwargs["run_name"])
        summary["runtime_process_alive_after_kill"] = None
        return summary

    _mock_matrix_run_dependencies(monkeypatch, run_single)

    summary = run_minecraft_matrix(
        config_path=config_path,
        output_dir=tmp_path / "matrix",
        run_names=["first", "second"],
        execute=True,
        execute_timeout_seconds=30,
    )

    assert calls == ["first"]
    assert summary["aborted"] is True
    assert summary["abort_reason"] == "unsafe_runtime_target"
    assert summary["skipped_runs"] == 1
    assert summary["runs"][1]["reason"] == "unsafe_runtime_target"


def test_minecraft_matrix_classifies_preexisting_quarantine_and_never_starts_runtime(
    tmp_path,
    monkeypatch,
):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(
        json.dumps([_config("first", 0), _config("second", 1)]),
        encoding="utf-8",
    )
    lock = MinecraftTargetLock(
        lock_root=tmp_path / "target-locks",
        host="127.0.0.1",
        port=25565,
        world_id="",
        attempt_id="unsafe-attempt",
    )
    with lock:
        lock.quarantine(
            run_name="unsafe-run",
            reasons=["bridge_cleanup_incomplete"],
            diagnostics={"runtime_started": True},
        )
    calls = []
    monkeypatch.setattr(
        "benchmarks.minecraft.experiment._execute_real_runtime_bounded",
        lambda *_args, **_kwargs: calls.append("runtime"),
    )

    summary = run_minecraft_matrix(
        config_path=config_path,
        output_dir=tmp_path / "matrix",
        run_names=["first", "second"],
        execute=True,
        execute_timeout_seconds=30,
    )

    assert calls == []
    assert summary["aborted"] is True
    assert summary["abort_reason"] == "target_quarantined"
    assert summary["runs"][0]["runtime_started"] is False
    assert summary["runs"][0]["runtime_target_quarantined"] is True
    assert summary["runs"][1]["status"] == "skipped"
    assert summary["runs"][1]["reason"] == "target_quarantined"


def test_minecraft_matrix_aborts_after_target_lock_metadata_error(tmp_path, monkeypatch):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(
        json.dumps([_config("first", 0), _config("second", 1), _config("third", 2)]),
        encoding="utf-8",
    )
    calls = []

    def run_single(**kwargs):
        calls.append(kwargs["run_name"])
        summary = _matrix_run_summary(tmp_path, kwargs["run_name"])
        summary.update({
            "error": "lock metadata is invalid",
            "error_type": "MinecraftTargetLockMetadataError",
            "runtime_target_lock_admission": "metadata_invalid",
            "runtime_target_lock_metadata_valid": False,
            "runtime_target_safe_to_reuse": False,
            "runtime_target_quarantined": False,
        })
        return summary

    _mock_matrix_run_dependencies(monkeypatch, run_single)

    summary = run_minecraft_matrix(
        config_path=config_path,
        output_dir=tmp_path / "matrix",
        run_names=["first", "second", "third"],
        execute=True,
        execute_timeout_seconds=30,
    )

    assert calls == ["first"]
    assert summary["aborted"] is True
    assert summary["abort_reason"] == "target_lock_metadata_invalid"
    assert summary["unsafe_run"]["error_type"] == "MinecraftTargetLockMetadataError"
    assert summary["skipped_runs"] == 2
    assert all(
        run["reason"] == "target_lock_metadata_invalid"
        for run in summary["runs"][1:]
    )


def test_minecraft_matrix_aborts_when_target_lock_is_unavailable(tmp_path, monkeypatch):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(
        json.dumps([_config("first", 0), _config("second", 1), _config("third", 2)]),
        encoding="utf-8",
    )
    calls = []

    def run_single(**kwargs):
        calls.append(kwargs["run_name"])
        summary = _matrix_run_summary(tmp_path, kwargs["run_name"])
        summary.update({
            "error": "Minecraft target is busy",
            "error_type": "MinecraftTargetLockBusyError",
            "runtime_started": False,
            "runtime_target_lock_admission": "busy",
            "runtime_target_lock_unavailable": True,
            "runtime_target_lock_unavailable_reason": "busy",
            "runtime_target_lock_owner": {
                "status": "acquired",
                "attempt_id": "attempt-owner",
            },
            "runtime_target_lock_metadata_valid": None,
            "runtime_target_safe_to_reuse": False,
            "runtime_target_quarantined": False,
        })
        return summary

    _mock_matrix_run_dependencies(monkeypatch, run_single)

    summary = run_minecraft_matrix(
        config_path=config_path,
        output_dir=tmp_path / "matrix",
        run_names=["first", "second", "third"],
        execute=True,
        execute_timeout_seconds=30,
    )

    assert calls == ["first"]
    assert summary["aborted"] is True
    assert summary["abort_reason"] == "target_lock_unavailable"
    assert summary["skipped_runs"] == 2
    assert summary["runs"][0]["runtime_target_lock_admission"] == "busy"
    assert summary["runs"][0]["runtime_target_lock_owner"]["attempt_id"] == "attempt-owner"
    assert all(
        run["reason"] == "target_lock_unavailable"
        for run in summary["runs"][1:]
    )


def test_minecraft_matrix_dry_run_does_not_require_cleanup_metadata(tmp_path, monkeypatch):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(
        json.dumps([_config("first", 0), _config("second", 1)]),
        encoding="utf-8",
    )
    calls = []

    def run_single(**kwargs):
        calls.append(kwargs["run_name"])
        summary = _matrix_run_summary(tmp_path, kwargs["run_name"])
        summary.pop("runtime_target_safe_to_reuse")
        summary.pop("bridge_cleanup")
        return summary

    _mock_matrix_run_dependencies(monkeypatch, run_single)

    summary = run_minecraft_matrix(
        config_path=config_path,
        output_dir=tmp_path / "matrix",
        run_names=["first", "second"],
    )

    assert calls == ["first", "second"]
    assert summary["aborted"] is False
    assert summary["completed_runs"] == 2


def test_minecraft_matrix_cli_returns_failure_when_aborted(tmp_path, monkeypatch):
    config_path = tmp_path / "minecraft_config.json"
    config_path.write_text(json.dumps(_config("first", 0)), encoding="utf-8")
    monkeypatch.setattr(
        "benchmarks.minecraft.matrix.run_minecraft_matrix",
        lambda **_kwargs: {"aborted": True, "failed_runs": 1},
    )

    assert main(["--config", str(config_path)]) == 1


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


def _matrix_run_summary(tmp_path, run_name):
    return {
        "output_dir": str(tmp_path / "single-runs" / run_name),
        "attempt_id": f"attempt-{run_name}",
        "run_name": run_name,
        "mode": "execute",
        "error": None,
        "artifact_admission": {"passed": True},
        "runtime_target_safe_to_reuse": True,
        "runtime_started": True,
        "runtime_target_lock_admission": "granted",
        "runtime_target_lock_unavailable": False,
        "runtime_target_lock_unavailable_reason": None,
        "runtime_target_lock_owner": {},
        "runtime_process_alive_after_kill": False,
        "runtime_process_group_alive_after_kill": False,
        "runtime_target_lock_metadata_valid": True,
        "runtime_target_quarantined": False,
        "bridge_cleanup": {"cleanup_complete": True, "processes": {}},
    }


def _mock_matrix_run_dependencies(monkeypatch, run_single):
    monkeypatch.setattr(
        "benchmarks.minecraft.matrix.run_minecraft_experiment",
        run_single,
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.matrix.validate_run_attempt",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.matrix.summarize_minecraft_run",
        lambda _run_dir, summary: {
            "benchmark": "minecraft",
            "run_name": summary["run_name"],
            "status": "failed" if summary.get("error") else "success",
        },
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.matrix._read_json",
        lambda path: {} if path.name == "metrics.json" else json.loads(path.read_text(encoding="utf-8")),
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
        "task_scenario": "move",
    }
