import argparse
import json
import subprocess

import pytest

from benchmarks.common.run_artifacts import RunDirectoryExistsError
from benchmarks.cwah.matrix import (
    MatrixRun,
    aggregate_results,
    build_matrix,
    matrix_port,
    parse_int_list,
    run_matrix_item,
    write_matrix_summary,
)


def test_parse_int_list_and_build_matrix():
    assert parse_int_list("0, 2,3") == (0, 2, 3)
    assert build_matrix((0, 1), (3, 4)) == (
        MatrixRun(index=0, task_id=0, seed=3),
        MatrixRun(index=1, task_id=0, seed=4),
        MatrixRun(index=2, task_id=1, seed=3),
        MatrixRun(index=3, task_id=1, seed=4),
    )


def test_matrix_ports_are_unique_for_task_seed_collision_case():
    runs = build_matrix((0, 1), (0, 1))

    ports = [matrix_port(base_port=6314, run=run, port_stride=1) for run in runs]

    assert ports == [6314, 6315, 6316, 6317]
    assert len(ports) == len(set(ports))


def test_matrix_port_supports_stride_and_rejects_invalid_stride():
    run = MatrixRun(index=2, task_id=10, seed=20)

    assert matrix_port(base_port=7000, run=run, port_stride=10) == 7020
    with pytest.raises(ValueError, match="port_stride"):
        matrix_port(base_port=7000, run=run, port_stride=0)


def test_aggregate_results_counts_passes_and_progress():
    aggregate = aggregate_results([
        {"passed": True, "metrics": {"task_success": True, "normalized_progress": 1.0}},
        {"passed": False, "metrics": {"task_success": False, "normalized_progress": 0.25}},
    ])

    assert aggregate == {
        "runs": 2,
        "passed_runs": 1,
        "failed_runs": 1,
        "task_successes": 1,
        "average_progress": 0.625,
    }


def test_write_matrix_summary(tmp_path):
    write_matrix_summary(
        output_dir=tmp_path,
        results=[{
            "task_id": 0,
            "seed": 1,
            "matrix_index": 0,
            "base_port": 6314,
            "passed": True,
            "metrics": {"task_success": False, "normalized_progress": 0.5, "episode_steps": 2},
            "event_counts": {"policy_overrides": 1},
            "diagnostics": {"failed_action_record_count": 2, "open_failure_record_count": 1, "navigation_loop_count": 1, "result_failure_count": 3, "failure_reason_counts": {"script_impossible": 2}, "open_failure_reason_counts": {"already_open": 1}},
        }],
    )

    summary = json.loads((tmp_path / "matrix_summary.json").read_text(encoding="utf-8"))
    metrics_csv = (tmp_path / "matrix_metrics.csv").read_text(encoding="utf-8")

    assert summary["aggregate"]["passed_runs"] == 1
    assert "matrix_index,task_id,seed,base_port,passed,task_success,normalized_progress,episode_steps,policy_overrides,failed_action_records,open_failure_records,navigation_loop_count,result_failures,failure_reason_counts,open_failure_reason_counts" in metrics_csv
    assert '0,0,1,6314,True,False,0.5,2,1,2,1,1,3,"{""script_impossible"": 2}","{""already_open"": 1}"' in metrics_csv


def test_matrix_uses_environment_credential_without_argv_or_artifact_leak(tmp_path, monkeypatch):
    secret = "sentinel-secret-value-12345"
    captured_command = []

    def fake_run(command, **kwargs):
        captured_command.extend(command)
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=f"provider output {secret}",
            stderr=f"provider error {secret}",
        )

    monkeypatch.setenv("CWAH_LLM_API_KEY", secret)
    monkeypatch.setattr("benchmarks.cwah.matrix.subprocess.run", fake_run)
    args = argparse.Namespace(
        env="mock",
        max_steps=1,
        max_policy_steps=1,
        prefer_physical_after_steps=0,
        navigation_loop_threshold=12,
        base_url="http://example.test/v1",
        model="model",
        full_episode=False,
        coela_cwah_path="",
        dataset_path="",
        executable_file="",
        base_port=6314,
        port_stride=1,
    )

    result = run_matrix_item(
        args=args,
        output_dir=tmp_path,
        run=MatrixRun(index=0, task_id=0, seed=0),
    )

    assert "--api-key" not in captured_command
    assert secret not in captured_command
    assert secret not in json.dumps(result)
    assert result["stdout"] == "provider output [REDACTED]"
    assert result["stderr"] == "provider error [REDACTED]"


def test_matrix_rejects_stale_child_summary_before_execution(tmp_path):
    stale_summary = tmp_path / "task_0_seed_0" / "normalized" / "summary.json"
    stale_summary.parent.mkdir(parents=True)
    stale_summary.write_text(
        json.dumps({"metrics": {"task_success": True, "normalized_progress": 1.0}}),
        encoding="utf-8",
    )
    args = _matrix_args()

    with pytest.raises(RunDirectoryExistsError, match="not empty"):
        run_matrix_item(
            args=args,
            output_dir=tmp_path,
            run=MatrixRun(index=0, task_id=0, seed=0),
        )


def test_matrix_rejects_summary_from_another_attempt(tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        artifact_dir = command[command.index("--artifact-dir") + 1]
        artifact_path = tmp_path / "task_0_seed_0" / "normalized"
        assert artifact_dir == str(artifact_path)
        artifact_path.mkdir(parents=True)
        (artifact_path / "summary.json").write_text(
            json.dumps({
                "attempt_id": "stale-attempt",
                "metrics": {"task_success": True, "normalized_progress": 1.0},
            }),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("benchmarks.cwah.matrix.subprocess.run", fake_run)

    result = run_matrix_item(
        args=_matrix_args(),
        output_dir=tmp_path,
        run=MatrixRun(index=0, task_id=0, seed=0),
    )

    assert result["passed"] is False
    assert result["artifact_error"] == "summary_attempt_mismatch"
    assert "metrics" not in result
    manifest = json.loads(
        (tmp_path / "task_0_seed_0" / "artifact_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "failed"


def test_matrix_child_exception_finalizes_failed_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "benchmarks.cwah.matrix.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("launch failed")),
    )

    with pytest.raises(RuntimeError, match="launch failed"):
        run_matrix_item(
            args=_matrix_args(),
            output_dir=tmp_path,
            run=MatrixRun(index=0, task_id=0, seed=0),
        )
    run_dir = tmp_path / "task_0_seed_0"
    manifest = json.loads((run_dir / "artifact_manifest.json").read_text(encoding="utf-8"))

    assert manifest["status"] == "failed"
    assert not (run_dir / "_COMPLETED").exists()


def _matrix_args():
    return argparse.Namespace(
        env="mock",
        max_steps=1,
        max_policy_steps=1,
        prefer_physical_after_steps=0,
        navigation_loop_threshold=12,
        base_url="http://example.test/v1",
        model="model",
        full_episode=False,
        coela_cwah_path="",
        dataset_path="",
        executable_file="",
        base_port=6314,
        port_stride=1,
    )
