from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any
import uuid

import yaml


ATTEMPT_FILE = "attempt.json"
ARTIFACT_MANIFEST_FILE = "artifact_manifest.json"
COMPLETION_MARKER_FILE = "_COMPLETED"


class RunDirectoryExistsError(FileExistsError):
    """Raised when a benchmark would reuse a non-empty run directory."""


class RunArtifactValidationError(ValueError):
    """Raised when a run bundle does not belong to the expected attempt."""


def prepare_run_directory(
    run_dir: Path,
    *,
    producer: str,
    overwrite: bool = False,
) -> str:
    if run_dir.is_symlink():
        raise RunDirectoryExistsError(f"Benchmark run directory must not be a symlink: {run_dir}")
    if run_dir.exists():
        if any(run_dir.iterdir()) and not overwrite:
            raise RunDirectoryExistsError(
                f"Benchmark run directory is not empty: {run_dir}. Use explicit overwrite mode to replace it."
            )
        if overwrite:
            shutil.rmtree(run_dir)
        else:
            run_dir.rmdir()
    run_dir.mkdir(parents=True)
    attempt_id = uuid.uuid4().hex
    _write_json_atomic(run_dir / ATTEMPT_FILE, {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "producer": producer,
        "status": "running",
    })
    return attempt_id


def finalize_run_directory(
    run_dir: Path,
    *,
    attempt_id: str,
    producer: str,
    status: str,
    stamp_nested: bool = True,
) -> dict[str, Any]:
    if status not in {"completed", "failed"}:
        raise ValueError(f"Unsupported run artifact status: {status}")
    _validate_attempt_file(run_dir, attempt_id)
    (run_dir / ARTIFACT_MANIFEST_FILE).unlink(missing_ok=True)
    (run_dir / COMPLETION_MARKER_FILE).unlink(missing_ok=True)

    artifact_paths = _stamp_artifacts(run_dir, attempt_id=attempt_id, nested=stamp_nested)
    _write_json_atomic(run_dir / ATTEMPT_FILE, {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "producer": producer,
        "status": status,
    })
    if run_dir / ATTEMPT_FILE not in artifact_paths:
        artifact_paths.append(run_dir / ATTEMPT_FILE)

    manifest = {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "producer": producer,
        "status": status,
        "artifacts": [
            {
                "path": str(path.relative_to(run_dir)),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in sorted(set(artifact_paths))
            if path.exists() and path.name not in {ARTIFACT_MANIFEST_FILE, COMPLETION_MARKER_FILE}
        ],
    }
    _write_json_atomic(run_dir / ARTIFACT_MANIFEST_FILE, manifest)
    if status == "completed":
        _write_text_atomic(run_dir / COMPLETION_MARKER_FILE, attempt_id + "\n")
    return manifest


def read_attempt_id(run_dir: Path) -> str:
    attempt = _read_json(run_dir / ATTEMPT_FILE)
    attempt_id = str(attempt.get("attempt_id") or "")
    if not attempt_id:
        raise RunArtifactValidationError(f"Missing attempt ID in {run_dir / ATTEMPT_FILE}")
    return attempt_id


def validate_run_attempt(
    run_dir: Path,
    *,
    attempt_id: str,
    require_completed: bool = True,
) -> dict[str, Any]:
    _validate_attempt_file(run_dir, attempt_id)
    manifest = _read_json(run_dir / ARTIFACT_MANIFEST_FILE)
    if manifest.get("attempt_id") != attempt_id:
        raise RunArtifactValidationError(
            f"Artifact manifest attempt mismatch in {run_dir}: expected {attempt_id}, got {manifest.get('attempt_id')}"
        )
    for artifact in manifest.get("artifacts", []):
        if not isinstance(artifact, dict) or not artifact.get("path"):
            raise RunArtifactValidationError(f"Invalid artifact manifest entry in {run_dir}")
        artifact_path = run_dir / str(artifact["path"])
        if not artifact_path.exists():
            raise RunArtifactValidationError(f"Missing manifested artifact: {artifact_path}")
        if artifact_path.stat().st_size != artifact.get("size") or _sha256(artifact_path) != artifact.get("sha256"):
            raise RunArtifactValidationError(f"Artifact checksum mismatch: {artifact_path}")
    if require_completed:
        marker = run_dir / COMPLETION_MARKER_FILE
        if manifest.get("status") != "completed" or not marker.exists():
            raise RunArtifactValidationError(f"Run bundle is not completed: {run_dir}")
        if marker.read_text(encoding="utf-8").strip() != attempt_id:
            raise RunArtifactValidationError(f"Completion marker attempt mismatch in {run_dir}")
    return manifest


def _stamp_artifacts(run_dir: Path, *, attempt_id: str, nested: bool) -> list[Path]:
    candidates = run_dir.rglob("*") if nested else run_dir.glob("*")
    paths = [path for path in candidates if path.is_file()]
    for path in paths:
        if path.name in {ARTIFACT_MANIFEST_FILE, COMPLETION_MARKER_FILE, ATTEMPT_FILE}:
            continue
        if path.suffix == ".json":
            _stamp_json(path, attempt_id)
        elif path.suffix == ".jsonl":
            _stamp_jsonl(path, attempt_id)
        elif path.suffix == ".csv":
            _stamp_csv(path, attempt_id)
        elif path.suffix in {".yaml", ".yml"}:
            _stamp_yaml(path, attempt_id)
    return paths


def _stamp_json(path: Path, attempt_id: str) -> None:
    try:
        payload = _read_json(path)
    except (json.JSONDecodeError, RunArtifactValidationError):
        return
    key = "_attempt_id" if path.name == "action_log.json" else "attempt_id"
    payload[key] = attempt_id
    _write_json_atomic(path, payload)


def _stamp_jsonl(path: Path, attempt_id: str) -> None:
    output = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            output.append(line)
            continue
        if isinstance(payload, dict):
            payload["attempt_id"] = attempt_id
            output.append(json.dumps(payload, ensure_ascii=False))
        else:
            output.append(line)
    _write_text_atomic(path, "\n".join(output) + ("\n" if output else ""))


def _stamp_csv(path: Path, attempt_id: str) -> None:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return
        rows = list(reader)
        fieldnames = list(reader.fieldnames)
    if "attempt_id" not in fieldnames:
        fieldnames.append("attempt_id")
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            row["attempt_id"] = attempt_id
            writer.writerow(row)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary_path, path)


def _stamp_yaml(path: Path, attempt_id: str) -> None:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return
    if not isinstance(payload, dict):
        return
    payload["attempt_id"] = attempt_id
    _write_text_atomic(path, yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))


def _validate_attempt_file(run_dir: Path, attempt_id: str) -> None:
    observed = read_attempt_id(run_dir)
    if observed != attempt_id:
        raise RunArtifactValidationError(
            f"Run attempt mismatch in {run_dir}: expected {attempt_id}, got {observed}"
        )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RunArtifactValidationError(f"Missing run artifact: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RunArtifactValidationError(f"Expected JSON object in run artifact: {path}")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_text_atomic(path: Path, content: str) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary_path, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
