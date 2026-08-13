"""Read-only Gate A admission scoped to experiment-owned resources only."""
from __future__ import annotations

import sys

if __name__ == "__main__" and (not sys.flags.isolated or not sys.dont_write_bytecode):
    sys.stdout.write('{"attempts":0,"execution_flags":{"canary":false,"five_run":false,"matrix":false,"production":false},"phase_id":"source_authentication","reason_code":"unexpected_failure","schema_version":"gate_a_v4_owned_admission.v2","status":"admission_failed"}\n')
    raise SystemExit(3)

import argparse
import hashlib
import http.client
import json
import os
import re
import signal
import stat
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any, Callable, Mapping


SCHEMA_VERSION = "gate_a_v4_owned_admission.v2"
FIXED_REVISION = "25113661a6b09761ab47a05bd70bd8f0386e2b67"
FIXED_CHILD_MANIFEST = "ce8c30e13ddef9251d64a3f833625e509dd9590b163229f52fe585444794ae5d"
FIXED_CHILD_ASSETS = 125
FIXED_RUNTIME_DIGEST = "sha256:25441b6e08ce2eff2a71dd6330ff4ddfaa6e5c9f1aa89e508e2580a16b262e0f"
FIXED_RUNTIME_IMAGE = "docker.io/itzg/minecraft-server@sha256:5b3e96bcd7dace8ab7be89c245dc9ba0b1573fdef2f01d3e101ac40e7843fa70"
FIXED_PREMANIFEST_BYTES = "222afe434cace4e7609cddaae578284ba1d2a1b1ed0dd927a4a6155ade71192f"
FIXED_PREMANIFEST_CANONICAL = "bffaedbe296d18b1c4d0cd7bc0d073d011566e3415133d464e65492bb9c13f3a"
FIXED_PREMANIFEST_MODE = 0o444
FIXED_DOCKER_CONTRACT = "ebf181d73d28e24ec8d257d06f3107d1f25211bfb26dedfd999507870fb41d01"
FIXED_MODEL_DIGEST = "4eb23ef187e2c5462566d6a1d3bbbc2f1346d0b4327cbb66d58fffbcc9b2b05c"
FIXED_MODEL_ENDPOINT = "http://10.255.255.5:11434"
RUN_ID = "diagonal-s17-baseline_open"
BASELINE_ARCHIVE_SHA256 = "644707660d4fb073016830f2f89e78a1c348d7845d4964326c21a6076a137c1b"
BASELINE_TREE_SHA256 = "3e743096c7b0a5bde0784c126bf9a36f0b3bd38901c964d6d5a3118181ad3594"
HOST_PRIVATE_PARENT = Path("/tmp/opencode")
FIXED_GIT_EXECUTABLE = "/nix/store/c0277k5giric1mn9dklllavbzvxl6hzb-git-2.53.0/bin/git"
MAX_JSON_BYTES = 65536
OWNED_DOCKER_LABEL_FILTERS = (
    "label=org.villageragent.minecraft.managed=true",
    "label=org.villageragent.experiment=minecraft-judged-production-v4",
    "label=org.villageragent.gate=A",
    "label=org.villageragent.run=diagonal-s17-baseline_open",
)
_LAUNCH_AUTHORITY = object()
MODEL_DIAGNOSTIC_REASONS = frozenset({
    "endpoint_unreachable", "endpoint_protocol_mismatch", "inventory_schema_mismatch",
    "model_missing", "model_name_ambiguous", "model_digest_missing",
    "model_digest_mismatch", "inventory_parser_mismatch", "inventory_match",
    "unexpected_failure",
})

ALLOWED_DIAGNOSTICS = frozenset({
    ("source_authentication", "source_hash_mismatch"),
    ("source_authentication", "unexpected_failure"),
    ("revision_worktree", "revision_mismatch"),
    ("revision_worktree", "worktree_dirty_or_not_detached"),
    ("revision_worktree", "unexpected_failure"),
    ("runtime_identity", "runtime_mismatch"),
    ("runtime_identity", "child_manifest_mismatch"),
    ("runtime_identity", "unexpected_failure"),
    ("premanifest_identity", "premanifest_mismatch"),
    ("premanifest_identity", "unexpected_failure"),
    ("model_inventory", "model_inventory_mismatch"),
    ("model_inventory", "unexpected_failure"),
    ("docker_identity", "docker_identity_mismatch"),
    ("docker_identity", "unexpected_failure"),
    ("managed_docker_residue", "managed_container_residue"),
    ("managed_docker_residue", "managed_name_or_label_collision"),
    ("managed_docker_residue", "unexpected_failure"),
    ("destination_parent", "destination_parent_invalid"),
    ("destination_parent", "destination_parent_identity_changed"),
    ("destination_parent", "destination_symlink_or_alias"),
    ("destination_parent", "unexpected_failure"),
    ("destination_absence", "destination_exists"),
    ("destination_absence", "unexpected_failure"),
    ("ownership_state", "ownership_state_present"),
    ("ownership_state", "ownership_state_malformed"),
    ("ownership_state", "run_owned_child_present"),
    ("ownership_state", "runtime_result_state_present"),
    ("ownership_state", "unexpected_failure"),
    ("baseline_identity", "baseline_mismatch"),
    ("baseline_identity", "unexpected_failure"),
    ("canary_derivation", "canary_mismatch"),
    ("canary_derivation", "unexpected_failure"),
    ("final_recheck", "final_recheck_failed"),
    ("final_recheck", "unexpected_failure"),
    ("admission_passed", "none"),
})
PHASE_IDS = frozenset(phase for phase, _ in ALLOWED_DIAGNOSTICS)
REASON_CODES = frozenset(reason for _, reason in ALLOWED_DIAGNOSTICS)


class OwnedAdmissionError(RuntimeError):
    """A sanitized fail-closed admission error."""

    def __init__(self, phase_id: str, reason_code: str):
        if (phase_id, reason_code) not in ALLOWED_DIAGNOSTICS or reason_code == "none":
            phase_id, reason_code = "final_recheck", "unexpected_failure"
        self.phase_id = phase_id
        self.reason_code = reason_code
        super().__init__(f"{phase_id}:{reason_code}")


class ModelInventoryDiagnosticError(RuntimeError):
    def __init__(self, reason_code: str):
        self.reason_code = reason_code if reason_code in MODEL_DIAGNOSTIC_REASONS else "unexpected_failure"
        super().__init__(self.reason_code)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        raise ModelInventoryDiagnosticError("endpoint_protocol_mismatch")


@dataclass(frozen=True)
class OwnedPaths:
    private_parent: Path
    namespace: Path
    output: Path
    lock: Path
    work: Path
    runtime_result: Path
    runtime_result_tmp: Path
    lease: Path
    run_state: Path
    child_registry: Path


@dataclass(frozen=True)
class AdmissionBindings:
    git: Callable[[], Mapping[str, Any]]
    runtime: Callable[[], Mapping[str, Any]]
    premanifest: Callable[[], Mapping[str, Any]]
    model: Callable[[], Mapping[str, Any]]
    docker: Callable[[], Mapping[str, Any]]


def owned_paths(private_parent: Path) -> OwnedPaths:
    namespace = private_parent / ".villageragent.minecraft-judged-production-v4.gate-a.diagonal-s17-baseline_open"
    runtime_result = namespace / "runtime-result.json"
    return OwnedPaths(
        private_parent=private_parent,
        namespace=namespace,
        output=namespace / "output",
        lock=namespace / "lock",
        work=namespace / "work",
        runtime_result=runtime_result,
        runtime_result_tmp=namespace / "runtime-result.json.tmp",
        lease=namespace / "lease.json",
        run_state=namespace / "run-state.json",
        child_registry=namespace / "children.json",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise OwnedAdmissionError("baseline_identity", "baseline_mismatch")
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        final = os.stat(path, follow_symlinks=False)
    except OSError:
        raise OwnedAdmissionError("baseline_identity", "baseline_mismatch") from None
    if (opened.st_dev, opened.st_ino, opened.st_size) != (final.st_dev, final.st_ino, final.st_size):
        raise OwnedAdmissionError("final_recheck", "final_recheck_failed")
    return digest.hexdigest()


def _absolute_unsymlinked(path: Path, *, exists: bool | None, phase_id: str,
                          invalid_reason: str, alias_reason: str) -> Path:
    if not path.is_absolute() or path != Path(os.path.normpath(str(path))):
        raise OwnedAdmissionError(phase_id, alias_reason)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise OwnedAdmissionError(phase_id, alias_reason)
        if not current.exists():
            break
    if exists is True and not path.exists():
        raise OwnedAdmissionError(phase_id, invalid_reason)
    if exists is False and (path.exists() or path.is_symlink()):
        raise OwnedAdmissionError("destination_absence", "destination_exists")
    return path


def _parent_identity(parent: Path) -> tuple[int, int, int, int]:
    _absolute_unsymlinked(
        parent, exists=True, phase_id="destination_parent",
        invalid_reason="destination_parent_invalid", alias_reason="destination_symlink_or_alias",
    )
    try:
        info = os.stat(parent, follow_symlinks=False)
    except OSError:
        raise OwnedAdmissionError("destination_parent", "destination_parent_invalid") from None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise OwnedAdmissionError("destination_parent", "destination_parent_invalid")
    return info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)


def _inside(candidate: Path, root: Path) -> bool:
    return candidate == root or root in candidate.parents


def _mount_unescape(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def _read_mount_table() -> tuple[tuple[str, PurePosixPath, Path], ...]:
    try:
        raw = Path("/proc/self/mountinfo").read_bytes()
    except OSError:
        raise OwnedAdmissionError("destination_parent", "destination_parent_invalid") from None
    if not raw or len(raw) > 1024 * 1024 or b"\x00" in raw:
        raise OwnedAdmissionError("destination_parent", "destination_parent_invalid")
    entries = []
    try:
        for line in raw.decode("utf-8").splitlines():
            fields = line.split()
            separator = fields.index("-")
            if separator < 6:
                raise ValueError
            device = fields[2]
            root = PurePosixPath(_mount_unescape(fields[3]))
            mountpoint = Path(_mount_unescape(fields[4]))
            if not device or not root.is_absolute() or not mountpoint.is_absolute():
                raise ValueError
            entries.append((device, root, mountpoint))
    except (UnicodeError, ValueError):
        raise OwnedAdmissionError("destination_parent", "destination_parent_invalid") from None
    if not entries or len(entries) > 4096:
        raise OwnedAdmissionError("destination_parent", "destination_parent_invalid")
    return tuple(entries)


def _backing_coordinate(path: Path, table: tuple[tuple[str, PurePosixPath, Path], ...]):
    candidates = [entry for entry in table if _inside(path, entry[2])]
    if not candidates:
        raise OwnedAdmissionError("destination_parent", "destination_parent_invalid")
    device, root, mountpoint = max(candidates, key=lambda entry: len(entry[2].parts))
    relative = path.relative_to(mountpoint)
    return device, root.joinpath(*relative.parts)


def _visible_aliases(path: Path, table: tuple[tuple[str, PurePosixPath, Path], ...]) -> frozenset[Path]:
    device, backing = _backing_coordinate(path, table)
    aliases = {path}
    for entry_device, entry_root, mountpoint in table:
        if entry_device != device:
            continue
        try:
            relative = backing.relative_to(entry_root)
        except ValueError:
            continue
        aliases.add(mountpoint.joinpath(*relative.parts))
    return frozenset(aliases)


def _open_parent(paths: OwnedPaths):
    identity = _parent_identity(paths.private_parent)
    try:
        descriptor = os.open(paths.private_parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(descriptor)
    except OSError:
        raise OwnedAdmissionError("destination_parent", "destination_parent_invalid") from None
    if (info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)) != identity:
        os.close(descriptor)
        raise OwnedAdmissionError("destination_parent", "destination_parent_identity_changed")
    return descriptor, identity


def _validate_paths(paths: OwnedPaths, worktrees: tuple[Path, ...], parent_fd: int,
                    parent_identity: tuple[int, int, int, int]) -> None:
    expected = owned_paths(paths.private_parent)
    if paths != expected:
        raise OwnedAdmissionError("destination_parent", "destination_symlink_or_alias")
    info = os.fstat(parent_fd)
    if (info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)) != parent_identity:
        raise OwnedAdmissionError("destination_parent", "destination_parent_identity_changed")
    try:
        path_info = os.stat(paths.private_parent, follow_symlinks=False)
    except OSError:
        raise OwnedAdmissionError("destination_parent", "destination_parent_identity_changed") from None
    if (path_info.st_dev, path_info.st_ino, path_info.st_uid, stat.S_IMODE(path_info.st_mode)) != parent_identity:
        raise OwnedAdmissionError("destination_parent", "destination_parent_identity_changed")
    destinations = (
        paths.namespace, paths.output, paths.lock, paths.work, paths.runtime_result,
        paths.runtime_result_tmp, paths.lease, paths.run_state, paths.child_registry,
    )
    if len(set(destinations)) != len(destinations):
        raise OwnedAdmissionError("destination_parent", "destination_symlink_or_alias")
    for destination in destinations:
        if any(_inside(destination, worktree) for worktree in worktrees):
            raise OwnedAdmissionError("destination_parent", "destination_symlink_or_alias")
    ancestry_identities = set()
    ancestor = paths.private_parent
    while True:
        try:
            ancestor_info = os.stat(ancestor, follow_symlinks=False)
        except OSError:
            raise OwnedAdmissionError("destination_parent", "destination_parent_invalid") from None
        ancestry_identities.add((ancestor_info.st_dev, ancestor_info.st_ino))
        if ancestor == ancestor.parent:
            break
        ancestor = ancestor.parent
    for worktree in worktrees:
        try:
            _absolute_unsymlinked(
                worktree, exists=True, phase_id="destination_parent",
                invalid_reason="destination_parent_invalid", alias_reason="destination_symlink_or_alias",
            )
            worktree_info = os.stat(worktree, follow_symlinks=False)
        except OSError:
            raise OwnedAdmissionError("destination_parent", "destination_symlink_or_alias") from None
        if (worktree_info.st_dev, worktree_info.st_ino) in ancestry_identities:
            raise OwnedAdmissionError("destination_parent", "destination_symlink_or_alias")
    mount_table = _read_mount_table()
    parent_aliases = _visible_aliases(paths.private_parent, mount_table)
    for worktree in worktrees:
        worktree_aliases = _visible_aliases(worktree, mount_table)
        if any(_inside(parent_alias, worktree_alias) for parent_alias in parent_aliases for worktree_alias in worktree_aliases):
            raise OwnedAdmissionError("destination_parent", "destination_symlink_or_alias")
    try:
        namespace_info = os.stat(paths.namespace.name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError:
        raise OwnedAdmissionError("ownership_state", "ownership_state_malformed") from None
    if not stat.S_ISDIR(namespace_info.st_mode):
        raise OwnedAdmissionError("destination_parent", "destination_symlink_or_alias")
    _classify_owned_namespace(parent_fd, paths.namespace.name)


def _classify_owned_namespace(parent_fd: int, namespace_name: str) -> None:
    """Classify only the bounded experiment-owned namespace; never inspect PIDs."""
    try:
        namespace_fd = os.open(
            namespace_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
        try:
            entries = set()
            with os.scandir(namespace_fd) as iterator:
                for entry in iterator:
                    entries.add(entry.name)
                    if len(entries) > 16:
                        raise OwnedAdmissionError("ownership_state", "ownership_state_malformed")
            known = {
                "output", "lock", "work", "runtime-result.json", "runtime-result.json.tmp",
                "lease.json", "run-state.json", "children.json",
            }
            if entries - known:
                raise OwnedAdmissionError("ownership_state", "ownership_state_malformed")
            for entry in entries:
                entry_info = os.stat(entry, dir_fd=namespace_fd, follow_symlinks=False)
                if stat.S_ISLNK(entry_info.st_mode):
                    raise OwnedAdmissionError("destination_parent", "destination_symlink_or_alias")
            if entries & {"runtime-result.json", "runtime-result.json.tmp"}:
                raise OwnedAdmissionError("ownership_state", "runtime_result_state_present")
            if "children.json" in entries:
                record = _read_owned_record(namespace_fd, "children.json")
                children = record.get("children")
                required_record_keys = {
                    "schema_version", "experiment_id", "gate", "run_id", "lease_id",
                    "execution_revision", "premanifest_canonical", "generation",
                    "registered_total", "reaped_total", "children",
                }
                if (
                    set(record) != required_record_keys
                    or record.get("schema_version") != "gate_a_v4_owned_children.v1"
                    or record.get("experiment_id") != "minecraft-judged-production-v4"
                    or record.get("gate") != "A"
                    or record.get("run_id") != RUN_ID
                    or record.get("execution_revision") != FIXED_REVISION
                    or record.get("premanifest_canonical") != FIXED_PREMANIFEST_CANONICAL
                    or not isinstance(record.get("lease_id"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", record["lease_id"])
                    or type(record.get("generation")) is not int
                    or type(record.get("registered_total")) is not int
                    or type(record.get("reaped_total")) is not int
                    or record["registered_total"] < record["reaped_total"]
                    or record["generation"] != record["registered_total"] + record["reaped_total"]
                    or not isinstance(children, list)
                    or len(children) != record["registered_total"] - record["reaped_total"]
                ):
                    raise OwnedAdmissionError("ownership_state", "ownership_state_malformed")
                for child in children:
                    if (
                        not isinstance(child, dict)
                        or set(child) != {"pid", "start_ticks", "pgid", "session_id", "role", "state"}
                        or child.get("role") != "runtime_process_group"
                        or child.get("state") != "registered"
                        or any(type(child.get(key)) is not int or child[key] <= 0 for key in ("pid", "start_ticks", "pgid", "session_id"))
                    ):
                        raise OwnedAdmissionError("ownership_state", "ownership_state_malformed")
                if any(child["state"] == "registered" for child in children):
                    raise OwnedAdmissionError("ownership_state", "run_owned_child_present")
                raise OwnedAdmissionError("ownership_state", "ownership_state_present")
            if entries & {"lease.json", "run-state.json", "lock"}:
                raise OwnedAdmissionError("ownership_state", "ownership_state_present")
            if entries & {"output", "work"}:
                raise OwnedAdmissionError("destination_absence", "destination_exists")
            raise OwnedAdmissionError("ownership_state", "ownership_state_present")
        finally:
            os.close(namespace_fd)
    except OwnedAdmissionError:
        raise
    except OSError:
        raise OwnedAdmissionError("ownership_state", "ownership_state_malformed") from None


def _read_owned_record(directory_fd: int, name: str) -> dict[str, Any]:
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(4097)
        value = json.loads(raw)
    except (OSError, ValueError, TypeError, UnicodeError):
        raise OwnedAdmissionError("ownership_state", "ownership_state_malformed") from None
    if not raw or len(raw) > 4096 or not isinstance(value, dict):
        raise OwnedAdmissionError("ownership_state", "ownership_state_malformed")
    return value


def _validate_git(value: Mapping[str, Any]) -> tuple[Path, ...]:
    if set(value) != {"revision", "detached", "clean", "worktrees"}:
        raise OwnedAdmissionError("revision_worktree", "revision_mismatch")
    if value.get("revision") != FIXED_REVISION:
        raise OwnedAdmissionError("revision_worktree", "revision_mismatch")
    if value.get("detached") is not True or value.get("clean") is not True:
        raise OwnedAdmissionError("revision_worktree", "worktree_dirty_or_not_detached")
    worktrees = value.get("worktrees")
    if not isinstance(worktrees, tuple) or not worktrees:
        raise OwnedAdmissionError("revision_worktree", "worktree_dirty_or_not_detached")
    result = []
    for item in worktrees:
        path = Path(item)
        if not path.is_absolute() or path != Path(os.path.normpath(str(path))):
            raise OwnedAdmissionError("revision_worktree", "worktree_dirty_or_not_detached")
        result.append(path)
    return tuple(result)


def _validate_runtime(value: Mapping[str, Any]) -> None:
    if value.get("child_manifest_sha256") != FIXED_CHILD_MANIFEST or value.get("child_assets") != FIXED_CHILD_ASSETS:
        raise OwnedAdmissionError("runtime_identity", "child_manifest_mismatch")
    if dict(value) != {
        "child_manifest_sha256": FIXED_CHILD_MANIFEST,
        "child_assets": FIXED_CHILD_ASSETS,
        "runtime_digest": FIXED_RUNTIME_DIGEST,
        "runtime_image": FIXED_RUNTIME_IMAGE,
    }:
        raise OwnedAdmissionError("runtime_identity", "runtime_mismatch")


def _validate_premanifest(value: Mapping[str, Any]) -> None:
    if value.get("canary") != RUN_ID:
        raise OwnedAdmissionError("canary_derivation", "canary_mismatch")
    if (
        value.get("baseline_archive_sha256") != BASELINE_ARCHIVE_SHA256
        or value.get("baseline_tree_sha256") != BASELINE_TREE_SHA256
    ):
        raise OwnedAdmissionError("baseline_identity", "baseline_mismatch")
    if dict(value) != {
        "byte_sha256": FIXED_PREMANIFEST_BYTES,
        "canonical_identity": FIXED_PREMANIFEST_CANONICAL,
        "mode": FIXED_PREMANIFEST_MODE,
        "canary": RUN_ID,
        "baseline_archive_sha256": BASELINE_ARCHIVE_SHA256,
        "baseline_tree_sha256": BASELINE_TREE_SHA256,
    }:
        raise OwnedAdmissionError("premanifest_identity", "premanifest_mismatch")


def _validate_model(value: Mapping[str, Any]) -> None:
    if dict(value) != {
        "provider": "ollama", "name": "gemma4:12b",
        "digest": FIXED_MODEL_DIGEST, "endpoint": FIXED_MODEL_ENDPOINT,
        "matched_count": 1,
    }:
        raise OwnedAdmissionError("model_inventory", "model_inventory_mismatch")


def _validate_docker(value: Mapping[str, Any]) -> None:
    if value.get("managed_container_count", 0) != 0:
        raise OwnedAdmissionError("managed_docker_residue", "managed_container_residue")
    if value.get("managed_labeled_count", 0) != 0:
        raise OwnedAdmissionError("managed_docker_residue", "managed_name_or_label_collision")
    required = {
        "contract_sha256": FIXED_DOCKER_CONTRACT,
        "connection_category": "current_uid_rootless_unix_socket",
        "authorization_category": "current_uid_owner_read_write_no_world",
        "daemon_identity_category": "pinned_rootless_daemon",
        "executable_identity_category": "pinned_trusted_executable",
        "pinned_image": "matched",
        "managed_container_count": 0,
    }
    if not isinstance(value, Mapping) or any(value.get(key) != expected for key, expected in required.items()):
        raise OwnedAdmissionError("docker_identity", "docker_identity_mismatch")
    if set(value) - (set(required) | {"identity", "unrelated_container_count", "managed_labeled_count"}):
        raise OwnedAdmissionError("docker_identity", "docker_identity_mismatch")
    if "identity" not in value:
        raise OwnedAdmissionError("docker_identity", "docker_identity_mismatch")


def _observe(phase_id: str, callback: Callable[[], Mapping[str, Any]]) -> dict[str, Any]:
    try:
        return dict(callback())
    except OwnedAdmissionError:
        raise
    except Exception:
        raise OwnedAdmissionError(phase_id, "unexpected_failure") from None


def _snapshot(bindings: AdmissionBindings):
    git = _observe("revision_worktree", bindings.git)
    runtime = _observe("runtime_identity", bindings.runtime)
    premanifest = _observe("premanifest_identity", bindings.premanifest)
    model = _observe("model_inventory", bindings.model)
    docker = _observe("docker_identity", bindings.docker)
    worktrees = _validate_git(git)
    _validate_runtime(runtime)
    _validate_premanifest(premanifest)
    _validate_model(model)
    _validate_docker(docker)
    return git, runtime, premanifest, model, docker, worktrees


def _counters():
    return {
        "attempts": 0, "restore": 0, "executor": 0, "validation": 0,
        "docker_create_start": 0, "minecraft": 0, "model_generation": 0,
        "output_creation": 0,
    }


def read_only_admission(paths: OwnedPaths, bindings: AdmissionBindings) -> dict[str, Any]:
    first = _snapshot(bindings)
    parent_fd, parent_identity = _open_parent(paths)
    try:
        _validate_paths(paths, first[-1], parent_fd, parent_identity)
        second = _snapshot(bindings)
        if first[:4] != second[:4] or first[4] != second[4]:
            raise OwnedAdmissionError("final_recheck", "final_recheck_failed")
        _validate_paths(paths, second[-1], parent_fd, parent_identity)
        final_git = _observe("final_recheck", bindings.git)
        try:
            final_worktrees = _validate_git(final_git)
        except OwnedAdmissionError:
            raise OwnedAdmissionError("final_recheck", "final_recheck_failed") from None
        if final_git != second[0]:
            raise OwnedAdmissionError("final_recheck", "final_recheck_failed")
        _validate_paths(paths, final_worktrees, parent_fd, parent_identity)
    finally:
        os.close(parent_fd)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "admission_passed",
        "phase_id": "admission_passed",
        "reason_code": "none",
        "ownership_mechanism": "exclusive_absent_stable_namespace",
        "namespace_state": "absent",
        "managed_containers": 0,
        "run_owned_children": 0,
        "lock_state": "absent",
        "lease_state": "absent",
        "run_state": "absent",
        "output_state": "absent",
        "work_state": "absent",
        "runtime_result_state": "absent",
        "model_inventory": "match",
        "runtime_identity": "match",
        "premanifest_identity": "match",
        "docker_identity": "match",
        "baseline_identity": "match",
        "canary": RUN_ID,
        "final_recheck": "passed",
        "attempts": 0,
        "counters": _counters(),
        "execution_flags": {"canary": False, "five_run": False, "matrix": False, "production": False},
    }


def diagnostic_admission(paths: OwnedPaths, bindings: AdmissionBindings) -> dict[str, Any]:
    """Run the existing admission and return only bounded failure categories."""
    try:
        return read_only_admission(paths, bindings)
    except OwnedAdmissionError as exc:
        phase_id, reason_code = exc.phase_id, exc.reason_code
    except Exception:
        phase_id, reason_code = "final_recheck", "unexpected_failure"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "admission_failed",
        "phase_id": phase_id,
        "reason_code": reason_code,
        "attempts": 0,
        "execution_flags": {"canary": False, "five_run": False, "matrix": False, "production": False},
    }


def _git_environment():
    return {
        "PATH": "/run/current-system/sw/bin:/usr/bin:/bin",
        "HOME": "/nonexistent/va-gate-a-git-home",
        "LC_ALL": "C",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "core.fsmonitor",
        "GIT_CONFIG_VALUE_0": "false",
        "GIT_CONFIG_KEY_1": "core.hooksPath",
        "GIT_CONFIG_VALUE_1": "/dev/null",
    }


def _git(root: Path, *args, check=True):
    return subprocess.run(
        [FIXED_GIT_EXECUTABLE, *args], cwd=root, env=_git_environment(), stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=30, check=check,
    )


def _host_git(root: Path):
    if _git(root, "rev-parse", "--show-toplevel").stdout.strip() != str(root):
        raise OwnedAdmissionError("revision_worktree", "revision_mismatch")
    raw = _git(root, "worktree", "list", "--porcelain").stdout
    worktrees = tuple(
        line.removeprefix("worktree ") for line in raw.splitlines()
        if line.startswith("worktree ")
    )
    return {
        "revision": _git(root, "rev-parse", "HEAD").stdout.strip(),
        "detached": _git(root, "symbolic-ref", "-q", "HEAD", check=False).returncode != 0,
        "clean": not bool(_git(root, "status", "--porcelain", "--untracked-files=all").stdout),
        "worktrees": worktrees,
    }


def _git_blob(root: Path, relative: str) -> bytes:
    try:
        result = subprocess.run(
            [FIXED_GIT_EXECUTABLE, "show", f"{FIXED_REVISION}:{relative}"], cwd=root,
            env=_git_environment(), stdin=subprocess.DEVNULL,
            capture_output=True, timeout=30, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        raise OwnedAdmissionError("source_authentication", "source_hash_mismatch") from None
    if not result.stdout or len(result.stdout) > 2 * 1024 * 1024:
        raise OwnedAdmissionError("source_authentication", "source_hash_mismatch")
    return result.stdout


def _host_runtime(root: Path):
    package_name = "_gate_a_owned_runtime_env"
    package = ModuleType(package_name)
    package.__path__ = []
    package.__package__ = package_name
    sys.modules[package_name] = package
    for short_name, relative in (
        ("runtime_paths", "env/runtime_paths.py"),
        ("runtime_execution", "env/runtime_execution.py"),
    ):
        name = f"{package_name}.{short_name}"
        source = _git_blob(root, relative)
        module = ModuleType(name)
        module.__file__ = str(root / relative)
        module.__package__ = package_name
        sys.modules[name] = module
        exec(compile(source, module.__file__, "exec"), module.__dict__, module.__dict__)
    runtime_execution = sys.modules[f"{package_name}.runtime_execution"]
    execution = runtime_execution.RuntimeExecution.resolve(root)
    execution.verify()
    components = {
        "adapter": hashlib.sha256(_git_blob(root, "benchmarks/minecraft/docker_runtime.py")).hexdigest(),
        "diagnostics": hashlib.sha256(_git_blob(root, "benchmarks/minecraft/docker_diagnostics.py")).hexdigest(),
        "marker_verification": hashlib.sha256(_git_blob(root, "benchmarks/minecraft/restart_marker_verification.py")).hexdigest(),
        "image": "sha256:5b3e96bcd7dace8ab7be89c245dc9ba0b1573fdef2f01d3e101ac40e7843fa70",
        "image_source_revision": "162bd9b5f19a0de2870407a4406506aeb0fe5a99",
        "node_dependencies": execution.asset("package_lock").sha256,
        "probe": execution.asset("docker_probe").sha256,
        "server_jar": "b26727069ef5f61c704add9a378ac90e3d271fd7876c0bd3dcfbe9fd0bec4d96",
        "server_metadata_sha1": "ed548106acf3ac7e8205a6ee8fd2710facfa164f",
        "execution_manifest": execution.manifest_sha256,
    }
    runtime_digest = "sha256:" + hashlib.sha256(
        json.dumps(components, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    return {
        "child_manifest_sha256": execution.manifest_sha256,
        "child_assets": len(execution.assets),
        "runtime_digest": runtime_digest,
        "runtime_image": FIXED_RUNTIME_IMAGE,
    }


def _read_json(path: Path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise ValueError
            raw = stream.read(MAX_JSON_BYTES + 1)
        final = os.stat(path, follow_symlinks=False)
        if not raw or len(raw) > MAX_JSON_BYTES:
            raise ValueError
        if (opened.st_dev, opened.st_ino, opened.st_size) != (final.st_dev, final.st_ino, final.st_size):
            raise ValueError
        value = json.loads(raw)
    except (OSError, ValueError, TypeError, UnicodeError):
        raise OwnedAdmissionError("premanifest_identity", "premanifest_mismatch") from None
    if not isinstance(value, dict):
        raise OwnedAdmissionError("premanifest_identity", "premanifest_mismatch")
    return raw, value, stat.S_IMODE(opened.st_mode)


def _host_premanifest(root: Path, path: Path):
    raw, value, mode = _read_json(path)
    runs = [run for run in value.get("runs", ()) if run.get("run_id") == RUN_ID]
    baselines = [item for item in value.get("baselines", ()) if item.get("baseline_id") == "baseline_open"]
    if len(runs) != 1 or len(baselines) != 1:
        raise OwnedAdmissionError("canary_derivation", "canary_mismatch")
    run, baseline = runs[0], baselines[0]
    try:
        registry = json.loads(_git_blob(
            root, "benchmarks/minecraft/assets/issue_443/baseline_registry.json",
        ))
    except (ValueError, TypeError, UnicodeError):
        raise OwnedAdmissionError("baseline_identity", "baseline_mismatch") from None
    approved = [item for item in registry.get("baselines", ()) if item.get("baseline_id") == "baseline_open"]
    if len(approved) != 1:
        raise OwnedAdmissionError("baseline_identity", "baseline_mismatch")
    item = approved[0]
    snapshot_path = Path(run.get("snapshot_path", ""))
    if snapshot_path.is_absolute() or snapshot_path != Path(os.path.normpath(str(snapshot_path))):
        raise OwnedAdmissionError("baseline_identity", "baseline_mismatch")
    archive_path = _absolute_unsymlinked(
        root / snapshot_path, exists=True, phase_id="baseline_identity",
        invalid_reason="baseline_mismatch", alias_reason="baseline_mismatch",
    )
    if not archive_path.is_file() or not stat.S_ISREG(os.lstat(archive_path).st_mode):
        raise OwnedAdmissionError("baseline_identity", "baseline_mismatch")
    if (
        value.get("lifecycle_state") != "finalized"
        or value.get("matrix_id") != "minecraft-judged-production-v4"
        or value.get("revision") != FIXED_REVISION
        or value.get("premanifest_sha256") != FIXED_PREMANIFEST_CANONICAL
        or value.get("runtime") != {"name": "minecraft-1.19.2-local", "image": FIXED_RUNTIME_IMAGE, "digest": FIXED_RUNTIME_DIGEST}
        or value.get("model") != {"provider": "ollama", "name": "gemma4:12b", "digest": FIXED_MODEL_DIGEST}
        or len(value.get("runs", ())) != 12
        or {key: run.get(key) for key in ("order", "variant", "seed", "baseline_id", "snapshot_sha256")} != {
            "order": 4, "variant": "diagonal", "seed": 17,
            "baseline_id": "baseline_open", "snapshot_sha256": BASELINE_ARCHIVE_SHA256,
        }
        or baseline.get("sha256") != BASELINE_ARCHIVE_SHA256
        or item.get("archive_sha256") != BASELINE_ARCHIVE_SHA256
        or item.get("tree_sha256") != BASELINE_TREE_SHA256
        or _sha256(archive_path) != BASELINE_ARCHIVE_SHA256
    ):
        raise OwnedAdmissionError("premanifest_identity", "premanifest_mismatch")
    return {
        "byte_sha256": hashlib.sha256(raw).hexdigest(),
        "canonical_identity": value.get("premanifest_sha256"),
        "mode": mode,
        "canary": run.get("run_id"),
        "baseline_archive_sha256": item.get("archive_sha256"),
        "baseline_tree_sha256": item.get("tree_sha256"),
    }


def _model_diagnostic_result(reason_code: str, *, endpoint: str, model_name: str,
                             model_digest: str) -> dict[str, Any]:
    if reason_code not in MODEL_DIAGNOSTIC_REASONS:
        reason_code = "unexpected_failure"
    return {
        "status": "match" if reason_code == "inventory_match" else "mismatch",
        "reason_code": reason_code,
        "endpoint": endpoint,
        "model_name": model_name,
        "model_digest": model_digest,
        "attempts": 0,
    }


def _read_model_response(response) -> bytes:
    previous_handler = signal.getsignal(signal.SIGALRM)

    def deadline_exceeded(signum, frame):
        del signum, frame
        raise TimeoutError

    signal.signal(signal.SIGALRM, deadline_exceeded)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 8.0)
    try:
        return response.read(MAX_JSON_BYTES + 1)
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


def _fetch_model_inventory(opener=None):
    request = urllib.request.Request(f"{FIXED_MODEL_ENDPOINT}/api/tags", method="GET")
    if opener is None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=8) as response:
            if response.status // 100 != 2:
                raise ModelInventoryDiagnosticError("endpoint_protocol_mismatch")
            raw = _read_model_response(response)
            if not raw or len(raw) > MAX_JSON_BYTES:
                raise ModelInventoryDiagnosticError("endpoint_protocol_mismatch")
            try:
                return json.loads(raw)
            except (ValueError, TypeError, UnicodeError):
                raise ModelInventoryDiagnosticError("endpoint_protocol_mismatch") from None
    except ModelInventoryDiagnosticError:
        raise
    except urllib.error.HTTPError:
        raise ModelInventoryDiagnosticError("endpoint_protocol_mismatch") from None
    except http.client.RemoteDisconnected:
        raise ModelInventoryDiagnosticError("endpoint_unreachable") from None
    except http.client.HTTPException:
        raise ModelInventoryDiagnosticError("endpoint_protocol_mismatch") from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise ModelInventoryDiagnosticError("endpoint_unreachable") from None


def _classify_model_inventory(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"models"} or not isinstance(payload["models"], list):
        return _model_diagnostic_result(
            "inventory_schema_mismatch", endpoint="reachable",
            model_name="not_checked", model_digest="not_checked",
        )
    matches = [
        item for item in payload["models"]
        if isinstance(item, dict) and item.get("name") == "gemma4:12b"
    ]
    if not matches:
        return _model_diagnostic_result(
            "model_missing", endpoint="reachable", model_name="missing", model_digest="not_checked",
        )
    if len(matches) != 1:
        return _model_diagnostic_result(
            "model_name_ambiguous", endpoint="reachable", model_name="ambiguous", model_digest="not_checked",
        )
    if (
        "digest" in matches[0] and matches[0]["digest"] is not None
        and not isinstance(matches[0]["digest"], str)
    ):
        return _model_diagnostic_result(
            "inventory_schema_mismatch", endpoint="reachable",
            model_name="approved_name_match", model_digest="not_checked",
        )
    digest = matches[0].get("digest")
    if not digest:
        return _model_diagnostic_result(
            "model_digest_missing", endpoint="reachable",
            model_name="approved_name_match", model_digest="missing",
        )
    if digest != FIXED_MODEL_DIGEST:
        return _model_diagnostic_result(
            "model_digest_mismatch", endpoint="reachable",
            model_name="approved_name_match", model_digest="mismatch",
        )
    return _model_diagnostic_result(
        "inventory_match", endpoint="reachable",
        model_name="approved_name_match", model_digest="match",
    )


def _admission_model_observation(payload: Any) -> Mapping[str, Any]:
    diagnosis = _classify_model_inventory(payload)
    if diagnosis["reason_code"] != "inventory_match":
        raise OwnedAdmissionError("model_inventory", "model_inventory_mismatch")
    return {
        "provider": "ollama", "name": "gemma4:12b", "digest": FIXED_MODEL_DIGEST,
        "endpoint": FIXED_MODEL_ENDPOINT, "matched_count": 1,
    }


def read_only_model_diagnostic(*, opener=None,
                               admission_parser=_admission_model_observation) -> dict[str, Any]:
    try:
        payload = _fetch_model_inventory(opener)
        diagnosis = _classify_model_inventory(payload)
        if diagnosis["reason_code"] != "inventory_match":
            return diagnosis
        try:
            observed = dict(admission_parser(payload))
        except Exception:
            return _model_diagnostic_result(
                "inventory_parser_mismatch", endpoint="reachable",
                model_name="approved_name_match", model_digest="match",
            )
        expected = {
            "provider": "ollama", "name": "gemma4:12b", "digest": FIXED_MODEL_DIGEST,
            "endpoint": FIXED_MODEL_ENDPOINT, "matched_count": 1,
        }
        if observed != expected:
            return _model_diagnostic_result(
                "inventory_parser_mismatch", endpoint="reachable",
                model_name="approved_name_match", model_digest="match",
            )
        return diagnosis
    except ModelInventoryDiagnosticError as exc:
        endpoint = "unreachable" if exc.reason_code == "endpoint_unreachable" else "reachable"
        return _model_diagnostic_result(
            exc.reason_code, endpoint=endpoint, model_name="not_checked", model_digest="not_checked",
        )
    except Exception:
        return _model_diagnostic_result(
            "unexpected_failure", endpoint="unreachable",
            model_name="not_checked", model_digest="not_checked",
        )


def _host_model():
    return _admission_model_observation(_fetch_model_inventory())


def _load_authenticated(path: Path, expected_sha256: str, name: str):
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != expected_sha256:
        raise OwnedAdmissionError("source_authentication", "source_hash_mismatch")
    module = ModuleType(name)
    module.__file__ = str(path)
    if name in sys.modules:
        raise OwnedAdmissionError("source_authentication", "unexpected_failure")
    sys.modules[name] = module
    try:
        exec(compile(source, str(path), "exec"), module.__dict__, module.__dict__)
    except Exception:
        if sys.modules.get(name) is module:
            del sys.modules[name]
        raise
    return module


def _host_bindings(root: Path, premanifest: Path, contract: Path, docker_executable: Path):
    docker_contract = _load_authenticated(
        contract, FIXED_DOCKER_CONTRACT, "authenticated_docker_contract",
    )
    docker_environment = docker_contract.bind_environment()
    initial_identity = []

    def docker_snapshot():
        previous = initial_identity[0] if initial_identity else None
        inspected = docker_contract.inspect_docker_contract(
            docker_executable, docker_environment, previous=previous, require_clean=False,
        )
        if not initial_identity:
            initial_identity.append(inspected.identity)
        runner = docker_contract.make_bound_runner(
            docker_executable, initial_identity[0], docker_environment,
        )
        image_result = runner([
            "docker", "image", "inspect", FIXED_RUNTIME_IMAGE, "--format", "{{json .}}",
        ], capture_output=True, text=True, timeout=30, check=True)
        try:
            image = json.loads(image_result.stdout)
            image_id = image["Id"]
            digests = image["RepoDigests"]
            source_revision = image["Config"]["Labels"]["org.opencontainers.image.revision"]
        except (AttributeError, KeyError, TypeError, ValueError):
            raise OwnedAdmissionError("docker_identity", "docker_identity_mismatch") from None
        image_matched = (
            image_id == "sha256:5b3e96bcd7dace8ab7be89c245dc9ba0b1573fdef2f01d3e101ac40e7843fa70"
            and any(str(value).rsplit("@", 1)[-1] == image_id for value in digests)
            and source_revision == "162bd9b5f19a0de2870407a4406506aeb0fe5a99"
        )
        labels_result = runner([
            "docker", "ps", "-a",
            *(item for ownership_filter in OWNED_DOCKER_LABEL_FILTERS
              for item in ("--filter", ownership_filter)),
            "--format", "{{.Names}}",
        ], capture_output=True, text=True, timeout=30, check=True)
        labeled_names = {line for line in labels_result.stdout.splitlines() if line}
        labeled = tuple(sorted(labeled_names))
        if len(labeled) > 128 or any(not name.isascii() or len(name) > 128 for name in labeled):
            raise OwnedAdmissionError("managed_docker_residue", "managed_name_or_label_collision")
        final = docker_contract.inspect_docker_contract(
            docker_executable, docker_environment, previous=initial_identity[0], require_clean=False,
        )
        report = dict(final.report)
        return {
            "contract_sha256": FIXED_DOCKER_CONTRACT,
            "connection_category": report.get("connection_category"),
            "authorization_category": report.get("authorization_category"),
            "daemon_identity_category": report.get("daemon_identity_category"),
            "executable_identity_category": report.get("executable_identity_category"),
            "pinned_image": "matched" if image_matched else "mismatch",
            "managed_container_count": len(labeled),
            "managed_labeled_count": len(labeled),
            "identity": final.identity,
        }

    return AdmissionBindings(
        git=lambda: _host_git(root),
        runtime=lambda: _host_runtime(root),
        premanifest=lambda: _host_premanifest(root, premanifest),
        model=_host_model,
        docker=docker_snapshot,
    )


class _BoundedArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        del message
        raise OwnedAdmissionError("source_authentication", "unexpected_failure")


def _arguments(argv=None):
    parser = _BoundedArgumentParser(
        description="Issue #499 bounded read-only model/owned-resource diagnostic", add_help=False,
    )
    parser.add_argument("--execution-root")
    parser.add_argument("--premanifest")
    parser.add_argument("--private-parent")
    parser.add_argument("--docker-contract")
    parser.add_argument("--docker-executable")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--read-only-admission", action="store_true")
    modes.add_argument("--read-only-model-diagnostic", action="store_true")
    args = parser.parse_args(argv)
    paths = (args.execution_root, args.premanifest, args.private_parent,
             args.docker_contract, args.docker_executable)
    if args.read_only_model_diagnostic and any(value is not None for value in paths):
        parser.error("model diagnostic arguments rejected")
    if args.read_only_admission and any(value is None for value in paths):
        parser.error("owned admission arguments rejected")
    return args


def main(argv=None, *, _authority=None):
    try:
        if _authority is not _LAUNCH_AUTHORITY:
            raise OwnedAdmissionError("source_authentication", "source_hash_mismatch")
        args = _arguments(argv)
        if args.read_only_model_diagnostic:
            result = read_only_model_diagnostic()
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            return 0 if result["status"] == "match" else 3
        root = _absolute_unsymlinked(
            Path(args.execution_root), exists=True, phase_id="revision_worktree",
            invalid_reason="revision_mismatch", alias_reason="worktree_dirty_or_not_detached",
        )
        premanifest = _absolute_unsymlinked(
            Path(args.premanifest), exists=True, phase_id="premanifest_identity",
            invalid_reason="premanifest_mismatch", alias_reason="premanifest_mismatch",
        )
        parent = _absolute_unsymlinked(
            Path(args.private_parent), exists=True, phase_id="destination_parent",
            invalid_reason="destination_parent_invalid", alias_reason="destination_symlink_or_alias",
        )
        if parent != HOST_PRIVATE_PARENT:
            raise OwnedAdmissionError("destination_parent", "destination_parent_invalid")
        contract = _absolute_unsymlinked(
            Path(args.docker_contract), exists=True, phase_id="source_authentication",
            invalid_reason="source_hash_mismatch", alias_reason="source_hash_mismatch",
        )
        executable = _absolute_unsymlinked(
            Path(args.docker_executable), exists=True, phase_id="docker_identity",
            invalid_reason="docker_identity_mismatch", alias_reason="docker_identity_mismatch",
        )
        result = diagnostic_admission(
            owned_paths(parent), _host_bindings(root, premanifest, contract, executable),
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result["status"] == "admission_passed" else 3
    except OwnedAdmissionError as exc:
        print(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "status": "admission_failed",
            "phase_id": exc.phase_id,
            "reason_code": exc.reason_code,
            "attempts": 0,
            "execution_flags": {"canary": False, "five_run": False, "matrix": False, "production": False},
        }, sort_keys=True, separators=(",", ":")))
        return 3
    except Exception:
        print(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "status": "admission_failed",
            "phase_id": "final_recheck",
            "reason_code": "unexpected_failure",
            "attempts": 0,
            "execution_flags": {"canary": False, "five_run": False, "matrix": False, "production": False},
        }, sort_keys=True, separators=(",", ":")))
        return 3


if __name__ == "__main__":
    raise SystemExit("authenticated launcher required")
