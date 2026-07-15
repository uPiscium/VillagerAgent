import argparse
import json
import subprocess
import sys

import pytest

from benchmarks.common.run_artifacts import (
    finalize_run_directory,
    prepare_run_directory,
    read_attempt_id,
    validate_run_attempt,
)
from benchmarks.cwah.baseline import build_manifest, build_matrix_command, main
from benchmarks.experiment_provenance import finalize_provenance, write_provenance


def test_build_matrix_command_forwards_baseline_options(tmp_path):
    args = argparse.Namespace(
        env="coela",
        tasks="0,1",
        seeds="0,1",
        max_steps=25,
        max_policy_steps=2,
        prefer_physical_after_steps=0,
        navigation_loop_threshold=12,
        base_url="http://example.test/v1",
        api_key="key",
        model="model",
        base_port=6414,
        port_stride=10,
        full_episode=True,
        coela_cwah_path="/tmp/coela/cwah",
        dataset_path="/tmp/coela/dataset.pik",
        executable_file="/tmp/coela/linux_exec",
    )

    command = build_matrix_command(args=args, output_dir=tmp_path / "matrix")

    assert command[:3] == [sys.executable, "-m", "benchmarks.cwah.matrix"]
    assert "--full-episode" in command
    assert command[command.index("--tasks") + 1] == "0,1"
    assert command[command.index("--base-port") + 1] == "6414"
    assert command[command.index("--port-stride") + 1] == "10"
    assert command[command.index("--navigation-loop-threshold") + 1] == "12"
    assert command[command.index("--coela-cwah-path") + 1] == "/tmp/coela/cwah"
    assert "--api-key" not in command
    assert "key" not in command


def test_build_manifest_marks_mock_as_validation_not_performance_claim(tmp_path):
    secret = "sentinel-secret-value-12345"
    args = argparse.Namespace(
        env="mock",
        tasks="0",
        seeds="0",
        max_steps=4,
        max_policy_steps=2,
        full_episode=True,
        prefer_physical_after_steps=0,
        navigation_loop_threshold=12,
        model="model",
        base_port=6314,
        port_stride=1,
        api_key=secret,
    )

    manifest = build_manifest(
        args=args,
        command=["python", "-m", "benchmarks.cwah.matrix", "--api-key", secret],
        matrix_returncode=0,
        matrix_stdout=json.dumps({"runs": 1, "error": secret}),
        matrix_stderr=f"provider rejected {secret}",
        output_dir=tmp_path / "matrix",
        report_dir=tmp_path / "report",
        common_rows=[{"run_name": "task_0_seed_0"}],
    )

    assert manifest["baseline_type"] == "mock_validation"
    assert manifest["performance_claim"] is False
    assert manifest["runs"] == 1
    assert manifest["outputs"]["common_report_json"].endswith("common_report.json")
    serialized = json.dumps(manifest)
    assert secret not in serialized
    assert manifest["command"][-1] == "[REDACTED]"


def test_failed_matrix_launch_does_not_reuse_stale_summary(tmp_path, monkeypatch):
    output_dir = tmp_path / "matrix"
    stale_attempt = prepare_run_directory(output_dir, producer="stale")
    (output_dir / "matrix_summary.json").write_text(
        json.dumps({
            "attempt_id": stale_attempt,
            "runs": [{"run_name": "stale", "passed": True, "metrics": {"task_success": True}}],
        }),
        encoding="utf-8",
    )
    finalize_run_directory(
        output_dir,
        attempt_id=stale_attempt,
        producer="stale",
        status="completed",
        stamp_nested=False,
    )
    args = _baseline_args(output_dir=output_dir, report_dir=tmp_path / "report")
    monkeypatch.setattr("benchmarks.cwah.baseline.parse_args", lambda: args)
    monkeypatch.setattr(
        "benchmarks.cwah.baseline.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, stdout="", stderr="not empty"),
    )

    with pytest.raises(SystemExit, match="1"):
        main()

    manifest = json.loads(
        (tmp_path / "report" / "baseline_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["runs"] == 0
    assert manifest["matrix_attempt_id"] is None


def test_failed_matrix_launch_does_not_mutate_stale_matrix_for_default_report(tmp_path, monkeypatch):
    output_dir = tmp_path / "matrix"
    stale_attempt = prepare_run_directory(output_dir, producer="benchmarks.cwah.matrix")
    (output_dir / "matrix_summary.json").write_text(
        json.dumps({"runs": [{"run_name": "stale", "passed": True}]}),
        encoding="utf-8",
    )
    finalize_run_directory(
        output_dir,
        attempt_id=stale_attempt,
        producer="benchmarks.cwah.matrix",
        status="completed",
        stamp_nested=False,
    )
    args = _baseline_args(output_dir=output_dir, report_dir="")
    monkeypatch.setattr("benchmarks.cwah.baseline.parse_args", lambda: args)
    monkeypatch.setattr(
        "benchmarks.cwah.baseline.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, stdout="", stderr="failed"),
    )

    with pytest.raises(SystemExit, match="1"):
        main()

    assert not (output_dir / "common_report").exists()
    validate_run_attempt(output_dir, attempt_id=stale_attempt)


def test_baseline_writes_default_report_inside_completed_matrix(tmp_path, monkeypatch):
    output_dir = tmp_path / "matrix"
    args = _baseline_args(output_dir=output_dir, report_dir="")
    monkeypatch.setattr("benchmarks.cwah.baseline.parse_args", lambda: args)

    def fake_run(command, **kwargs):
        matrix_attempt = prepare_run_directory(output_dir, producer="benchmarks.cwah.matrix")
        (output_dir / "matrix_summary.json").write_text(
            json.dumps({
                "runs": [{
                    "run_name": "task_0_seed_0",
                    "task_id": 0,
                    "seed": 0,
                    "passed": True,
                    "metrics": {"task_success": True, "normalized_progress": 1.0},
                }],
            }),
            encoding="utf-8",
        )
        write_provenance(
            output_dir,
            benchmark="cwah",
            command=command,
            resolved_config={"attempt_id": matrix_attempt},
        )
        finalize_provenance(output_dir, status="success")
        finalize_run_directory(
            output_dir,
            attempt_id=matrix_attempt,
            producer="benchmarks.cwah.matrix",
            status="completed",
            stamp_nested=False,
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("benchmarks.cwah.baseline.subprocess.run", fake_run)

    main()

    report_dir = output_dir / "common_report"
    assert (report_dir / "common_report.json").exists()
    manifest = json.loads((report_dir / "baseline_manifest.json").read_text(encoding="utf-8"))
    assert manifest["runs"] == 1
    assert manifest["matrix_attempt_id"]
    assert manifest["outputs"]["matrix_provenance"] == str(output_dir / "provenance.json")
    report_provenance = json.loads((report_dir / "provenance.json").read_text(encoding="utf-8"))
    matrix_asset = next(
        asset for asset in report_provenance["assets"] if asset["name"] == "matrix_provenance"
    )
    assert report_provenance["schema_version"] == "2.0.0"
    assert report_provenance["lifecycle"]["status"] == "success"
    assert report_provenance["effective_settings"]["matrix_provenance"] == str(
        output_dir / "provenance.json"
    )
    assert matrix_asset["available"] is True
    assert matrix_asset["sha256"]


def test_baseline_finalizes_nested_report_and_matrix_after_report_error(tmp_path, monkeypatch):
    output_dir = tmp_path / "matrix"
    args = _baseline_args(output_dir=output_dir, report_dir="")
    monkeypatch.setattr("benchmarks.cwah.baseline.parse_args", lambda: args)

    def fake_run(command, **kwargs):
        matrix_attempt = prepare_run_directory(output_dir, producer="benchmarks.cwah.matrix")
        (output_dir / "matrix_summary.json").write_text(
            json.dumps({"runs": [{"run_name": "task_0_seed_0", "passed": True, "metrics": {}}]}),
            encoding="utf-8",
        )
        finalize_run_directory(
            output_dir,
            attempt_id=matrix_attempt,
            producer="benchmarks.cwah.matrix",
            status="completed",
            stamp_nested=False,
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("benchmarks.cwah.baseline.subprocess.run", fake_run)
    monkeypatch.setattr(
        "benchmarks.cwah.baseline.write_json_report",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("report failed")),
    )

    with pytest.raises(RuntimeError, match="report failed"):
        main()

    report_dir = output_dir / "common_report"
    report_manifest = json.loads((report_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert report_manifest["status"] == "failed"
    matrix_attempt = read_attempt_id(output_dir)
    validate_run_attempt(output_dir, attempt_id=matrix_attempt)


def test_failed_matrix_writes_failed_default_report_without_reusing_stale_attempt(tmp_path, monkeypatch):
    output_dir = tmp_path / "matrix"
    args = _baseline_args(output_dir=output_dir, report_dir="")
    monkeypatch.setattr("benchmarks.cwah.baseline.parse_args", lambda: args)

    def fake_run(command, **kwargs):
        matrix_attempt = prepare_run_directory(output_dir, producer="benchmarks.cwah.matrix")
        (output_dir / "matrix_summary.json").write_text(
            json.dumps({"runs": [{"run_name": "task_0_seed_0", "passed": False, "metrics": {}}]}),
            encoding="utf-8",
        )
        finalize_run_directory(
            output_dir,
            attempt_id=matrix_attempt,
            producer="benchmarks.cwah.matrix",
            status="failed",
            stamp_nested=False,
        )
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="failed")

    monkeypatch.setattr("benchmarks.cwah.baseline.subprocess.run", fake_run)

    with pytest.raises(SystemExit, match="1"):
        main()

    report_dir = output_dir / "common_report"
    report_manifest = json.loads((report_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    matrix_manifest = json.loads((output_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert report_manifest["status"] == "failed"
    assert matrix_manifest["status"] == "failed"
    assert json.loads((report_dir / "baseline_manifest.json").read_text(encoding="utf-8"))["runs"] == 1
    report_provenance = json.loads((report_dir / "provenance.json").read_text(encoding="utf-8"))
    assert report_provenance["lifecycle"]["status"] == "failure"


def test_baseline_rejects_report_directory_containing_matrix(tmp_path, monkeypatch):
    args = _baseline_args(
        output_dir=tmp_path / "report" / "matrix",
        report_dir=tmp_path / "report",
    )
    monkeypatch.setattr("benchmarks.cwah.baseline.parse_args", lambda: args)

    with pytest.raises(ValueError, match="must not equal or contain"):
        main()


def _baseline_args(*, output_dir, report_dir):
    return argparse.Namespace(
        env="mock",
        tasks="0",
        seeds="0",
        output_dir=str(output_dir),
        report_dir=str(report_dir),
        max_steps=1,
        max_policy_steps=1,
        full_episode=False,
        prefer_physical_after_steps=0,
        navigation_loop_threshold=12,
        base_url="http://example.test/v1",
        model="model",
        temperature=0.0,
        max_tokens=128,
        coela_cwah_path="",
        dataset_path="",
        executable_file="",
        base_port=6314,
        port_stride=1,
        overwrite=False,
    )
