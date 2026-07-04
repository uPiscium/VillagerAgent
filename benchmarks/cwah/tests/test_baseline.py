import argparse
import json
import sys

from benchmarks.cwah.baseline import build_manifest, build_matrix_command


def test_build_matrix_command_forwards_baseline_options(tmp_path):
    args = argparse.Namespace(
        env="coela",
        tasks="0,1",
        seeds="0,1",
        max_steps=25,
        max_policy_steps=2,
        prefer_physical_after_steps=0,
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
    assert command[command.index("--coela-cwah-path") + 1] == "/tmp/coela/cwah"


def test_build_manifest_marks_mock_as_validation_not_performance_claim(tmp_path):
    args = argparse.Namespace(
        env="mock",
        tasks="0",
        seeds="0",
        max_steps=4,
        max_policy_steps=2,
        full_episode=True,
        prefer_physical_after_steps=0,
        model="model",
        base_port=6314,
        port_stride=1,
    )

    manifest = build_manifest(
        args=args,
        command=["python", "-m", "benchmarks.cwah.matrix"],
        matrix_returncode=0,
        matrix_stdout=json.dumps({"runs": 1}),
        matrix_stderr="",
        output_dir=tmp_path / "matrix",
        report_dir=tmp_path / "report",
        common_rows=[{"run_name": "task_0_seed_0"}],
    )

    assert manifest["baseline_type"] == "mock_validation"
    assert manifest["performance_claim"] is False
    assert manifest["runs"] == 1
    assert manifest["outputs"]["common_report_json"].endswith("common_report.json")
