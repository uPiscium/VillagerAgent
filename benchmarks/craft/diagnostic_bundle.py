from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

from benchmarks.common.run_artifacts import finalize_run_directory, prepare_run_directory
from benchmarks.craft.config import repo_root
from benchmarks.experiment_provenance import file_identity, finalize_provenance, write_provenance


SCHEMA_VERSION = "1.0.0"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DiagnosticBundleError(ValueError):
    """Raised when a diagnostic bundle configuration is invalid."""


def build_bundle(config_path: Path, output_dir: Path, *, overwrite: bool = False) -> Path:
    config, resolved_artifacts = _load_config(config_path)
    attempt_id = prepare_run_directory(
        output_dir,
        producer="benchmarks.craft.diagnostic_bundle",
        overwrite=overwrite,
    )
    finalized = False
    try:
        effective_config = {
            **config,
            "config_path": str(config_path.resolve()),
            "attempt_id": attempt_id,
        }
        write_provenance(
            output_dir,
            benchmark="craft",
            command=[
                sys.executable,
                "-m",
                "benchmarks.craft.diagnostic_bundle",
                "--config",
                str(config_path),
                "--output",
                str(output_dir),
                *(["--overwrite"] if overwrite else []),
            ],
            resolved_config=effective_config,
            environment_notes="diagnostic_only=true; issue=291",
            assets=[
                file_identity(source, name=f"evidence_{index}", kind=artifact["kind"])
                for index, (artifact, source) in enumerate(resolved_artifacts)
            ],
        )
        for artifact, source in resolved_artifacts:
            destination = output_dir / artifact["destination"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())

        source_releases = {
            "schema_version": SCHEMA_VERSION,
            "classification": "diagnostic",
            "releases": config["source_releases"],
        }
        _write_json(output_dir / "source_releases.json", source_releases)
        summary, metrics = _summarize(config, resolved_artifacts)
        _write_json(output_dir / "summary.json", summary)
        _write_metrics(output_dir / "metrics.csv", metrics)
        finalize_provenance(output_dir, status="success")
        finalize_run_directory(
            output_dir,
            attempt_id=attempt_id,
            producer="benchmarks.craft.diagnostic_bundle",
            status="completed",
        )
        finalized = True
    finally:
        if not finalized:
            finalize_provenance(output_dir, status="failure")
            finalize_run_directory(
                output_dir,
                attempt_id=attempt_id,
                producer="benchmarks.craft.diagnostic_bundle",
                status="failed",
            )
    return output_dir


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a managed CRAFT diagnostic analysis bundle.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output = build_bundle(Path(args.config), Path(args.output), overwrite=args.overwrite)
    print(f"Wrote managed diagnostic bundle: {output}")
    return 0


def _load_config(config_path: Path) -> tuple[dict[str, Any], list[tuple[dict[str, str], Path]]]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise DiagnosticBundleError(f"schema_version must be {SCHEMA_VERSION!r}.")
    if payload.get("benchmark") != "craft" or payload.get("classification") != "diagnostic":
        raise DiagnosticBundleError("Bundle must be a diagnostic CRAFT bundle.")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise DiagnosticBundleError("artifacts must be a non-empty array.")
    resolved_artifacts = []
    destinations = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise DiagnosticBundleError("Each artifact must be an object.")
        source_value = artifact.get("source")
        destination_value = artifact.get("destination")
        kind = artifact.get("kind")
        if not all(isinstance(value, str) and value for value in (source_value, destination_value, kind)):
            raise DiagnosticBundleError("Each artifact requires source, destination, and kind strings.")
        destination = Path(destination_value)
        if destination.is_absolute() or ".." in destination.parts or destination_value in destinations:
            raise DiagnosticBundleError(f"Unsafe or duplicate artifact destination: {destination_value}")
        destinations.add(destination_value)
        source = Path(source_value)
        if not source.is_absolute():
            source = repo_root() / source
        if not source.is_file() or source.is_symlink():
            raise DiagnosticBundleError(f"Evidence source must be a regular file: {source}")
        resolved_artifacts.append((artifact, source.resolve()))
    releases = payload.get("source_releases")
    if not isinstance(releases, list) or not releases:
        raise DiagnosticBundleError("source_releases must be a non-empty array.")
    for release in releases:
        if not isinstance(release, dict) or not all(
            isinstance(release.get(key), str) and release[key]
            for key in ("condition", "archive_url", "metadata_url", "archive_sha256", "manifest_sha256")
        ):
            raise DiagnosticBundleError("Each source release requires URLs and archive/manifest checksums.")
        if not release["archive_url"].startswith("https://") or not release["metadata_url"].startswith("https://"):
            raise DiagnosticBundleError("Source release URLs must use HTTPS.")
        if not _SHA256.fullmatch(release["archive_sha256"]) or not _SHA256.fullmatch(release["manifest_sha256"]):
            raise DiagnosticBundleError("Source release checksums must be lowercase SHA-256 values.")
    return payload, resolved_artifacts


def _summarize(
    config: dict[str, Any],
    artifacts: list[tuple[dict[str, str], Path]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = []
    comparison_count = 0
    probe_count = 0
    for artifact, source in artifacts:
        if artifact["kind"] == "comparison_report":
            report = json.loads(source.read_text(encoding="utf-8"))
            comparison_count += 1
            rows.append({
                "evidence_type": "paired_comparison",
                "evidence_id": report["comparison_id"],
                "matched_pair_count": report["pairing"]["matched_pair_count"],
                "granted_claim": report["granted_claim"],
                "retrieved_node_count": "",
                "retrieval_used_in_top_action_count": "",
                "retrieval_changed_top_action_count": "",
            })
        elif artifact["kind"] == "retrieval_probe_output":
            report = json.loads(source.read_text(encoding="utf-8"))
            probe_count += 1
            rows.append({
                "evidence_type": "retrieval_probe",
                "evidence_id": report["probe_id"],
                "matched_pair_count": "",
                "granted_claim": report["classification"],
                "retrieved_node_count": report["retrieval"]["retrieved_node_count"],
                "retrieval_used_in_top_action_count": report["retrieval"]["retrieval_used_in_top_action_count"],
                "retrieval_changed_top_action_count": report["retrieval"]["retrieval_changed_top_action_count"],
            })
    summary = {
        "schema_version": SCHEMA_VERSION,
        "bundle_id": config["bundle_id"],
        "benchmark": "craft",
        "classification": "diagnostic",
        "performance_claim_eligible": False,
        "comparison_report_count": comparison_count,
        "retrieval_probe_count": probe_count,
        "source_release_count": len(config["source_releases"]),
        "status": "completed",
    }
    return summary, rows


def _write_metrics(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "evidence_type",
        "evidence_id",
        "matched_pair_count",
        "granted_claim",
        "retrieved_node_count",
        "retrieval_used_in_top_action_count",
        "retrieval_changed_top_action_count",
    ]
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
