"""Resolve repository-approved Minecraft premanifests without running them."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from benchmarks.minecraft.matrix_spec import (
    MATRIX_SCHEMA_VERSION,
    MatrixSpec,
    matrix_spec_sha256,
    parse_matrix_spec,
    validate_matrix_spec,
)


DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_METADATA_BYTES = 1024 * 1024
DEFAULT_MAX_ARTIFACT_BYTES = 256 * 1024
REGISTRY_SCHEMA_VERSION = 1
GITHUB_API_HOST = "api.github.com"
GIST_RAW_HOST = "gist.githubusercontent.com"
APPROVED_CLEANUP_POLICY = "independent_snapshot_per_run"


class ApprovedExperimentError(ValueError):
    """An approved experiment failed closed during registry or resolution checks."""


@dataclass(frozen=True)
class ArtifactReference:
    provider: str
    owner: str
    gist_id: str
    revision: str
    path: str
    byte_sha256: str


@dataclass(frozen=True)
class ApprovedExperiment:
    schema_version: int
    experiment_id: str
    approved_source_revision: str
    canonical_premanifest_identity: str
    runtime_identity: Mapping[str, Any]
    model_endpoint: str
    artifact: ArtifactReference
    expected: Mapping[str, Any]

    # Compatibility names are intentionally read-only.
    @property
    def approved_revision(self) -> str:
        return self.approved_source_revision

    @property
    def canonical_identity(self) -> str:
        return self.canonical_premanifest_identity


@dataclass(frozen=True)
class ResolvedExperiment:
    record: ApprovedExperiment
    premanifest_path: Path
    provenance_path: Path
    spec: MatrixSpec


_TOP_LEVEL_KEYS = {
    "schema_version",
    "experiment_id",
    "approved_source_revision",
    "canonical_premanifest_identity",
    "runtime_identity",
    "model_endpoint",
    "artifact",
    "expected",
}
_RUNTIME_KEYS = {"name", "image", "digest"}
_ARTIFACT_KEYS = {"provider", "owner", "gist_id", "revision", "path", "byte_sha256"}
_EXPECTED_KEYS = {
    "model",
    "seeds",
    "baselines",
    "generation",
    "execution",
    "cleanup_policy",
    "ordered_runs",
}
_MODEL_KEYS = {"provider", "name", "digest"}
_BASELINE_KEYS = {"baseline_id", "path", "sha256"}
_GENERATION_KEYS = {"temperature", "top_p", "max_tokens", "timeout_seconds", "max_iterations"}
_EXECUTION_KEYS = {"mode", "stop_on_first_failure", "retry_policy"}
_RUN_KEYS = {"order", "run_id", "variant", "seed", "baseline_id", "variant_definition_sha256"}


def load_registry(registry_dir: str | Path | None = None) -> dict[str, ApprovedExperiment]:
    directory = (
        Path(registry_dir)
        if registry_dir is not None
        else Path(__file__).resolve().parents[2] / "configs" / "minecraft" / "approved-experiments"
    )
    if not directory.is_dir() or directory.is_symlink():
        raise ApprovedExperimentError("approved experiment registry is unavailable")
    records: dict[str, ApprovedExperiment] = {}
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError as exc:
        raise ApprovedExperimentError("approved experiment registry is unreadable") from exc
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ApprovedExperimentError(f"registry entry is not a regular file: {path.name}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise ApprovedExperimentError(f"invalid registry JSON: {path.name}") from exc
        record = _parse_record(raw)
        if record.experiment_id in records:
            raise ApprovedExperimentError(f"duplicate approved experiment ID: {record.experiment_id}")
        records[record.experiment_id] = record
    return records


def get_approved_experiment(
    experiment_id: str, registry_dir: str | Path | None = None
) -> ApprovedExperiment:
    if not isinstance(experiment_id, str) or not experiment_id or any(
        char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in experiment_id
    ):
        raise ApprovedExperimentError("approved experiment ID is invalid")
    records = load_registry(registry_dir)
    try:
        return records[experiment_id]
    except KeyError as exc:
        raise ApprovedExperimentError(f"unknown approved experiment: {experiment_id}") from exc


def resolve_approved_experiment(
    experiment_id: str,
    output_dir: str | Path,
    execution_worktree: str | Path,
    registry_dir: str | Path | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_metadata_bytes: int = DEFAULT_MAX_METADATA_BYTES,
    max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
) -> ResolvedExperiment:
    """Resolve and validate an approved artifact without starting an experiment."""
    timeout = _positive_number(timeout_seconds, "timeout")
    metadata_limit = _positive_integer(max_metadata_bytes, "metadata size limit")
    artifact_limit = _positive_integer(max_artifact_bytes, "artifact size limit")
    record = get_approved_experiment(experiment_id, registry_dir)
    execution = _validate_execution_worktree(
        execution_worktree, record.approved_source_revision, timeout
    )
    output = _validate_output_path(output_dir, execution, timeout)

    artifact = record.artifact
    metadata_url = (
        f"https://{GITHUB_API_HOST}/gists/{artifact.gist_id}/{artifact.revision}"
    )
    metadata_bytes = _fetch_bounded(metadata_url, timeout, metadata_limit)
    try:
        metadata = json.loads(metadata_bytes, parse_constant=_reject_json_constant)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ApprovedExperimentError("pinned Gist metadata is invalid") from exc
    files = metadata.get("files") if isinstance(metadata, dict) else None
    if not isinstance(files, dict) or artifact.path not in files:
        raise ApprovedExperimentError("registered premanifest is missing from pinned Gist revision")
    owner = metadata.get("owner") if isinstance(metadata, dict) else None
    if isinstance(owner, dict) and owner.get("login", "").lower() != artifact.owner:
        raise ApprovedExperimentError("pinned Gist owner does not match the registry")

    encoded_path = urllib.parse.quote(artifact.path, safe="")
    raw_url = (
        f"https://{GIST_RAW_HOST}/{artifact.owner}/{artifact.gist_id}/raw/"
        f"{artifact.revision}/{encoded_path}"
    )
    payload = _fetch_bounded(raw_url, timeout, artifact_limit)
    if hashlib.sha256(payload).hexdigest() != artifact.byte_sha256:
        raise ApprovedExperimentError("approved premanifest byte SHA-256 mismatch")
    spec = _validate_premanifest(payload, record, execution)

    control_plane_revision = _git_output(
        Path(__file__).resolve().parents[2], ["rev-parse", "HEAD"], timeout
    )
    provenance = {
        "schema_version": 1,
        "record_type": "approved_experiment_resolution",
        "experiment_id": record.experiment_id,
        "control_plane_revision": control_plane_revision,
        "execution_revision": record.approved_source_revision,
        "canonical_premanifest_identity": record.canonical_premanifest_identity,
        "runtime_identity": dict(record.runtime_identity),
        "model_endpoint": record.model_endpoint,
        "artifact": {
            "provider": artifact.provider,
            "gist_id": artifact.gist_id,
            "revision": artifact.revision,
            "path": artifact.path,
            "byte_sha256": artifact.byte_sha256,
        },
        "resolved_path": "premanifest.json",
    }
    return _write_resolution_atomically(output, payload, provenance, record, spec)


def _parse_record(raw: Any) -> ApprovedExperiment:
    value = _strict_object(raw, _TOP_LEVEL_KEYS, "registry record")
    if value["schema_version"] != REGISTRY_SCHEMA_VERSION or isinstance(
        value["schema_version"], bool
    ):
        raise ApprovedExperimentError("unsupported approved experiment registry schema")
    experiment_id = _nonempty_string(value["experiment_id"], "experiment ID")
    revision = _git_sha(value["approved_source_revision"], "approved source revision")
    canonical = _sha256(value["canonical_premanifest_identity"], "canonical premanifest identity")

    runtime = _strict_object(value["runtime_identity"], _RUNTIME_KEYS, "runtime identity")
    for key in ("name", "image"):
        _nonempty_string(runtime[key], f"runtime {key}")
    _sha256(runtime["digest"], "runtime digest", prefix=True)

    endpoint = _validate_model_endpoint(value["model_endpoint"])
    source = _strict_object(value["artifact"], _ARTIFACT_KEYS, "artifact")
    if source["provider"] != "github-gist":
        raise ApprovedExperimentError("unsupported artifact provider")
    owner = _nonempty_string(source["owner"], "Gist owner").lower()
    if any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in owner):
        raise ApprovedExperimentError("Gist owner is invalid")
    gist_id = _hex_string(source["gist_id"], 32, "Gist ID")
    artifact_revision = _git_sha(source["revision"], "immutable artifact revision")
    artifact_path = _artifact_path(source["path"])
    byte_sha256 = _sha256(source["byte_sha256"], "artifact byte SHA-256")

    expected = _parse_expected(value["expected"])
    return ApprovedExperiment(
        schema_version=REGISTRY_SCHEMA_VERSION,
        experiment_id=experiment_id,
        approved_source_revision=revision,
        canonical_premanifest_identity=canonical,
        runtime_identity=dict(runtime),
        model_endpoint=endpoint,
        artifact=ArtifactReference(
            provider="github-gist",
            owner=owner,
            gist_id=gist_id,
            revision=artifact_revision,
            path=artifact_path,
            byte_sha256=byte_sha256,
        ),
        expected=expected,
    )


def _parse_expected(raw: Any) -> dict[str, Any]:
    value = _strict_object(raw, _EXPECTED_KEYS, "expected contract")
    model = _strict_object(value["model"], _MODEL_KEYS, "expected model")
    for key in ("provider", "name"):
        _nonempty_string(model[key], f"model {key}")
    _sha256(model["digest"], "model digest", prefix=True)

    seeds = value["seeds"]
    if not isinstance(seeds, list) or any(isinstance(item, bool) or not isinstance(item, int) for item in seeds):
        raise ApprovedExperimentError("expected seeds must be an integer array")
    baselines = _strict_object_array(value["baselines"], _BASELINE_KEYS, "expected baseline")
    for baseline in baselines:
        _nonempty_string(baseline["baseline_id"], "baseline ID")
        _repository_path(baseline["path"], "baseline path")
        _sha256(baseline["sha256"], "baseline SHA-256")

    generation = _strict_object(value["generation"], _GENERATION_KEYS, "expected generation")
    execution = _strict_object(value["execution"], _EXECUTION_KEYS, "expected execution")
    ordered_runs = _strict_object_array(value["ordered_runs"], _RUN_KEYS, "expected ordered run")
    for run in ordered_runs:
        if isinstance(run["order"], bool) or not isinstance(run["order"], int):
            raise ApprovedExperimentError("ordered run index must be an integer")
        if isinstance(run["seed"], bool) or not isinstance(run["seed"], int):
            raise ApprovedExperimentError("ordered run seed must be an integer")
        for key in ("run_id", "variant", "baseline_id"):
            _nonempty_string(run[key], f"ordered run {key}")
        _sha256(run["variant_definition_sha256"], "variant definition SHA-256")
    if value["cleanup_policy"] != APPROVED_CLEANUP_POLICY:
        raise ApprovedExperimentError("unsupported approved cleanup policy")
    return {
        "model": dict(model),
        "seeds": list(seeds),
        "baselines": [dict(item) for item in baselines],
        "generation": dict(generation),
        "execution": dict(execution),
        "cleanup_policy": value["cleanup_policy"],
        "ordered_runs": [dict(item) for item in ordered_runs],
    }


def _validate_premanifest(
    payload: bytes, record: ApprovedExperiment, execution: Path
) -> MatrixSpec:
    try:
        spec = parse_matrix_spec(payload)
    except (UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise ApprovedExperimentError("artifact is not an authoritative schema-v2 premanifest") from exc
    if spec.schema_version != MATRIX_SCHEMA_VERSION or spec.lifecycle_state != "finalized":
        raise ApprovedExperimentError("artifact is not a finalized schema-v2 premanifest")
    canonical = matrix_spec_sha256(spec)
    if canonical != record.canonical_premanifest_identity or spec.premanifest_sha256 != canonical:
        raise ApprovedExperimentError("canonical premanifest identity mismatch")
    if spec.revision != record.approved_source_revision:
        raise ApprovedExperimentError("approved source revision mismatch")
    if vars(spec.runtime) != dict(record.runtime_identity):
        raise ApprovedExperimentError("approved runtime identity drift")

    expected = record.expected
    comparisons = (
        (vars(spec.model), expected["model"], "approved model identity drift"),
        (list(spec.seeds), expected["seeds"], "approved seed order drift"),
        ([vars(item) for item in spec.baselines], expected["baselines"], "approved baseline identity drift"),
        (vars(spec.generation), expected["generation"], "approved generation parameter drift"),
        (vars(spec.execution), expected["execution"], "approved execution/retry policy drift"),
        (
            [
                {
                    "order": run.order,
                    "run_id": run.run_id,
                    "variant": run.variant,
                    "seed": run.seed,
                    "baseline_id": run.baseline_id,
                    "variant_definition_sha256": run.variant_definition_sha256,
                }
                for run in spec.runs
            ],
            expected["ordered_runs"],
            "approved run variant/order drift",
        ),
    )
    for observed, approved, message in comparisons:
        if observed != approved:
            raise ApprovedExperimentError(message)
    if expected["cleanup_policy"] != APPROVED_CLEANUP_POLICY:
        raise ApprovedExperimentError("approved cleanup policy drift")
    try:
        return validate_matrix_spec(spec, repo_root=execution)
    except (ValueError, TypeError, KeyError) as exc:
        raise ApprovedExperimentError("finalized premanifest validation failed") from exc


def _validate_execution_worktree(
    worktree: str | Path, approved_revision: str, timeout: float
) -> Path:
    requested = Path(worktree).expanduser()
    if requested.is_symlink() or not requested.is_dir():
        raise ApprovedExperimentError("execution worktree must be an existing non-symlink directory")
    resolved = requested.resolve()
    top_level = Path(_git_output(resolved, ["rev-parse", "--show-toplevel"], timeout)).resolve()
    if top_level != resolved:
        raise ApprovedExperimentError("execution worktree path must be its Git top level")
    if _git_output(resolved, ["rev-parse", "HEAD"], timeout) != approved_revision:
        raise ApprovedExperimentError("execution worktree revision does not match the approval")
    if _git_output(resolved, ["status", "--porcelain", "--untracked-files=all"], timeout):
        raise ApprovedExperimentError("execution worktree must be clean")
    return resolved


def _validate_output_path(output: str | Path, execution: Path, timeout: float) -> Path:
    requested = Path(output).expanduser()
    if not requested.is_absolute():
        raise ApprovedExperimentError("resolver output must be an absolute path")
    if requested.exists() or requested.is_symlink():
        raise ApprovedExperimentError("resolver output must not already exist")
    _reject_symlink_ancestors(requested.parent)
    _validate_private_output_ancestor(requested.parent)
    resolved = requested.resolve(strict=False)
    repository_root = Path(__file__).resolve().parents[2]
    worktree_text = _git_output(repository_root, ["worktree", "list", "--porcelain"], timeout)
    worktrees = [execution]
    worktrees.extend(
        Path(line.removeprefix("worktree ")).resolve()
        for line in worktree_text.splitlines()
        if line.startswith("worktree ")
    )
    if any(resolved == root or root in resolved.parents for root in worktrees):
        raise ApprovedExperimentError("resolver output must be outside tracked worktrees")
    return resolved


def _fetch_bounded(url: str, timeout: float, limit: int) -> bytes:
    _validate_source_url(url)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "VillagerAgent-approved-resolver/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            if not isinstance(status, int) or status < 200 or status >= 300:
                raise ApprovedExperimentError(f"approved artifact provider returned HTTP {status}")
            final_url = response.geturl()
            _validate_source_url(final_url)
            if urllib.parse.urlsplit(final_url).hostname != urllib.parse.urlsplit(url).hostname:
                raise ApprovedExperimentError("approved source redirected to another host")
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared = int(content_length)
                except ValueError as exc:
                    raise ApprovedExperimentError("artifact provider returned invalid Content-Length") from exc
                if declared < 0 or declared > limit:
                    raise ApprovedExperimentError("approved source response exceeds size limit")
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = response.read(min(65536, limit + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise ApprovedExperimentError("approved source response exceeds size limit")
                chunks.append(chunk)
            return b"".join(chunks)
    except ApprovedExperimentError:
        raise
    except urllib.error.HTTPError as exc:
        if urllib.parse.urlsplit(url).hostname == GITHUB_API_HOST and exc.code == 404:
            raise ApprovedExperimentError("pinned Gist revision is unavailable") from exc
        raise ApprovedExperimentError(f"approved artifact provider returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise ApprovedExperimentError("approved artifact fetch failed within configured bounds") from exc


def _write_resolution_atomically(
    output: Path,
    payload: bytes,
    provenance: Mapping[str, Any],
    record: ApprovedExperiment,
    spec: MatrixSpec,
) -> ResolvedExperiment:
    output.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_ancestors(output.parent)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        premanifest = staging / "premanifest.json"
        provenance_path = staging / "resolution_provenance.json"
        _write_bytes_durable(premanifest, payload)
        _write_bytes_durable(
            provenance_path,
            (json.dumps(provenance, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return ResolvedExperiment(
        record=record,
        premanifest_path=output / "premanifest.json",
        provenance_path=output / "resolution_provenance.json",
        spec=spec,
    )


def _write_bytes_durable(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _git_output(cwd: Path, arguments: list[str], timeout: float) -> str:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ApprovedExperimentError("bounded Git worktree inspection failed") from exc


def _validate_source_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {GITHUB_API_HOST, GIST_RAW_HOST}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ApprovedExperimentError("approved artifact source URL is not allowlisted")


def _validate_model_endpoint(value: Any) -> str:
    endpoint = _nonempty_string(value, "model endpoint")
    parsed = urllib.parse.urlsplit(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") not in {"", "/v1"}
    ):
        raise ApprovedExperimentError("approved model endpoint is invalid")
    try:
        parsed.port
    except ValueError as exc:
        raise ApprovedExperimentError("approved model endpoint has an invalid port") from exc
    return endpoint.rstrip("/")


def _artifact_path(value: Any) -> str:
    path = _nonempty_string(value, "artifact path")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or len(parsed.parts) != 1 or path != parsed.as_posix() or parsed.suffix != ".json":
        raise ApprovedExperimentError("artifact path must be an explicit JSON filename")
    return path


def _repository_path(value: Any, context: str) -> str:
    path = _nonempty_string(value, context)
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts or "\\" in path:
        raise ApprovedExperimentError(f"{context} must be repository-relative")
    return path


def _strict_object(value: Any, keys: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ApprovedExperimentError(f"{context} must be an object")
    actual = set(value)
    if actual != keys:
        raise ApprovedExperimentError(
            f"{context} schema mismatch; missing={sorted(keys - actual)}, extra={sorted(actual - keys)}"
        )
    return value


def _strict_object_array(value: Any, keys: set[str], context: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ApprovedExperimentError(f"{context} list must be an array")
    return [_strict_object(item, keys, context) for item in value]


def _nonempty_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApprovedExperimentError(f"{context} must be a non-empty string")
    return value


def _hex_string(value: Any, length: int, context: str) -> str:
    text = _nonempty_string(value, context)
    if len(text) != length or any(char not in "0123456789abcdef" for char in text):
        raise ApprovedExperimentError(f"{context} is malformed")
    return text


def _git_sha(value: Any, context: str) -> str:
    return _hex_string(value, 40, context)


def _sha256(value: Any, context: str, *, prefix: bool = False) -> str:
    text = _nonempty_string(value, context)
    candidate = text.removeprefix("sha256:") if prefix else text
    _hex_string(candidate, 64, context)
    if prefix and text.startswith("sha256:"):
        return text
    if prefix or text == candidate:
        return text
    raise ApprovedExperimentError(f"{context} is malformed")


def _positive_number(value: Any, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ApprovedExperimentError(f"{context} must be finite and positive")
    return float(value)


def _positive_integer(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ApprovedExperimentError(f"{context} must be a positive integer")
    return value


def _reject_symlink_ancestors(path: Path) -> None:
    current = path
    while True:
        if current.is_symlink():
            raise ApprovedExperimentError("resolver output path contains a symlink")
        if current == current.parent:
            break
        current = current.parent


def _validate_private_output_ancestor(path: Path) -> None:
    current = path
    while not current.exists():
        if current == current.parent:
            raise ApprovedExperimentError("resolver output has no existing parent")
        current = current.parent
    metadata = current.stat()
    if not current.is_dir() or metadata.st_uid != os.geteuid():
        raise ApprovedExperimentError("resolver output parent must be an owned directory")
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise ApprovedExperimentError("resolver output parent must not be group/world writable")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve an approved Minecraft experiment bundle.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    resolve_parser = subparsers.add_parser("resolve")
    resolve_parser.add_argument("--experiment", required=True)
    resolve_parser.add_argument("--output", required=True)
    resolve_parser.add_argument("--execution-worktree", required=True)
    resolve_parser.add_argument("--registry-dir")
    resolve_parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    resolve_parser.add_argument("--max-metadata-bytes", type=int, default=DEFAULT_MAX_METADATA_BYTES)
    resolve_parser.add_argument("--max-artifact-bytes", type=int, default=DEFAULT_MAX_ARTIFACT_BYTES)
    args = parser.parse_args(argv)
    try:
        resolved = resolve_approved_experiment(
            args.experiment,
            args.output,
            args.execution_worktree,
            registry_dir=args.registry_dir,
            timeout_seconds=args.timeout_seconds,
            max_metadata_bytes=args.max_metadata_bytes,
            max_artifact_bytes=args.max_artifact_bytes,
        )
    except (ApprovedExperimentError, OSError) as exc:
        print(f"approved experiment resolution failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "experiment_id": resolved.record.experiment_id,
                "canonical_premanifest_identity": resolved.record.canonical_premanifest_identity,
                "resolved_path": str(resolved.premanifest_path),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
