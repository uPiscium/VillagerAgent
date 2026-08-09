"""Admission-controlled entry point for approved Minecraft production matrices."""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.minecraft.approved_experiment import (
    ApprovedExperimentError,
    ResolvedExperiment,
    get_approved_experiment,
    resolve_approved_experiment,
)
from benchmarks.minecraft.docker_runtime import DockerMatrixExecutor, pinned_runtime_identity
from benchmarks.minecraft.matrix_runner import run_finalized_matrix
from model.ollama_config import normalize_ollama_api_base
from env.runtime_execution import RuntimeExecution


class ProductionAdmissionError(RuntimeError):
    """Production did not pass every pre-execution approval check."""


@dataclass(frozen=True)
class ProductionAdmission:
    resolved: ResolvedExperiment
    execution_worktree: Path
    execution: RuntimeExecution
    output: Path
    endpoint: str


def admit_approved_experiment(
    approved_experiment: str,
    execution_worktree: str | Path,
    output: str | Path,
    *,
    registry_dir: str | Path | None = None,
) -> ProductionAdmission:
    """Resolve all approved inputs before an executor can be registered or called."""
    destination = Path(output).expanduser()
    if not destination.is_absolute():
        raise ProductionAdmissionError("production output must be an absolute path")
    if destination.exists() or destination.is_symlink():
        raise ProductionAdmissionError("production output must not already exist")
    supplied_execution = Path(execution_worktree).expanduser()
    execution = Path(os.path.abspath(supplied_execution))
    try:
        record = get_approved_experiment(approved_experiment, registry_dir)
    except ApprovedExperimentError as exc:
        raise ProductionAdmissionError("approved experiment registry validation failed") from exc

    configured_endpoint = os.environ.get("VILLAGER_MINECRAFT_MODEL_API_BASE")
    if not configured_endpoint:
        raise ProductionAdmissionError("approved model endpoint is not configured")
    try:
        configured = normalize_ollama_api_base(configured_endpoint)
        approved = normalize_ollama_api_base(record.model_endpoint)
    except ValueError as exc:
        raise ProductionAdmissionError("configured or approved model endpoint is invalid") from exc
    if configured != approved:
        raise ProductionAdmissionError("configured model endpoint does not match the approval")
    expected_model = record.expected["model"]
    for field, environment_name in (
        ("provider", "VILLAGER_MINECRAFT_MODEL_PROVIDER"),
        ("name", "VILLAGER_MINECRAFT_MODEL_NAME"),
        ("digest", "VILLAGER_MINECRAFT_MODEL_DIGEST"),
    ):
        if os.environ.get(environment_name) != expected_model[field]:
            raise ProductionAdmissionError("configured model identity does not match the approval")
    key_environment = os.environ.get("VILLAGER_MINECRAFT_MODEL_API_KEY_ENV")
    if (
        not key_environment
        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_environment) is None
        or not os.environ.get(key_environment)
    ):
        raise ProductionAdmissionError("approved model credential environment is unavailable")
    try:
        resolved_execution = RuntimeExecution.resolve(execution)
        resolved_execution.verify()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProductionAdmissionError("execution worktree runtime validation failed") from exc
    if pinned_runtime_identity(resolved_execution) != dict(record.runtime_identity):
        raise ProductionAdmissionError("control-plane runtime implementation does not match the approval")
    verified_root = resolved_execution.root

    try:
        resolved = resolve_approved_experiment(
            approved_experiment,
            destination / "admission",
            verified_root,
            registry_dir=registry_dir,
        )
    except (ApprovedExperimentError, OSError) as exc:
        raise ProductionAdmissionError("approved experiment resolution failed") from exc
    return ProductionAdmission(
        resolved=resolved,
        execution_worktree=verified_root,
        execution=resolved_execution,
        output=destination.resolve(),
        endpoint=approved,
    )


def run_approved_production(
    approved_experiment: str,
    execution_worktree: str | Path,
    output: str | Path,
    *,
    registry_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Admit an approved bundle, then execute exactly its finalized matrix."""
    admission = admit_approved_experiment(
        approved_experiment,
        execution_worktree,
        output,
        registry_dir=registry_dir,
    )
    spec = admission.resolved.spec
    executor = DockerMatrixExecutor(
        {
            "runtime": vars(spec.runtime),
            "model": vars(spec.model),
            "generation": vars(spec.generation),
        },
        execution_root=admission.execution,
    )
    report = run_finalized_matrix(
        admission.resolved.premanifest_path,
        admission.output / "matrix",
        executor=executor,
        repo_root=admission.execution_worktree,
    )
    return {
        "status": "passed" if report.get("gate_passed") is True else "failed",
        "gate_passed": report.get("gate_passed") is True,
        "approved_experiment": admission.resolved.record.experiment_id,
        "approved_revision": admission.resolved.record.approved_source_revision,
        "canonical_premanifest_identity": (
            admission.resolved.record.canonical_premanifest_identity
        ),
        "matrix": report,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a repository-approved Minecraft production matrix.")
    parser.add_argument("--approved-experiment", required=True)
    parser.add_argument("--execution-worktree", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        summary = run_approved_production(
            args.approved_experiment,
            args.execution_worktree,
            args.output,
        )
    except (ProductionAdmissionError, ApprovedExperimentError, OSError, ValueError):
        print(json.dumps({"status": "admission_failed", "gate_passed": False}, sort_keys=True))
        return 2
    except Exception:
        print(json.dumps({"status": "execution_failed", "gate_passed": False}, sort_keys=True))
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
