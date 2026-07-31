from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable

from benchmarks.minecraft.matrix_report import generate_matrix_report
from benchmarks.minecraft.matrix_spec import (
    MATRIX_RUN_COUNT,
    MatrixRunSpec,
    MatrixSpec,
    load_matrix_spec,
    matrix_spec_to_dict,
    validate_matrix_spec,
)
from benchmarks.minecraft.matrix_validation import validate_matrix_run
from benchmarks.minecraft.world_snapshot import (
    RestoredWorld,
    WorldSnapshotDescriptor,
    restore_world_snapshot,
)


MatrixExecutor = Callable[..., str | Path]


class MatrixRunnerError(RuntimeError):
    pass


def run_finalized_matrix(
    premanifest_path: str | Path,
    matrix_dir: str | Path,
    *,
    executor: MatrixExecutor,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Execute a finalized matrix sequentially against independently restored worlds."""
    if not callable(executor):
        raise TypeError("executor is required and must be callable")
    source = Path(premanifest_path)
    root = Path(matrix_dir)
    if root.exists() and any(root.iterdir()):
        raise MatrixRunnerError(f"matrix output directory is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    spec = _verified_finalized_spec(source, repo_root=repo_root)
    _verify_executor_identity(executor, spec)
    copied_premanifest = root / "matrix_premanifest.json"
    temporary = copied_premanifest.with_suffix(copied_premanifest.suffix + ".tmp")
    shutil.copyfile(source, temporary)
    os.replace(temporary, copied_premanifest)
    try:
        copied_premanifest.chmod(0o444)
    except OSError:
        pass

    records: list[dict[str, Any]] = []
    failure_reason: str | None = None
    failure_run: str | None = None
    for run in spec.runs:
        if failure_reason is not None:
            records.append(_skipped_record(run, failure_reason))
            continue
        try:
            current = _verified_finalized_spec(source, repo_root=repo_root)
            if current != spec:
                raise MatrixRunnerError("finalized premanifest changed during matrix execution")
            run_root = root / "work" / run.run_id
            restored = restore_world_snapshot(
                _snapshot_descriptor(run, repo_root), run_root / "world"
            )
            bundle = Path(executor(run=run, restored_world=restored, output_dir=run_root))
            validation_path = root / "validations" / run.run_id / "matrix_run_validation.json"
            record = validate_matrix_run(
                bundle,
                output_path=validation_path,
                tolerance=run.target_tolerance,
                expected_target=run.evaluation_target.as_dict(),
                expected_completion_policy=run.expected_completion_policy,
                expected_completion_semantics=run.expected_completion_semantics,
                expected_position_convention=run.position_convention,
                expected_seed_contract={
                    "seed": run.seed,
                    "requested_scopes": list(run.seed_scopes.requested),
                },
            )
            record = _enrich_record(record, run, restored, bundle)
            _write_json(validation_path, record)
            records.append(record)
            if record.get("passed") is not True:
                failure_reason = "run_validation_failed"
                failure_run = run.run_id
        except Exception as exc:
            failure_reason = _safe_failure_reason(exc)
            failure_run = run.run_id
            records.append(_failed_record(run, failure_reason))

    for record in records:
        validation_path = root / "validations" / record["run_name"] / "matrix_run_validation.json"
        if not validation_path.exists():
            _write_json(validation_path, record)

    validation_references = []
    for record in records:
        path = root / "validations" / record["run_name"] / "matrix_run_validation.json"
        if path.is_file():
            validation_references.append({
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256(path),
            })
    report = generate_matrix_report(
        root,
        records,
        expected_run_count=MATRIX_RUN_COUNT,
        matrix_id=spec.matrix_id,
        revision=spec.revision,
        premanifest_sha256=spec.premanifest_sha256,
        artifact_references={
            "premanifest": {
                "path": copied_premanifest.name,
                "sha256": _sha256(copied_premanifest),
            },
            "validations": validation_references,
        },
        failure={
            "reason": failure_reason or "matrix_gate_failed",
            "run": failure_run,
            "skipped_count": sum(item.get("status") == "skipped" for item in records),
        },
    )
    return report


def _verified_finalized_spec(path: Path, *, repo_root: str | Path | None) -> MatrixSpec:
    spec = load_matrix_spec(path, repo_root=repo_root)
    if spec.lifecycle_state != "finalized" or spec.premanifest_sha256 is None:
        raise MatrixRunnerError("runner requires a finalized premanifest")
    return validate_matrix_spec(spec, repo_root=repo_root)


def _snapshot_descriptor(run: MatrixRunSpec, repo_root: str | Path | None) -> WorldSnapshotDescriptor:
    path = Path(run.snapshot_path)
    if not path.is_absolute():
        root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
        path = root / path
    return WorldSnapshotDescriptor(run.baseline_id, path, run.snapshot_sha256)


def _verify_executor_identity(executor: MatrixExecutor, spec: MatrixSpec) -> None:
    identity = getattr(executor, "matrix_identity", None)
    expected = {
        "runtime": vars(spec.runtime),
        "model": vars(spec.model),
        "generation": vars(spec.generation),
    }
    if identity != expected:
        raise MatrixRunnerError(
            "runtime adapter identity does not match the finalized premanifest"
        )


def _enrich_record(
    record: dict[str, Any], run: MatrixRunSpec, restored: RestoredWorld, bundle: Path
) -> dict[str, Any]:
    checks = record.get("checks", [])
    by_id = {item.get("id"): item.get("passed") for item in checks if isinstance(item, dict)}
    observed = record.get("observed", {})
    return {
        **record,
        "run_name": run.run_id,
        "matrix_index": run.order,
        "status": "passed" if record.get("passed") is True else "failed",
        "variant": run.variant,
        "seed": run.seed,
        "seed_scopes": {
            "requested": list(run.seed_scopes.requested),
            "supported": list(run.seed_scopes.supported),
            "applied": list(run.seed_scopes.applied),
        },
        "baseline": {"id": run.baseline_id, "sha256": run.snapshot_sha256},
        "target": run.evaluation_target.as_dict(),
        "position_convention": run.position_convention,
        "attempts": 1,
        "action": {
            "count": observed.get("action_count"),
            "failed_count": observed.get("failed_action_count"),
        },
        "diagnostics": {
            "available": by_id.get("diagnostics.schema_2") is True,
            "iterations_consistent": by_id.get("iterations.agent_consistent") is True,
            "agent_iteration_used": observed.get("agent_iteration", {}).get("used") if isinstance(observed.get("agent_iteration"), dict) else None,
            "judger_usage_available": observed.get("judger_iteration", {}).get("usage_available") if isinstance(observed.get("judger_iteration"), dict) else None,
            "judger_iteration_used": observed.get("judger_iteration", {}).get("used") if isinstance(observed.get("judger_iteration"), dict) else None,
        },
        "manifests": {**record.get("manifests", {}), "bundle": bundle.name},
        "cleanup": {"passed": by_id.get("cleanup.complete") is True},
        "safety": {
            "target_reusable": by_id.get("target.reusable") is True,
            "not_quarantined": by_id.get("target.not_quarantined") is True,
            "bundle_scan_clean": by_id.get("bundle_scan.clean") is True,
        },
        "restored_world": {
            "snapshot_sha256": restored.descriptor.archive_sha256,
            "tree_sha256": restored.tree_identity.manifest_sha256,
            "file_count": restored.tree_identity.file_count,
        },
    }


def _failed_record(run: MatrixRunSpec, reason: str) -> dict[str, Any]:
    return {
        "schema_version": 1, "record_type": "minecraft_matrix_run_validation",
        "matrix_index": run.order, "run_name": run.run_id, "attempt_id": "not-started",
        "status": "failed", "passed": False, "variant": run.variant, "seed": run.seed,
        "baseline": {"id": run.baseline_id, "sha256": run.snapshot_sha256},
        "target": run.evaluation_target.as_dict(),
        "position_convention": run.position_convention,
        "attempts": 1,
        "action": {}, "diagnostics": {"available": False}, "manifests": {},
        "cleanup": {"passed": False}, "safety": {"passed": False},
        "checks": [], "errors": [{"check": "runner", "message": reason}],
    }


def _skipped_record(run: MatrixRunSpec, reason: str) -> dict[str, Any]:
    record = _failed_record(run, reason)
    record.update(status="skipped", attempt_id="not-started", attempts=0)
    return record


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_failure_reason(exc: Exception) -> str:
    message = str(exc).strip()
    if not message or any(marker in message for marker in ("/", "\\", "=")):
        return type(exc).__name__
    return f"{type(exc).__name__}: {message}"
