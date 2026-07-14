import argparse
import json
import subprocess
import sys

import pytest

from benchmarks.common.run_artifacts import finalize_run_directory, prepare_run_directory
from benchmarks.cwah.baseline import build_manifest, build_matrix_command, main


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
        coela_cwah_path="",
        dataset_path="",
        executable_file="",
        base_port=6314,
        port_stride=1,
        overwrite=False,
    )
