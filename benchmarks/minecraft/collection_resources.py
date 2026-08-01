"""Read-only resource admission for approved Minecraft collection lanes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from .approved_experiment import ApprovedExperiment, get_approved_experiment
from .collection_spec import (
    COLLECTION_SCHEMA_VERSION,
    ApprovedProductionLaneSpec,
    CollectionPlan,
    CollectionSpecError,
    validate_collection_plan,
)


class CollectionResourceError(ValueError):
    """Raised when a declared collection resource cannot be admitted."""


GitRunner = Callable[..., subprocess.CompletedProcess[str]]
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENV = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C", "LC_ALL": "C",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                  ensure_ascii=True).encode()).hexdigest()


def _git_result(argv: Sequence[str], cwd: Path, runner: GitRunner | None) -> subprocess.CompletedProcess[str]:
    run = runner or subprocess.run
    try:
        result = run(["git", "-c", "core.fsmonitor=false", *argv], cwd=str(cwd), check=False,
                     text=True, capture_output=True, shell=False, timeout=15, env=_ENV)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CollectionResourceError("git inspection failed") from exc
    return result


def _git(argv: Sequence[str], cwd: Path, runner: GitRunner | None) -> str:
    result = _git_result(argv, cwd, runner)
    if result.returncode != 0:
        raise CollectionResourceError("git inspection failed")
    return result.stdout.strip()


def _declared_path(path: str | Path) -> Path:
    if not isinstance(path, (str, Path)) or not Path(path).is_absolute():
        raise CollectionResourceError("worktree path must be absolute")
    candidate = Path(path)
    lexical = Path(os.path.normpath(str(candidate)))
    if lexical != candidate or candidate.is_symlink() or not candidate.exists() or not candidate.is_dir():
        raise CollectionResourceError("worktree path must be an existing canonical directory")
    if candidate.resolve() != candidate:
        raise CollectionResourceError("worktree path must not contain symlink ancestors")
    return candidate


def _worktree_top(path: str | Path, runner: GitRunner | None) -> tuple[Path, Path, Path]:
    declared = _declared_path(path)
    reported = Path(_git(["rev-parse", "--show-toplevel"], declared, runner))
    reported = (declared / reported if not reported.is_absolute() else reported).resolve()
    if reported != declared:
        raise CollectionResourceError("declared path is not the Git worktree top-level")
    git_dir = Path(_git(["rev-parse", "--git-dir"], declared, runner))
    git_dir = (declared / git_dir if not git_dir.is_absolute() else git_dir).resolve()
    common = Path(_git(["rev-parse", "--git-common-dir"], declared, runner))
    common = (declared / common if not common.is_absolute() else common).resolve()
    return declared, git_dir, common


def _check_sha(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise CollectionResourceError(f"{label} must be a full lowercase Git SHA")
    return value


@dataclass(frozen=True)
class WorktreeAdmission:
    role: str
    revision: str
    fingerprint: str
    untracked_count: int = 0
    ignored_count: int = 0

    def __post_init__(self) -> None:
        _check_sha(self.revision, "revision")
        if not _SHA256.fullmatch(self.fingerprint):
            raise CollectionResourceError("worktree fingerprint must be SHA256")
        if (self.role not in {"control", "execution"}
                or isinstance(self.untracked_count, bool) or not isinstance(self.untracked_count, int)
                or self.untracked_count < 0 or isinstance(self.ignored_count, bool)
                or not isinstance(self.ignored_count, int) or self.ignored_count < 0):
            raise CollectionResourceError("invalid worktree admission")

    @property
    def has_untracked(self) -> bool:
        return self.untracked_count > 0

    @property
    def has_ignored(self) -> bool:
        return self.ignored_count > 0


@dataclass(frozen=True)
class WorktreePairAdmission:
    control: WorktreeAdmission
    execution: WorktreeAdmission
    lane_binding: str

    def __post_init__(self) -> None:
        if not isinstance(self.lane_binding, str) or not _SHA256.fullmatch(self.lane_binding):
            raise CollectionResourceError("lane binding must be SHA256")


ApprovalLoader = Callable[[str, str | Path | None], ApprovedExperiment]


def _lane_binding(spec: ApprovedProductionLaneSpec) -> str:
    return _hash({"lane_id": spec.lane_id, "approved_experiment": spec.approved_experiment,
                  "control_plane_worktree": spec.control_plane_worktree,
                  "control_plane_revision": spec.control_plane_revision,
                  "execution_worktree": spec.execution_worktree,
                  "execution_revision": spec.execution_revision,
                  "output_root": spec.output_root, "lock_root": spec.environment.lock_root,
                  "resource_groups": list(spec.resource_groups)})


def inspect_worktree(path: str | Path, *, role: str, expected_revision: str,
                    runner: GitRunner | None = None) -> WorktreeAdmission:
    if role not in {"control", "execution"}:
        raise CollectionResourceError("role must be control or execution")
    top, git_dir, _ = _worktree_top(path, runner)
    expected = _check_sha(expected_revision, f"{role} revision")
    actual = _check_sha(_git(["rev-parse", "HEAD"], top, runner), f"{role} revision")
    if actual != expected:
        raise CollectionResourceError(f"{role} revision differs from the approved revision")
    if role == "execution":
        symbolic = _git_result(["symbolic-ref", "--quiet", "--short", "HEAD"], top, runner)
        if symbolic.returncode == 0:
            raise CollectionResourceError("execution worktree must be detached")
        if symbolic.returncode != 1:
            raise CollectionResourceError("unable to inspect execution worktree state")
    status = _git(["-c", "status.showUntrackedFiles=all", "-c", "status.submoduleSummary=true",
                   "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching",
                   "--ignore-submodules=none"], top, runner)
    entries = tuple(line for line in status.splitlines() if line)
    tracked = tuple(
        line for line in entries
        if not line.startswith(("?? ", "!! "))
    )
    untracked_count = sum(line.startswith("?? ") for line in entries)
    ignored_count = sum(line.startswith("!! ") for line in entries)
    if tracked or (role == "execution" and (untracked_count or ignored_count)):
        raise CollectionResourceError(f"{role} worktree is not clean")
    fingerprint = _hash({"git_dir": str(git_dir), "path": str(top)})
    return WorktreeAdmission(role, actual, fingerprint, untracked_count, ignored_count)


def admit_worktrees(spec: ApprovedProductionLaneSpec, *, runner: GitRunner | None = None,
                    approval_loader: ApprovalLoader | None = None,
                    registry_dir: str | Path | None = None) -> WorktreePairAdmission:
    if not isinstance(spec, ApprovedProductionLaneSpec):
        raise CollectionResourceError("lane must be an ApprovedProductionLaneSpec")
    try:
        validate_collection_plan(CollectionPlan(
            COLLECTION_SCHEMA_VERSION,
            "worktree-admission",
            1,
            (spec,),
        ))
    except CollectionSpecError as exc:
        raise CollectionResourceError("lane schema admission failed") from exc
    loader = approval_loader or get_approved_experiment
    try:
        record = loader(spec.approved_experiment, registry_dir)
    except Exception as exc:
        raise CollectionResourceError("approved experiment admission failed") from exc
    if not isinstance(record, ApprovedExperiment) or record.experiment_id != spec.approved_experiment:
        raise CollectionResourceError("approved experiment identity mismatch")
    if spec.execution_revision != record.approved_source_revision:
        raise CollectionResourceError("execution revision does not match approved experiment")
    control_top, _, control_common = _worktree_top(spec.control_plane_worktree, runner)
    execution_top, _, execution_common = _worktree_top(spec.execution_worktree, runner)
    if control_top == execution_top:
        raise CollectionResourceError("control and execution worktrees must be distinct")
    if control_common != execution_common:
        raise CollectionResourceError("worktrees are not in the same repository")
    control = inspect_worktree(spec.control_plane_worktree, role="control",
                               expected_revision=spec.control_plane_revision, runner=runner)
    execution = inspect_worktree(spec.execution_worktree, role="execution",
                                 expected_revision=spec.execution_revision, runner=runner)
    return WorktreePairAdmission(control, execution, _lane_binding(spec))


@dataclass(frozen=True)
class DockerIdentity:
    fingerprint: str
    context: str
    server_version: str
    operating_system: str
    architecture: str
    rootless: bool

    def __post_init__(self) -> None:
        if (not isinstance(self.fingerprint, str) or not _SHA256.fullmatch(self.fingerprint)
                or not isinstance(self.context, str) or not isinstance(self.server_version, str)
                or not isinstance(self.operating_system, str) or not isinstance(self.architecture, str)
                or not isinstance(self.rootless, bool)):
            raise CollectionResourceError("Docker fingerprint must be SHA256")


def docker_identity(info: Mapping[str, object], context: str) -> DockerIdentity:
    keys = ("ID", "ServerVersion", "OperatingSystem", "Architecture", "Rootless")
    if (not isinstance(context, str) or not context
            or any(ch.isspace() or ch == "/" or ch == "\\" or ord(ch) < 32 for ch in context)):
        raise CollectionResourceError("invalid Docker context")
    if any(key not in info for key in keys):
        raise CollectionResourceError("incomplete Docker identity")
    daemon, version, system, arch, rootless = (info[key] for key in keys)
    if not isinstance(daemon, str) or not daemon or not all(isinstance(v, str) and v for v in (version, system, arch)):
        raise CollectionResourceError("invalid Docker identity fields")
    if not isinstance(rootless, bool):
        raise CollectionResourceError("Docker Rootless must be boolean")
    return DockerIdentity(_hash({"daemon_id": daemon}), context, version, system, arch, rootless)


@dataclass(frozen=True)
class ModelIdentity:
    provider: str
    name: str
    digest: str
    endpoint_fingerprint: str

    def __post_init__(self) -> None:
        if (not all(isinstance(value, str) and value for value in
                    (self.provider, self.name, self.digest))
                or not isinstance(self.endpoint_fingerprint, str)
                or not _SHA256.fullmatch(self.endpoint_fingerprint)):
            raise CollectionResourceError("endpoint fingerprint must be SHA256")

    @property
    def fingerprint(self) -> str:
        return _hash(vars(self))


def model_identity(provider: str, name: str, digest: str, endpoint: str) -> ModelIdentity:
    if not all(isinstance(value, str) and value for value in (provider, name, digest, endpoint)):
        raise CollectionResourceError("model identity fields must be non-empty strings")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise CollectionResourceError("invalid model endpoint") from exc
    if parsed.scheme.lower() not in {"http", "https"} or parsed.username is not None or parsed.password is not None:
        raise CollectionResourceError("model endpoint must be a safe HTTP(S) URL")
    if parsed.query or parsed.fragment or not parsed.hostname:
        raise CollectionResourceError("model endpoint must not contain query, fragment, or missing host")
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    if ":" in host:
        raise CollectionResourceError("IPv6 model endpoints are not supported")
    netloc = host if port is None or (scheme, port) in {("http", 80), ("https", 443)} else f"{host}:{port}"
    normalized = urlunsplit((scheme, netloc, parsed.path or "/", "", ""))
    return ModelIdentity(provider, name, digest, _hash({"endpoint": normalized}))


def path_fingerprint(path: str | Path) -> str:
    candidate = Path(path)
    if not candidate.is_absolute() or candidate == Path(candidate.anchor):
        raise CollectionResourceError("resource path must be absolute")
    canonical = Path(os.path.normpath(str(candidate)))
    resolved = candidate.resolve(strict=False)
    if canonical != candidate or resolved != canonical:
        raise CollectionResourceError("resource path must be canonical and free of symlink aliases")
    return _hash({"path": str(canonical)})


@dataclass(frozen=True)
class ObservedResourceFingerprint:
    resource_groups: frozenset[str] = field(default_factory=frozenset)
    docker_fingerprint: str | None = None
    model_endpoint_fingerprint: str | None = None
    execution_worktree_fingerprint: str | None = None
    control_worktree_fingerprint: str | None = None
    output_root_fingerprint: str | None = None
    lock_root_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.resource_groups, frozenset)
                or any(not isinstance(group, str) or not group or group.strip() != group
                       or any(ord(ch) < 32 for ch in group)
                       or group.count(":") != 1
                       or not all(part for part in group.split(":"))
                       for group in self.resource_groups)):
            raise CollectionResourceError("resource_groups must be a frozen set of valid names")
        for value in (self.docker_fingerprint, self.model_endpoint_fingerprint,
                      self.execution_worktree_fingerprint, self.control_worktree_fingerprint,
                      self.output_root_fingerprint, self.lock_root_fingerprint):
            if value is not None and (not isinstance(value, str) or not _SHA256.fullmatch(value)):
                raise CollectionResourceError("resource fingerprints must be SHA256")

    @property
    def conflict_tokens(self) -> frozenset[str]:
        tokens = set(self.resource_groups)
        values = (("docker-fingerprint", self.docker_fingerprint), ("model-endpoint", self.model_endpoint_fingerprint),
                  ("worktree", self.execution_worktree_fingerprint), ("worktree", self.control_worktree_fingerprint),
                  ("output-root", self.output_root_fingerprint), ("lock-root", self.lock_root_fingerprint))
        tokens.update(f"{prefix}:{value}" for prefix, value in values if value)
        return frozenset(tokens)


def resources_conflict(left: ObservedResourceFingerprint, right: ObservedResourceFingerprint) -> bool:
    """Return whether lanes share a scheduling resource; this does not invalidate plans."""
    return bool(left.conflict_tokens & right.conflict_tokens)


def observed_lane_resources(
    spec: ApprovedProductionLaneSpec,
    worktrees: WorktreePairAdmission,
    *,
    docker: DockerIdentity | None = None,
    model: ModelIdentity | None = None,
) -> ObservedResourceFingerprint:
    """Build the scheduler projection without exposing declared filesystem paths."""
    if not isinstance(spec, ApprovedProductionLaneSpec):
        raise CollectionResourceError("lane must be an ApprovedProductionLaneSpec")
    if not isinstance(worktrees, WorktreePairAdmission):
        raise CollectionResourceError("worktrees must be admitted worktrees")
    if worktrees.lane_binding != _lane_binding(spec):
        raise CollectionResourceError("worktree admission is bound to another lane")
    return ObservedResourceFingerprint(
        frozenset(spec.resource_groups),
        docker.fingerprint if docker is not None else None,
        model.endpoint_fingerprint if model is not None else None,
        worktrees.execution.fingerprint,
        worktrees.control.fingerprint,
        path_fingerprint(spec.output_root),
        path_fingerprint(spec.environment.lock_root),
    )
