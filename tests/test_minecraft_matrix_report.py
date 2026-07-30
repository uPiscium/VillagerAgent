import json

import pytest

from benchmarks.minecraft.matrix_report import (
    MatrixReportValidationError,
    generate_matrix_report,
    validate_matrix_manifest,
)


def test_generates_ordered_reports_counts_manifest_and_completed_marker(tmp_path):
    records = [_record(f"run-{index:02d}", f"a{index}", True, index) for index in reversed(range(12))]

    result = generate_matrix_report(tmp_path, records, expected_run_count=12)

    rows = [json.loads(line) for line in (tmp_path / "matrix_runs.jsonl").read_text().splitlines()]
    assert [row["run_name"] for row in rows] == [f"run-{index:02d}" for index in range(12)]
    assert result["gate_passed"] is True
    assert result["passed_runs"] == 12
    assert (tmp_path / "_MATRIX_COMPLETED").is_file()
    assert not (tmp_path / "_MATRIX_FAILED").exists()
    manifest = validate_matrix_manifest(tmp_path)
    assert [item["path"] for item in manifest["artifacts"]] == [
        "matrix_runs.jsonl", "matrix_summary.json", "matrix_gate_report.md"
    ]
    assert all(item["sha256"] for item in manifest["artifacts"])
    assert str(tmp_path) not in "".join(path.read_text() for path in tmp_path.iterdir() if path.is_file())


def test_failed_or_missing_run_writes_only_failed_marker(tmp_path):
    result = generate_matrix_report(tmp_path, [_record("run-1", "a1", False, 0)], expected_run_count=2)

    assert result["gate_passed"] is False
    assert result["failed_runs"] == 1
    assert result["missing_runs"] == 1
    assert (tmp_path / "_MATRIX_FAILED").is_file()
    assert not (tmp_path / "_MATRIX_COMPLETED").exists()


def test_regeneration_switches_terminal_markers(tmp_path):
    generate_matrix_report(tmp_path, [_record("run-1", "a1", False, 0)])
    generate_matrix_report(
        tmp_path,
        [_record(f"run-{index:02d}", f"a{index}", True, index) for index in range(12)],
        expected_run_count=12,
    )

    assert (tmp_path / "_MATRIX_COMPLETED").exists()
    assert not (tmp_path / "_MATRIX_FAILED").exists()


def test_manifest_validation_detects_artifact_tampering(tmp_path):
    generate_matrix_report(tmp_path, [_record("run-1", "a1", False, 0)])
    with (tmp_path / "matrix_summary.json").open("a") as stream:
        stream.write(" ")

    with pytest.raises(MatrixReportValidationError, match="identity mismatch"):
        validate_matrix_manifest(tmp_path)


def test_manifest_validation_rejects_both_markers(tmp_path):
    generate_matrix_report(tmp_path, [_record("run-1", "a1", False, 0)])
    (tmp_path / "_MATRIX_COMPLETED").write_text("{}\n")

    with pytest.raises(MatrixReportValidationError, match="exactly one"):
        validate_matrix_manifest(tmp_path)


def test_rejects_duplicate_run_identity(tmp_path):
    with pytest.raises(MatrixReportValidationError, match="duplicate"):
        generate_matrix_report(tmp_path, [_record("run", "attempt", True, 0), _record("run", "attempt", True, 1)])


def _record(name, attempt, passed, index):
    return {
        "schema_version": 1,
        "record_type": "minecraft_matrix_run_validation",
        "matrix_index": index,
        "run_name": name,
        "attempt_id": attempt,
        "passed": passed,
        "checks": [],
        "errors": [],
    }
