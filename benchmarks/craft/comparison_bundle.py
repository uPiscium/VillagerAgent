from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from benchmarks.common.publish_bundle import (
    derive_public_bundle,
    sanitize_public_artifact,
    validate_public_bundle,
)
from benchmarks.common.run_artifacts import (
    finalize_run_directory,
    prepare_run_directory,
    read_attempt_id,
    validate_run_attempt,
)
from benchmarks.craft.config import repo_root
from benchmarks.experiment_provenance import finalize_provenance, write_provenance


SCHEMA_VERSION = "1.0.0"


class ComparisonBundleError(ValueError):
    """Raised when a comparison bundle configuration is invalid."""


def build_bundle(config_path: Path, output_dir: Path, *, overwrite: bool = False) -> Path:
    config, artifacts, source_dirs = _load_config(config_path)
    attempt_id = prepare_run_directory(
        output_dir,
        producer="benchmarks.craft.public_bundle",
        overwrite=overwrite,
    )
    finalized = False
    try:
        evidence_dir = output_dir / "evidence"
        for artifact, source in artifacts:
            destination = evidence_dir / artifact["destination"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix.lower() == ".json":
                payload = json.loads(source.read_text(encoding="utf-8"))
                _write_json(destination, _public_manifest_references(payload))
            else:
                shutil.copy2(source, destination)
            sanitize_public_artifact(destination)

        source_records = []
        for source_dir in source_dirs:
            source_attempt = json.loads((source_dir / "attempt.json").read_text(encoding="utf-8"))
            source_status = str(source_attempt.get("status") or "running")
            if source_status not in {"completed", "failed"}:
                raise ComparisonBundleError(f"Source run is not finalized: {source_dir}")
            source_manifest = validate_run_attempt(
                source_dir,
                attempt_id=read_attempt_id(source_dir),
                require_completed=source_status == "completed",
            )
            public_dir = output_dir / "runs" / source_dir.name
            validation = derive_public_bundle(source_dir, public_dir)
            source_records.append({
                "run_name": source_dir.name,
                "status": source_status,
                "source_attempt_id": source_manifest["attempt_id"],
                "source_manifest_sha256": _sha256(source_dir / "artifact_manifest.json"),
                "public_attempt_id": validation.attempt_id,
            })

        reports = [
            json.loads(source.read_text(encoding="utf-8"))
            for artifact, source in artifacts
            if artifact["kind"] == "comparison_report"
        ]
        resolved_config = {
            "schema_version": SCHEMA_VERSION,
            "bundle_id": config["bundle_id"],
            "benchmark": "craft",
            "classification": config["classification"],
            "source_run_count": len(source_records),
            "attempt_id": attempt_id,
        }
        _write_json(output_dir / "config.resolved.json", resolved_config)
        _write_json(output_dir / "source_index.json", {
            "schema_version": SCHEMA_VERSION,
            "sources": source_records,
        })
        source_statuses = dict(sorted(Counter(record["status"] for record in source_records).items()))
        _write_json(output_dir / "publication_source.json", {
            "schema_version": SCHEMA_VERSION,
            "source_run_statuses": source_statuses,
        })
        _write_json(output_dir / "summary.json", {
            **resolved_config,
            "status": "completed",
            "comparison_count": len(reports),
            "source_run_statuses": source_statuses,
            "performance_claim_eligible": all(
                report["claim_gates"]["performance_claim"]["eligible"] for report in reports
            ),
            "granted_claims": {
                report["comparison_id"]: report["granted_claim"] for report in reports
            },
        })
        _write_metrics(output_dir / "metrics.csv", reports)
        write_provenance(
            output_dir,
            benchmark="craft",
            command=[
                sys.executable,
                "-m",
                "benchmarks.craft.comparison_bundle",
                "--config",
                str(config_path),
                "--output",
                str(output_dir),
            ],
            resolved_config=resolved_config,
            environment_notes=(
                "sanitized_derivative=true; "
                f"bundle_id={config['bundle_id']}; full_comparison=true"
            ),
        )
        finalize_provenance(output_dir, status="success")
        sanitize_public_artifact(output_dir / "command.txt")
        sanitize_public_artifact(output_dir / "provenance.json")
        finalize_run_directory(
            output_dir,
            attempt_id=attempt_id,
            producer="benchmarks.craft.public_bundle",
            status="completed",
            stamp_nested=False,
        )
        validate_public_bundle(output_dir)
        finalized = True
    finally:
        if not finalized:
            if (output_dir / "provenance.json").exists():
                finalize_provenance(output_dir, status="failure")
            finalize_run_directory(
                output_dir,
                attempt_id=attempt_id,
                producer="benchmarks.craft.public_bundle",
                status="failed",
                stamp_nested=False,
            )
    return output_dir


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a public CRAFT comparison evidence bundle.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output = build_bundle(Path(args.config), Path(args.output), overwrite=args.overwrite)
    print(f"Wrote public comparison bundle: {output}")
    return 0


def _load_config(
    config_path: Path,
) -> tuple[dict[str, Any], list[tuple[dict[str, str], Path]], list[Path]]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ComparisonBundleError(f"schema_version must be {SCHEMA_VERSION!r}.")
    if payload.get("benchmark") != "craft" or payload.get("classification") != "integration_validation":
        raise ComparisonBundleError("Bundle must be CRAFT integration-validation evidence.")
    if not isinstance(payload.get("bundle_id"), str) or not payload["bundle_id"]:
        raise ComparisonBundleError("bundle_id must be a non-empty string.")

    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ComparisonBundleError("artifacts must be a non-empty array.")
    resolved_artifacts = []
    inputs = []
    destinations = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not all(
            isinstance(artifact.get(key), str) and artifact[key]
            for key in ("kind", "source", "destination")
        ):
            raise ComparisonBundleError("Each artifact requires kind, source, and destination strings.")
        destination = Path(artifact["destination"])
        if destination.is_absolute() or ".." in destination.parts or destination in destinations:
            raise ComparisonBundleError(f"Unsafe or duplicate artifact destination: {destination}")
        destinations.add(destination)
        source = _repo_path(artifact["source"])
        if not source.is_file() or source.is_symlink():
            raise ComparisonBundleError(f"Evidence source must be a regular file: {source}")
        resolved_artifacts.append((artifact, source))
        if artifact["kind"] == "comparison_input":
            inputs.append(json.loads(source.read_text(encoding="utf-8")))
    if not inputs or not any(artifact["kind"] == "comparison_report" for artifact in artifacts):
        raise ComparisonBundleError("At least one comparison input and report are required.")

    source_dirs = set()
    for comparison in inputs:
        for observation in comparison.get("observations", []):
            manifest_value = observation.get("run_manifest")
            if not isinstance(manifest_value, str) or not manifest_value:
                raise ComparisonBundleError("Every comparison observation requires a run_manifest.")
            manifest = _repo_path(manifest_value)
            if manifest.name != "artifact_manifest.json" or not manifest.is_file():
                raise ComparisonBundleError(f"Invalid source run manifest: {manifest}")
            source_dirs.add(manifest.parent.resolve())
    additional_manifests = payload.get("additional_run_manifests", [])
    if not isinstance(additional_manifests, list) or any(
        not isinstance(value, str) or not value for value in additional_manifests
    ):
        raise ComparisonBundleError("additional_run_manifests must be a list of non-empty strings.")
    for manifest_value in additional_manifests:
        manifest = _repo_path(manifest_value)
        if manifest.name != "artifact_manifest.json" or not manifest.is_file():
            raise ComparisonBundleError(f"Invalid supplementary run manifest: {manifest}")
        source_dirs.add(manifest.parent.resolve())
    return payload, resolved_artifacts, sorted(source_dirs)


def _repo_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = repo_root() / path
    resolved = path.resolve()
    if not resolved.is_relative_to(repo_root().resolve()):
        raise ComparisonBundleError(f"Source path must remain inside the repository: {value}")
    return resolved


def _write_metrics(path: Path, reports: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "comparison_id",
            "matched_pair_count",
            "excluded_pair_count",
            "estimate",
            "adjusted_ci_lower",
            "adjusted_ci_upper",
            "granted_claim",
            "performance_claim_eligible",
        ])
        writer.writeheader()
        for report in reports:
            interval = report["effect"]["confidence_interval"]
            writer.writerow({
                "comparison_id": report["comparison_id"],
                "matched_pair_count": report["pairing"]["matched_pair_count"],
                "excluded_pair_count": report["pairing"]["excluded_pair_count"],
                "estimate": report["effect"]["estimate"],
                "adjusted_ci_lower": interval["lower"],
                "adjusted_ci_upper": interval["upper"],
                "granted_claim": report["granted_claim"],
                "performance_claim_eligible": report["claim_gates"]["performance_claim"]["eligible"],
            })


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _public_manifest_references(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _public_manifest_references(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_public_manifest_references(item) for item in value]
    if isinstance(value, str):
        path = Path(value)
        if path.is_absolute() and path.name == "artifact_manifest.json":
            try:
                path.resolve().relative_to(repo_root().resolve())
            except ValueError:
                pass
            else:
                return str(Path("runs") / path.parent.name / path.name)
        if (
            len(path.parts) >= 4
            and path.parts[:2] == ("result", "craft")
            and path.name == "artifact_manifest.json"
        ):
            return str(Path("runs") / Path(*path.parts[2:]))
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
