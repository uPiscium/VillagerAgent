import csv
import json

import pytest
import yaml

from benchmarks.common.run_artifacts import (
    ARTIFACT_MANIFEST_FILE,
    COMPLETION_MARKER_FILE,
    RunArtifactValidationError,
    RunDirectoryExistsError,
    finalize_run_directory,
    prepare_run_directory,
    validate_run_attempt,
)


def test_run_directory_rejects_reuse_and_explicit_overwrite_replaces_it(tmp_path):
    run_dir = tmp_path / "run"
    first_attempt = prepare_run_directory(run_dir, producer="test")
    (run_dir / "stale.json").write_text('{"stale": true}', encoding="utf-8")

    with pytest.raises(RunDirectoryExistsError, match="not empty"):
        prepare_run_directory(run_dir, producer="test")

    second_attempt = prepare_run_directory(run_dir, producer="test", overwrite=True)

    assert second_attempt != first_attempt
    assert not (run_dir / "stale.json").exists()


def test_finalize_stamps_artifacts_and_writes_manifest_last(tmp_path):
    run_dir = tmp_path / "run"
    attempt_id = prepare_run_directory(run_dir, producer="test")
    (run_dir / "summary.json").write_text('{"status": "ok"}', encoding="utf-8")
    (run_dir / "action_log.json").write_text('{"Alice": []}', encoding="utf-8")
    (run_dir / "events.jsonl").write_text('{"event": "step"}\n', encoding="utf-8")
    (run_dir / "config.yaml").write_text("model: test\n", encoding="utf-8")
    with (run_dir / "metrics.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["score"])
        writer.writeheader()
        writer.writerow({"score": 1})

    manifest = finalize_run_directory(
        run_dir,
        attempt_id=attempt_id,
        producer="test",
        status="completed",
    )

    assert json.loads((run_dir / "summary.json").read_text())["attempt_id"] == attempt_id
    assert json.loads((run_dir / "action_log.json").read_text())["_attempt_id"] == attempt_id
    assert json.loads((run_dir / "events.jsonl").read_text())["attempt_id"] == attempt_id
    assert yaml.safe_load((run_dir / "config.yaml").read_text())["attempt_id"] == attempt_id
    assert list(csv.DictReader((run_dir / "metrics.csv").open()))[0]["attempt_id"] == attempt_id
    assert (run_dir / COMPLETION_MARKER_FILE).read_text().strip() == attempt_id
    assert manifest["attempt_id"] == attempt_id
    assert validate_run_attempt(run_dir, attempt_id=attempt_id) == manifest


def test_failed_bundle_has_manifest_but_no_completion_marker(tmp_path):
    run_dir = tmp_path / "failed"
    attempt_id = prepare_run_directory(run_dir, producer="test")

    finalize_run_directory(
        run_dir,
        attempt_id=attempt_id,
        producer="test",
        status="failed",
    )

    assert (run_dir / ARTIFACT_MANIFEST_FILE).exists()
    assert not (run_dir / COMPLETION_MARKER_FILE).exists()
    validate_run_attempt(run_dir, attempt_id=attempt_id, require_completed=False)
    with pytest.raises(RunArtifactValidationError, match="not completed"):
        validate_run_attempt(run_dir, attempt_id=attempt_id)


def test_validation_rejects_artifact_modified_after_manifest(tmp_path):
    run_dir = tmp_path / "tampered"
    attempt_id = prepare_run_directory(run_dir, producer="test")
    artifact = run_dir / "summary.json"
    artifact.write_text('{"status": "ok"}', encoding="utf-8")
    finalize_run_directory(
        run_dir,
        attempt_id=attempt_id,
        producer="test",
        status="completed",
    )
    artifact.write_text('{"status": "changed"}', encoding="utf-8")

    with pytest.raises(RunArtifactValidationError, match="checksum mismatch"):
        validate_run_attempt(run_dir, attempt_id=attempt_id)
