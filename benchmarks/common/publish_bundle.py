from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

import yaml
import certifi

from benchmarks.common.run_artifacts import (
    ARTIFACT_MANIFEST_FILE,
    COMPLETION_MARKER_FILE,
    RunArtifactValidationError,
    finalize_run_directory,
    prepare_run_directory,
    read_attempt_id,
    validate_run_attempt,
)
from benchmarks.experiment_provenance import finalize_provenance, write_provenance


PUBLICATION_SCHEMA_VERSION = 1
DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_BUNDLE_BYTES = 500 * 1024 * 1024
_ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_PUBLIC_SUFFIXES = {".csv", ".json", ".jsonl", ".md", ".txt", ".yaml", ".yml"}
_FORBIDDEN_PATH_PARTS = {
    ".git",
    ".runtime",
    "__pycache__",
    "evaluator",
    "hidden",
    "private",
    "raw",
    "runtime",
}
_FORBIDDEN_DATA_KEYS = {
    "evaluator_state",
    "evaluator_progress",
    "full_graph_state",
    "hidden_evaluator_state",
    "hidden_state",
    "builder_prompt",
    "internal_thinking",
    "oracle_plan",
    "oracle_moves",
    "private_reasoning",
    "private_observation",
    "private_observations",
    "private_state_agents",
    "private_view",
    "raw_markdown",
    "raw_stdout",
    "stderr",
    "stdout",
    "target_blueprint",
    "target_director_views",
    "target_structure",
}
_CREDENTIAL_KEY = re.compile(
    r"(?:api[_-]?keys?|api[_-]?key[_-]?list|authorization|client[_-]?secrets?|credentials?|passwords?|passwds?|private[_-]?keys?|refresh[_-]?tokens?|secrets?|tokens?)$",
    re.IGNORECASE,
)
_CREDENTIAL_SOURCE_KEY = re.compile(r"(?:_env|credential_sources?)$", re.IGNORECASE)
_NON_CREDENTIAL_TOKEN_KEYS = {"max_token", "max_tokens"}
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
)
_INLINE_CREDENTIAL = re.compile(
    r"(?i)((?:--api[-_]?key|--authorization|--password|--secret|--token)(?:=|\s+))(?!\[REDACTED\])\S+"
)
_PAPER_RESULT = re.compile(r"<!--\s*paper-result:\s*([A-Za-z0-9_.-]+)\s*-->")
_BENCHMARK_RESULT = re.compile(r"<!--\s*benchmark-result:\s*([A-Za-z0-9_.-]+)\s*-->")
_HISTORICAL_RESULT = re.compile(r"<!--\s*historical-result:\s*([A-Za-z0-9_.-]+)\s*-->")
_REPORTED_EVALUATION = re.compile(
    r"Observed common-report aggregate:|^## Aggregate Results|For the latest verified .*run, the generated compact summary was:",
    re.IGNORECASE | re.MULTILINE,
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_STABLE_URL_PREFIXES = ("https://", "doi:", "ipfs://", "s3://", "gs://", "az://")
_LEGACY_UNARCHIVED_DECLARATION_PATHS = {
    "craft-clarification-policy-evaluations": "benchmarks/craft/CLARIFICATION_POLICY_FINDINGS.md",
    "craft-dual-dag-ablation-diagnostic": "benchmarks/craft/DUAL_DAG_ABLATION_FINDINGS.md",
    "craft-qwen-final-diagnostic": "benchmarks/craft/README.md",
    "craft-v5-action-selection-diagnostic": "benchmarks/craft/V5_ACTION_SELECTION_DIAGNOSTICS.md",
    "cwah-bounded-baseline-diagnostic": "docs/benchmarks/cwah_real_baseline.md",
    "cwah-goal-policy-diagnostics": "docs/benchmarks/cwah_goal_policy.md",
}


class PublicBundleValidationError(ValueError):
    """Raised when a managed run bundle is unsafe or incomplete for publication."""


class PublicationError(RuntimeError):
    """Raised when an immutable publisher cannot publish an archive."""


@dataclass(frozen=True)
class BundleValidation:
    benchmark: str
    attempt_id: str
    artifact_count: int
    total_bytes: int
    run_statuses: dict[str, int]
    source_manifest_sha256: str


@dataclass(frozen=True)
class ArchiveMetadata:
    schema_version: int
    benchmark: str
    attempt_id: str
    archive_name: str
    archive_sha256: str
    archive_size: int
    source_manifest: str
    source_manifest_sha256: str
    artifact_count: int
    total_uncompressed_bytes: int
    run_statuses: dict[str, int]


@dataclass(frozen=True)
class PublicationReference:
    schema_version: int
    publisher: str
    stable_id: str
    archive_url: str
    archive_sha256: str
    manifest_path: str


class Publisher(Protocol):
    def publish(
        self,
        archive_path: Path,
        metadata_path: Path,
        metadata: ArchiveMetadata,
    ) -> PublicationReference: ...


class LocalStagingPublisher:
    """Non-immutable filesystem staging for a later archival upload."""

    def __init__(self, root: Path, stable_id: str) -> None:
        self.root = root
        self.stable_id = stable_id

    def publish(
        self,
        archive_path: Path,
        metadata_path: Path,
        metadata: ArchiveMetadata,
    ) -> PublicationReference:
        _validate_local_staging_destination(self.root, self.stable_id)
        destination = self.root / self.stable_id
        if destination.exists():
            raise PublicationError(f"Immutable archive reference already exists: {destination}")
        destination.mkdir(parents=True)
        try:
            archive_destination = destination / archive_path.name
            metadata_destination = destination / metadata_path.name
            shutil.copyfile(archive_path, archive_destination)
            shutil.copyfile(metadata_path, metadata_destination)
        except Exception:
            shutil.rmtree(destination)
            raise
        return PublicationReference(
            schema_version=PUBLICATION_SCHEMA_VERSION,
            publisher="local-staging",
            stable_id=self.stable_id,
            archive_url=archive_destination.resolve().as_uri(),
            archive_sha256=metadata.archive_sha256,
            manifest_path=ARTIFACT_MANIFEST_FILE,
        )


# Compatibility for library callers; local output is deliberately not a public publisher.
LocalArchivePublisher = LocalStagingPublisher


class GitHubReleasePublisher:
    """Publisher using a new, dedicated GitHub release tag."""

    def __init__(
        self,
        repository: str,
        tag: str,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.repository = repository
        self.tag = tag
        self.runner = runner

    def publish(
        self,
        archive_path: Path,
        metadata_path: Path,
        metadata: ArchiveMetadata,
    ) -> PublicationReference:
        settings = self.runner(
            ["gh", "api", f"repos/{self.repository}/immutable-releases", "--jq", ".enabled"],
            capture_output=True,
            text=True,
            check=False,
        )
        if settings.returncode != 0 or settings.stdout.strip().lower() != "true":
            raise PublicationError(
                "GitHub repository must verifiably enable immutable releases before publication"
            )
        existing = self.runner(
            ["gh", "release", "view", self.tag, "--repo", self.repository],
            capture_output=True,
            text=True,
            check=False,
        )
        if existing.returncode == 0:
            raise PublicationError(f"GitHub release already exists; refusing to mutate it: {self.tag}")
        command = [
            "gh",
            "release",
            "create",
            self.tag,
            str(archive_path),
            str(metadata_path),
            "--repo",
            self.repository,
            "--title",
            self.tag,
            "--notes",
            f"Immutable sanitized benchmark bundle. SHA-256: {metadata.archive_sha256}",
            "--latest=false",
        ]
        created = self.runner(command, capture_output=True, text=True, check=False)
        if created.returncode != 0:
            raise PublicationError(created.stderr.strip() or "GitHub release creation failed")
        immutable = self.runner(
            [
                "gh",
                "api",
                f"repos/{self.repository}/releases/tags/{self.tag}",
                "--jq",
                ".immutable",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if immutable.returncode != 0 or immutable.stdout.strip().lower() != "true":
            raise PublicationError("Created GitHub release could not be verified as immutable")
        encoded_name = archive_path.name.replace(" ", "%20")
        return PublicationReference(
            schema_version=PUBLICATION_SCHEMA_VERSION,
            publisher="github-release",
            stable_id=f"{self.repository}@{self.tag}",
            archive_url=(
                f"https://github.com/{self.repository}/releases/download/{self.tag}/{encoded_name}"
            ),
            archive_sha256=metadata.archive_sha256,
            manifest_path=ARTIFACT_MANIFEST_FILE,
        )


def validate_public_bundle(
    bundle_dir: Path,
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_bundle_bytes: int = DEFAULT_MAX_BUNDLE_BYTES,
) -> BundleValidation:
    if bundle_dir.is_symlink():
        raise PublicBundleValidationError(f"Public bundle root must not be a symlink: {bundle_dir}")
    bundle_dir = bundle_dir.resolve()
    try:
        attempt_id = read_attempt_id(bundle_dir)
        attempt = json.loads((bundle_dir / "attempt.json").read_text(encoding="utf-8"))
        attempt_status = str(attempt.get("status") or "running")
        if attempt_status not in {"completed", "failed"}:
            raise PublicBundleValidationError("Public bundle contains an unfinished run attempt")
        manifest = validate_run_attempt(
            bundle_dir,
            attempt_id=attempt_id,
            require_completed=attempt_status == "completed",
        )
    except (OSError, json.JSONDecodeError, RunArtifactValidationError) as exc:
        raise PublicBundleValidationError(str(exc)) from exc

    producer = str(manifest.get("producer") or "")
    benchmark = _benchmark_from_producer(producer)
    if not benchmark:
        raise PublicBundleValidationError(
            "Bundle producer must identify CRAFT, C-WAH, or Minecraft/VillagerBench"
        )

    paths = sorted(path for path in bundle_dir.rglob("*") if path.is_file())
    total_bytes = 0
    for path in paths:
        relative = path.relative_to(bundle_dir)
        _validate_public_path(relative)
        size = path.stat().st_size
        if size > max_file_bytes:
            raise PublicBundleValidationError(
                f"Unexpected large file ({size} bytes, limit {max_file_bytes}): {relative}"
            )
        total_bytes += size
        if total_bytes > max_bundle_bytes:
            raise PublicBundleValidationError(
                f"Bundle exceeds public size limit of {max_bundle_bytes} bytes"
            )
        _scan_public_file(path, relative)

    present_names = {path.name for path in paths}
    if "provenance.json" not in present_names:
        raise PublicBundleValidationError("Public bundle must include sanitized provenance.json")
    if not ({"config.resolved.json", "config.resolved.yaml"} & present_names):
        raise PublicBundleValidationError("Public bundle must include a resolved non-secret config")

    statuses = _validate_nested_attempts(bundle_dir)
    if statuses.get("running", 0):
        raise PublicBundleValidationError("Public bundle contains an unfinished run attempt")
    publication_source = bundle_dir / "publication_source.json"
    if publication_source.exists():
        source_record = json.loads(publication_source.read_text(encoding="utf-8"))
        source_statuses = source_record.get("source_run_statuses")
        if not isinstance(source_statuses, dict) or any(
            status not in {"completed", "failed"}
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
            for status, count in source_statuses.items()
        ):
            raise PublicBundleValidationError("Derivative bundle has invalid source run accounting")
        statuses = source_statuses
    if not ({"summary.json", "matrix_summary.json", "verification.json"} & present_names):
        raise PublicBundleValidationError("Public bundle must include per-run or matrix status summaries")
    if not any(
        "metrics" in path.name or "report" in path.name or path.name == "verification.json"
        for path in paths
    ):
        raise PublicBundleValidationError("Public bundle must include metrics or aggregate report tables")

    manifest_path = bundle_dir / ARTIFACT_MANIFEST_FILE
    return BundleValidation(
        benchmark=benchmark,
        attempt_id=attempt_id,
        artifact_count=len(paths),
        total_bytes=total_bytes,
        run_statuses=dict(sorted(statuses.items())),
        source_manifest_sha256=_sha256(manifest_path),
    )


def derive_public_bundle(source_dir: Path, destination_dir: Path) -> BundleValidation:
    """Create a new managed, sanitized attempt linked to a #296 source manifest."""
    if source_dir.is_symlink():
        raise PublicBundleValidationError(f"Source bundle root must not be a symlink: {source_dir}")
    source_dir = source_dir.resolve()
    source_attempt_id = read_attempt_id(source_dir)
    source_attempt = json.loads((source_dir / "attempt.json").read_text(encoding="utf-8"))
    source_status = str(source_attempt.get("status") or "running")
    if source_status not in {"completed", "failed"}:
        raise PublicBundleValidationError("Only finalized source attempts can be sanitized")
    source_manifest = validate_run_attempt(
        source_dir,
        attempt_id=source_attempt_id,
        require_completed=source_status == "completed",
    )
    benchmark = _benchmark_from_producer(str(source_manifest.get("producer") or ""))
    if not benchmark:
        raise PublicBundleValidationError("Source producer is not a supported benchmark")

    attempt_id = prepare_run_directory(
        destination_dir,
        producer=f"benchmarks.{benchmark}.public_bundle",
    )
    try:
        for artifact in source_manifest["artifacts"]:
            relative = Path(str(artifact["path"]))
            if relative.name == "attempt.json" or _is_private_source_path(relative):
                continue
            target_relative = _derived_relative_path(relative)
            if target_relative.suffix.lower() not in _PUBLIC_SUFFIXES:
                continue
            target = destination_dir / target_relative
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_sanitized_artifact(source_dir / relative, target)

        source_manifest_path = source_dir / ARTIFACT_MANIFEST_FILE
        source_link = {
            "schema_version": PUBLICATION_SCHEMA_VERSION,
            "benchmark": benchmark,
            "source_attempt_id": source_attempt_id,
            "source_status": source_status,
            "source_producer": source_manifest.get("producer"),
            "source_manifest_sha256": _sha256(source_manifest_path),
            "source_run_statuses": dict(sorted(_validate_nested_attempts(source_dir).items())),
        }
        _write_json(destination_dir / "publication_source.json", source_link)
        write_provenance(
            destination_dir,
            benchmark=benchmark,
            command=[
                "python",
                "-m",
                "benchmarks.common.publish_bundle",
                "sanitize",
                "SOURCE_BUNDLE",
                "--output",
                "PUBLIC_BUNDLE",
            ],
            resolved_config={
                "publication_source": source_link,
                "excluded_classes": ["raw", "private", "hidden", "runtime"],
                "attempt_id": attempt_id,
            },
            environment_notes="sanitized_derivative=true",
        )
        finalize_provenance(destination_dir, status="success")
        finalize_run_directory(
            destination_dir,
            attempt_id=attempt_id,
            producer=f"benchmarks.{benchmark}.public_bundle",
            status="completed",
        )
    except BaseException:
        if (destination_dir / "provenance.json").exists():
            finalize_provenance(destination_dir, status="failure")
        finalize_run_directory(
            destination_dir,
            attempt_id=attempt_id,
            producer=f"benchmarks.{benchmark}.public_bundle",
            status="failed",
        )
        raise
    return validate_public_bundle(destination_dir)


def build_deterministic_archive(
    bundle_dir: Path,
    archive_path: Path,
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_bundle_bytes: int = DEFAULT_MAX_BUNDLE_BYTES,
) -> tuple[ArchiveMetadata, Path]:
    if bundle_dir.is_symlink():
        raise PublicBundleValidationError(f"Public bundle root must not be a symlink: {bundle_dir}")
    bundle_dir = bundle_dir.resolve()
    archive_path = archive_path.resolve()
    if archive_path.is_relative_to(bundle_dir):
        raise PublicationError("Archive output must be outside the source bundle")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive_path.with_suffix(archive_path.suffix + ".tmp")
    with tempfile.TemporaryDirectory(dir=archive_path.parent, prefix=".bundle-snapshot-") as temp_dir:
        snapshot = Path(temp_dir) / "bundle"
        shutil.copytree(bundle_dir, snapshot, symlinks=True)
        validation = validate_public_bundle(
            snapshot,
            max_file_bytes=max_file_bytes,
            max_bundle_bytes=max_bundle_bytes,
        )
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
            for path in sorted(item for item in snapshot.rglob("*") if item.is_file()):
                relative = path.relative_to(snapshot).as_posix()
                info = zipfile.ZipInfo(relative, date_time=_ARCHIVE_TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                output.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        temporary.replace(archive_path)

    metadata = ArchiveMetadata(
        schema_version=PUBLICATION_SCHEMA_VERSION,
        benchmark=validation.benchmark,
        attempt_id=validation.attempt_id,
        archive_name=archive_path.name,
        archive_sha256=_sha256(archive_path),
        archive_size=archive_path.stat().st_size,
        source_manifest=ARTIFACT_MANIFEST_FILE,
        source_manifest_sha256=validation.source_manifest_sha256,
        artifact_count=validation.artifact_count,
        total_uncompressed_bytes=validation.total_bytes,
        run_statuses=validation.run_statuses,
    )
    metadata_path = archive_path.with_suffix(archive_path.suffix + ".metadata.json")
    _write_json(metadata_path, asdict(metadata))
    return metadata, metadata_path


def publish_bundle(
    bundle_dir: Path,
    archive_path: Path,
    publisher: Publisher,
) -> PublicationReference:
    metadata, metadata_path = build_deterministic_archive(bundle_dir, archive_path)
    reference = publisher.publish(archive_path.resolve(), metadata_path, metadata)
    if reference.archive_sha256 != metadata.archive_sha256:
        raise PublicationError("Publisher returned a reference with the wrong archive checksum")
    reference_path = archive_path.with_suffix(archive_path.suffix + ".reference.json")
    _write_json(reference_path, asdict(reference))
    return reference


def check_paper_result_archives(
    docs_root: Path,
    registry_path: Path,
    *,
    report_roots: Sequence[Path] | None = None,
    fetcher: Callable[[str], bytes] | None = None,
) -> list[str]:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if registry.get("schema_version") != PUBLICATION_SCHEMA_VERSION:
        raise PublicBundleValidationError("Archive registry has an unsupported schema_version")
    entries = registry.get("results")
    if not isinstance(entries, list):
        raise PublicBundleValidationError("Archive registry results must be a list")
    by_id: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise PublicBundleValidationError("Every archive registry result must have a string id")
        result_id = entry["id"]
        if result_id in by_id:
            raise PublicBundleValidationError(f"Duplicate archive registry result id: {result_id}")
        _validate_registry_entry(entry, fetcher=fetcher)
        by_id[result_id] = entry

    repository_root = registry_path.parent.parent.resolve()
    for entry in entries:
        if entry.get("classification") != "legacy-diagnostic-unarchived":
            continue
        recovery_record = Path(entry["recovery_record"])
        resolved_record = (repository_root / recovery_record).resolve()
        if (
            recovery_record.is_absolute()
            or ".." in recovery_record.parts
            or recovery_record.suffix.lower() != ".md"
            or not resolved_record.is_relative_to(repository_root)
            or not resolved_record.is_file()
        ):
            raise PublicBundleValidationError(
                f"Unarchived diagnostic {entry['id']} has an invalid recovery_record"
            )

    roots = list(report_roots or [docs_root])
    default_benchmarks = registry_path.parent.parent / "benchmarks"
    if report_roots is None and default_benchmarks.exists():
        roots.append(default_benchmarks)
    declarations: list[str] = []
    paper_declarations: list[str] = []
    benchmark_declarations: list[str] = []
    historical_declarations: list[str] = []
    undeclared_reports: list[str] = []
    paths = sorted({path for root in roots for path in root.rglob("*.md")})
    for path in paths:
        text = path.read_text(encoding="utf-8")
        paper_ids = _PAPER_RESULT.findall(text)
        benchmark_ids = _BENCHMARK_RESULT.findall(text)
        historical_ids = _HISTORICAL_RESULT.findall(text)
        for result_id in historical_ids:
            legacy_path = _LEGACY_UNARCHIVED_DECLARATION_PATHS.get(result_id)
            if not legacy_path:
                raise PublicBundleValidationError(
                    f"Historical result {result_id} is not an approved retired pre-policy diagnostic"
                )
            expected_path = (registry_path.parent.parent / legacy_path).resolve()
            if path.resolve() != expected_path:
                raise PublicBundleValidationError(
                    f"Historical result {result_id} may only be declared in {legacy_path}"
                )
        paper_declarations.extend(paper_ids)
        benchmark_declarations.extend(benchmark_ids)
        historical_declarations.extend(historical_ids)
        declarations.extend([*paper_ids, *benchmark_ids, *historical_ids])
        if _REPORTED_EVALUATION.search(text) and not (paper_ids or benchmark_ids):
            undeclared_reports.append(str(path))
    duplicate_historical = sorted(
        result_id
        for result_id in set(historical_declarations)
        if historical_declarations.count(result_id) != 1
    )
    if duplicate_historical:
        raise PublicBundleValidationError(
            "Historical results must be declared exactly once: "
            + ", ".join(duplicate_historical)
        )
    missing_historical = sorted(
        result_id
        for result_id, entry in by_id.items()
        if entry.get("classification") == "legacy-diagnostic-unarchived"
        and result_id not in historical_declarations
    )
    if missing_historical:
        raise PublicBundleValidationError(
            "Historical results must be declared exactly once: "
            + ", ".join(missing_historical)
        )
    if undeclared_reports:
        raise PublicBundleValidationError(
            "Reported evaluations lack archive declarations: " + ", ".join(undeclared_reports)
        )
    missing = sorted(set(declarations) - by_id.keys())
    if missing:
        raise PublicBundleValidationError(
            "Paper-facing results lack resolvable archived manifests: " + ", ".join(missing)
        )
    invalid_paper = sorted(
        result_id
        for result_id in set(paper_declarations)
        if by_id[result_id].get("classification") != "archived"
    )
    if invalid_paper:
        raise PublicBundleValidationError(
            "Paper-facing results must resolve to verified archives: " + ", ".join(invalid_paper)
        )
    invalid_benchmark = sorted(
        result_id
        for result_id in set(benchmark_declarations)
        if by_id[result_id].get("classification") != "archived"
    )
    if invalid_benchmark:
        raise PublicBundleValidationError(
            "Benchmark-facing results must resolve to verified archives: "
            + ", ".join(invalid_benchmark)
        )
    invalid_historical = sorted(
        result_id
        for result_id in set(historical_declarations)
        if by_id[result_id].get("classification") != "legacy-diagnostic-unarchived"
    )
    if invalid_historical:
        raise PublicBundleValidationError(
            "Historical results must resolve to retired legacy inventory entries: "
            + ", ".join(invalid_historical)
        )
    return sorted(set(declarations))


def _is_private_source_path(relative: Path) -> bool:
    if relative.name in {ARTIFACT_MANIFEST_FILE, COMPLETION_MARKER_FILE}:
        return True
    lowered = {part.lower() for part in relative.parts}
    if lowered & _FORBIDDEN_PATH_PARTS or any(part.startswith(".") for part in relative.parts):
        return True
    return any(
        re.search(r"(?:^|[._-])(evaluator|hidden|private|raw|runtime)(?:[._-]|$)", part, re.IGNORECASE)
        for part in relative.parts
    )


def _derived_relative_path(relative: Path) -> Path:
    if len(relative.parts) == 1 and relative.name == "provenance.json":
        return Path("source_provenance.json")
    if len(relative.parts) == 1 and relative.name == "command.txt":
        return Path("source_command.txt")
    if len(relative.parts) == 1 and relative.name.startswith("config.resolved."):
        return Path("source_" + relative.name)
    return relative


def _write_sanitized_artifact(source: Path, target: Path) -> None:
    text = source.read_text(encoding="utf-8")
    suffix = source.suffix.lower()
    if suffix == ".json":
        payload = _sanitize_value(json.loads(text))
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif suffix == ".jsonl":
        rows = [
            json.dumps(_sanitize_value(json.loads(line)), ensure_ascii=False)
            for line in text.splitlines()
            if line.strip()
        ]
        target.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    elif suffix == ".csv":
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise PublicBundleValidationError(f"Invalid CSV source artifact: {source}")
        fieldnames = [name for name in reader.fieldnames if not _is_hidden_key(name)]
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for row in reader:
            writer.writerow({key: _sanitize_scalar_for_key(key, row.get(key)) for key in fieldnames})
        target.write_text(output.getvalue(), encoding="utf-8")
    elif suffix in {".yaml", ".yml"}:
        documents = [_sanitize_value(item) for item in yaml.safe_load_all(text)]
        target.write_text(yaml.safe_dump_all(documents, sort_keys=False, allow_unicode=True), encoding="utf-8")
    else:
        target.write_text(_sanitize_text(text), encoding="utf-8")


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        if _has_private_provenance(value):
            return None
        result = {}
        for key, child in value.items():
            key_text = str(key)
            if _is_hidden_key(key_text):
                continue
            if _is_credential_key(key_text) and not _CREDENTIAL_SOURCE_KEY.search(key_text):
                result[key] = "[REDACTED]"
            else:
                result[key] = _sanitize_value(child)
        return result
    if isinstance(value, list):
        sanitized = [_sanitize_value(item) for item in value]
        return [item for item in sanitized if item is not None]
    return value


def _has_private_provenance(value: dict[str, Any]) -> bool:
    provenance = value.get("provenance")
    if not isinstance(provenance, dict):
        return False
    return (
        str(provenance.get("visibility") or "").lower() == "private"
        or str(provenance.get("source") or "").lower() in {
            "private_observation",
            "private_view",
        }
    )


def _sanitize_scalar_for_key(key: str, value: Any) -> Any:
    if _is_credential_key(key) and not _CREDENTIAL_SOURCE_KEY.search(key):
        return "[REDACTED]"
    return value


def _sanitize_text(text: str) -> str:
    text = _INLINE_CREDENTIAL.sub(lambda match: match.group(1) + "[REDACTED]", text)
    lines = []
    assignment = re.compile(r"^(\s*)([A-Za-z0-9_-]+)(\s*[:=]\s*)(.*)$")
    for line in text.splitlines(keepends=True):
        match = assignment.match(line.rstrip("\r\n"))
        if match and _is_hidden_key(match.group(2)):
            continue
        if match and _is_credential_key(match.group(2)) and not _CREDENTIAL_SOURCE_KEY.search(match.group(2)):
            ending = "\n" if line.endswith("\n") else ""
            line = f"{match.group(1)}{match.group(2)}{match.group(3)}[REDACTED]{ending}"
        lines.append(line)
    sanitized = "".join(lines)
    for pattern in _SECRET_PATTERNS:
        if pattern.search(sanitized):
            raise PublicBundleValidationError("Credential literal in free-form source artifact cannot be safely derived")
    return sanitized


def _is_hidden_key(key: str) -> bool:
    return key.lower().replace("-", "_") in _FORBIDDEN_DATA_KEYS


def _is_credential_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized not in _NON_CREDENTIAL_TOKEN_KEYS and bool(_CREDENTIAL_KEY.search(key))


def _validate_public_path(relative: Path) -> None:
    lowered_parts = {part.lower() for part in relative.parts}
    forbidden = lowered_parts & _FORBIDDEN_PATH_PARTS
    if forbidden:
        raise PublicBundleValidationError(
            f"Private/hidden/runtime path is not publishable: {relative} ({sorted(forbidden)[0]})"
        )
    sensitive_name = next(
        (
            part
            for part in relative.parts
            if re.search(r"(?:^|[._-])(evaluator|hidden|private|raw|runtime)(?:[._-]|$)", part, re.IGNORECASE)
        ),
        None,
    )
    if sensitive_name:
        raise PublicBundleValidationError(
            f"Private/hidden/runtime path is not publishable: {relative} ({sensitive_name})"
        )
    if any(part.startswith(".") for part in relative.parts):
        raise PublicBundleValidationError(f"Hidden file is not publishable: {relative}")
    if relative.name == COMPLETION_MARKER_FILE:
        return
    if relative.suffix.lower() not in _PUBLIC_SUFFIXES:
        raise PublicBundleValidationError(f"Unexpected public artifact type: {relative}")


def _scan_public_file(path: Path, relative: Path) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PublicBundleValidationError(f"Binary public artifact is not allowed: {relative}") from exc
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            raise PublicBundleValidationError(f"credential pattern detected in {relative}")
    if _INLINE_CREDENTIAL.search(text):
        raise PublicBundleValidationError(f"Unredacted credential argument detected in {relative}")
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PublicBundleValidationError(f"Invalid JSON public artifact: {relative}") from exc
        _scan_value(payload, relative)
    elif suffix == ".jsonl":
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PublicBundleValidationError(
                    f"Invalid JSONL public artifact: {relative}:{line_number}"
                ) from exc
            _scan_value(payload, relative)
    elif suffix == ".csv":
        try:
            reader = csv.DictReader(io.StringIO(text))
            if reader.fieldnames is None:
                raise PublicBundleValidationError(f"Invalid CSV public artifact: {relative}")
            for row in reader:
                _scan_value(row, relative)
        except csv.Error as exc:
            raise PublicBundleValidationError(f"Invalid CSV public artifact: {relative}") from exc
    elif suffix in {".yaml", ".yml"}:
        try:
            for payload in yaml.safe_load_all(text):
                _scan_value(payload, relative)
        except yaml.YAMLError as exc:
            raise PublicBundleValidationError(f"Invalid YAML public artifact: {relative}") from exc
    else:
        for match in re.finditer(
            r"(?im)^\s*([A-Za-z0-9_-]+)\s*[:=]\s*['\"]?([^\s'\"]+)", text
        ):
            key, value = match.groups()
            _validate_key_value(key, value, relative)


def _scan_value(value: Any, relative: Path) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if key_text.lower().replace("-", "_") in _FORBIDDEN_DATA_KEYS:
                raise PublicBundleValidationError(
                    f"Hidden evaluator/private observation field detected in {relative}: {key_text}"
                )
            _validate_key_value(key_text, child, relative)
            _scan_value(child, relative)
    elif isinstance(value, list):
        for child in value:
            _scan_value(child, relative)


def _validate_key_value(key: str, value: Any, relative: Path) -> None:
    if key.lower().replace("-", "_") in _FORBIDDEN_DATA_KEYS:
        raise PublicBundleValidationError(
            f"Hidden evaluator/private observation field detected in {relative}: {key}"
        )
    if _CREDENTIAL_SOURCE_KEY.search(key):
        return
    if not _is_credential_key(key):
        return
    if value in (None, "", "[REDACTED]"):
        return
    raise PublicBundleValidationError(f"Unredacted credential field detected in {relative}: {key}")


def _validate_nested_attempts(bundle_dir: Path) -> dict[str, int]:
    statuses: dict[str, int] = {}
    for attempt_path in sorted(bundle_dir.rglob("attempt.json")):
        run_dir = attempt_path.parent
        payload = json.loads(attempt_path.read_text(encoding="utf-8"))
        status = str(payload.get("status") or "running")
        statuses[status] = statuses.get(status, 0) + 1
        if run_dir == bundle_dir:
            continue
        try:
            validate_run_attempt(
                run_dir,
                attempt_id=str(payload.get("attempt_id") or ""),
                require_completed=status == "completed",
            )
        except (OSError, json.JSONDecodeError, RunArtifactValidationError) as exc:
            raise PublicBundleValidationError(str(exc)) from exc
    return statuses


def _benchmark_from_producer(producer: str) -> str:
    lowered = producer.lower()
    if "minecraft" in lowered or "villagerbench" in lowered:
        return "minecraft"
    if "craft" in lowered:
        return "craft"
    if "cwah" in lowered:
        return "cwah"
    if "tdw_mat" in lowered or "tdw-mat" in lowered:
        return "tdw_mat"
    return ""


def _validate_registry_entry(
    entry: dict[str, Any],
    *,
    fetcher: Callable[[str], bytes] | None = None,
) -> None:
    result_id = entry["id"]
    if entry.get("benchmark") not in {"craft", "cwah", "minecraft", "tdw_mat"}:
        raise PublicBundleValidationError(f"Invalid benchmark for archived result {result_id}")
    classification = entry.get("classification")
    if classification not in {"archived", "legacy-diagnostic-unarchived"}:
        raise PublicBundleValidationError(f"Archived result {result_id} has invalid classification")
    accounting = entry.get("run_accounting")
    if not isinstance(accounting, dict):
        raise PublicBundleValidationError(f"Archived result {result_id} lacks run accounting")
    counts = [accounting.get(key) for key in ("expected", "completed", "failed", "missing")]
    if any(not isinstance(count, int) or isinstance(count, bool) or count < 0 for count in counts):
        raise PublicBundleValidationError(f"Archived result {result_id} has invalid run accounting")
    if counts[0] != sum(counts[1:]):
        raise PublicBundleValidationError(f"Archived result {result_id} does not account for every run")
    if classification == "legacy-diagnostic-unarchived":
        if result_id not in _LEGACY_UNARCHIVED_DECLARATION_PATHS:
            raise PublicBundleValidationError(
                f"Unarchived result {result_id} is not an approved pre-policy legacy diagnostic"
            )
        if entry.get("legacy_pre_policy") is not True:
            raise PublicBundleValidationError(f"Unarchived diagnostic {result_id} is not marked pre-policy")
        if entry.get("publication_satisfied") is not False or entry.get("claim_eligible") is not False:
            raise PublicBundleValidationError(
                f"Unarchived diagnostic {result_id} must be ineligible for publication and claims"
            )
        retired_at = entry.get("retired_at")
        recovery_record = entry.get("recovery_record")
        if (
            entry.get("retired") is not True
            or entry.get("paper_facing") is not False
            or entry.get("recovery_status") != "exhausted"
            or not isinstance(retired_at, str)
            or not retired_at.strip()
            or not isinstance(recovery_record, str)
            or not recovery_record.strip()
        ):
            raise PublicBundleValidationError(
                f"Unarchived diagnostic {result_id} lacks permanent retirement metadata"
            )
        try:
            date.fromisoformat(retired_at)
        except ValueError as error:
            raise PublicBundleValidationError(
                f"Unarchived diagnostic {result_id} has invalid retired_at date"
            ) from error
        if not isinstance(entry.get("reason"), str) or not entry["reason"].strip():
            raise PublicBundleValidationError(f"Unarchived diagnostic {result_id} lacks a reason")
        if counts[3] != counts[0]:
            raise PublicBundleValidationError(
                f"Unarchived diagnostic {result_id} must account for every source run as missing"
            )
        return

    archive_url = entry.get("archive_url")
    if not isinstance(archive_url, str) or not archive_url.startswith(_STABLE_URL_PREFIXES):
        raise PublicBundleValidationError(f"Archived result {result_id} lacks a stable archive URL")
    if "github.com/" in archive_url and "/releases/download/" not in archive_url:
        raise PublicBundleValidationError(
            f"Archived result {result_id} must use an immutable GitHub release asset URL"
        )
    for key in ("archive_sha256", "manifest_sha256"):
        if not isinstance(entry.get(key), str) or not _SHA256.fullmatch(entry[key]):
            raise PublicBundleValidationError(f"Archived result {result_id} has invalid {key}")
    manifest_path = Path(str(entry.get("manifest_path") or ""))
    if (
        manifest_path.is_absolute()
        or ".." in manifest_path.parts
        or manifest_path.name != ARTIFACT_MANIFEST_FILE
    ):
        raise PublicBundleValidationError(f"Archived result {result_id} has invalid manifest_path")
    metadata_url = entry.get("metadata_url")
    if not isinstance(metadata_url, str) or not metadata_url.startswith(_STABLE_URL_PREFIXES):
        raise PublicBundleValidationError(f"Archived result {result_id} lacks stable metadata_url")
    if "github.com/" in metadata_url and "/releases/download/" not in metadata_url:
        raise PublicBundleValidationError(
            f"Archived result {result_id} metadata must use an immutable GitHub release asset URL"
        )
    _verify_registry_archive(entry, fetcher=fetcher or _fetch_url)


def _verify_registry_archive(entry: dict[str, Any], *, fetcher: Callable[[str], bytes]) -> None:
    result_id = entry["id"]
    try:
        metadata = json.loads(fetcher(entry["metadata_url"]).decode("utf-8"))
        archive = fetcher(entry["archive_url"])
    except Exception as exc:
        raise PublicBundleValidationError(f"Archived result {result_id} is not resolvable: {exc}") from exc
    archive_sha = hashlib.sha256(archive).hexdigest()
    if archive_sha != entry["archive_sha256"] or metadata.get("archive_sha256") != archive_sha:
        raise PublicBundleValidationError(f"Archived result {result_id} archive checksum mismatch")
    if metadata.get("source_manifest_sha256") != entry["manifest_sha256"]:
        raise PublicBundleValidationError(f"Archived result {result_id} metadata manifest checksum mismatch")
    metadata_statuses = metadata.get("run_statuses")
    if not isinstance(metadata_statuses, dict) or any(
        status not in {"completed", "failed"}
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        for status, count in metadata_statuses.items()
    ):
        raise PublicBundleValidationError(f"Archived result {result_id} metadata has invalid run_statuses")
    accounting = entry["run_accounting"]
    completed = metadata_statuses.get("completed", 0)
    failed = metadata_statuses.get("failed", 0)
    if completed != accounting["completed"] or failed != accounting["failed"]:
        raise PublicBundleValidationError(f"Archived result {result_id} run accounting mismatch")
    if accounting["expected"] != completed + failed + accounting["missing"]:
        raise PublicBundleValidationError(f"Archived result {result_id} metadata does not account for every run")
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            manifest_path = entry["manifest_path"]
            if manifest_path not in bundle.namelist():
                raise PublicBundleValidationError(
                    f"Archived result {result_id} does not contain {manifest_path}"
                )
            manifest_sha = hashlib.sha256(bundle.read(manifest_path)).hexdigest()
    except zipfile.BadZipFile as exc:
        raise PublicBundleValidationError(f"Archived result {result_id} is not a ZIP archive") from exc
    if manifest_sha != entry["manifest_sha256"]:
        raise PublicBundleValidationError(f"Archived result {result_id} manifest checksum mismatch")


def _fetch_url(url: str) -> bytes:
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(url, timeout=30, context=context) as response:
        return response.read()


def _validate_local_staging_destination(root: Path, stable_id: str) -> None:
    stable = Path(stable_id)
    if stable.is_absolute() or len(stable.parts) != 1 or stable_id in {"", ".", ".."}:
        raise PublicationError("Local staging stable ID must be one safe path component")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", stable_id):
        raise PublicationError("Local staging stable ID contains unsafe characters")
    candidate = root.absolute()
    for path in [candidate, *candidate.parents]:
        if path.exists() and path.is_symlink():
            raise PublicationError(f"Local staging root must not contain symlinks: {path}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and publish sanitized benchmark bundles")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("bundle", type=Path)
    sanitize = subparsers.add_parser("sanitize")
    sanitize.add_argument("bundle", type=Path)
    sanitize.add_argument("--output", type=Path, required=True)
    archive = subparsers.add_parser("archive")
    archive.add_argument("bundle", type=Path)
    archive.add_argument("--output", type=Path, required=True)
    publish = subparsers.add_parser("publish")
    publish.add_argument("bundle", type=Path)
    publish.add_argument("--output", type=Path, required=True)
    publish.add_argument("--publisher", choices=("local-staging", "github"), required=True)
    publish.add_argument("--archive-root", type=Path)
    publish.add_argument("--stable-id")
    publish.add_argument("--repository")
    publish.add_argument("--tag")
    docs = subparsers.add_parser("check-docs")
    docs.add_argument("--docs-root", type=Path, default=Path("docs"))
    docs.add_argument("--registry", type=Path, default=Path("docs/benchmark_archives.json"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "validate":
        print(json.dumps(asdict(validate_public_bundle(args.bundle)), sort_keys=True, indent=2))
    elif args.command == "sanitize":
        print(json.dumps(asdict(derive_public_bundle(args.bundle, args.output)), sort_keys=True, indent=2))
    elif args.command == "archive":
        metadata, metadata_path = build_deterministic_archive(args.bundle, args.output)
        print(json.dumps({**asdict(metadata), "metadata_path": str(metadata_path)}, sort_keys=True, indent=2))
    elif args.command == "publish":
        if args.publisher == "local-staging":
            if args.archive_root is None or not args.stable_id:
                raise SystemExit("local staging requires --archive-root and --stable-id")
            publisher: Publisher = LocalStagingPublisher(args.archive_root, args.stable_id)
        else:
            if not args.repository or not args.tag:
                raise SystemExit("GitHub publication requires --repository and --tag")
            publisher = GitHubReleasePublisher(args.repository, args.tag)
        print(json.dumps(asdict(publish_bundle(args.bundle, args.output, publisher)), sort_keys=True, indent=2))
    else:
        declarations = check_paper_result_archives(args.docs_root, args.registry)
        print(f"Validated {len(declarations)} benchmark result declaration(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
