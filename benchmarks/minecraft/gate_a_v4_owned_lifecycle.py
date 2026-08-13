"""Minimal external producer for the Issue #497 run-owned namespace.

This module is not called by read-only admission.  A later authorized Gate A
operator must atomically create the stable namespace and these records before
any restore, child launch, Docker create/start, or output creation.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from collections.abc import Mapping
from types import MappingProxyType


LEASE_SCHEMA = "gate_a_v4_owned_lease.v1"
RUN_STATE_SCHEMA = "gate_a_v4_owned_run_state.v1"
CHILD_SCHEMA = "gate_a_v4_owned_children.v1"
LOCK_SCHEMA = "gate_a_v4_owned_lock.v1"
MAX_RECORD_BYTES = 4096
_TRANSITIONS = frozenset({
    ("prepared", "restore_started"),
    ("restore_started", "restore_finished"),
    ("restore_finished", "executor_started"),
    ("executor_started", "executor_finished"),
    ("executor_finished", "validation_started"),
    ("validation_started", "validation_finished"),
    ("restore_started", "cleanup_started"),
    ("restore_finished", "cleanup_started"),
    ("executor_started", "cleanup_started"),
    ("executor_finished", "cleanup_started"),
    ("validation_started", "cleanup_started"),
    ("validation_finished", "cleanup_started"),
    ("cleanup_started", "postflight_verified"),
    ("postflight_verified", "success_clean"),
    ("postflight_verified", "failed_clean"),
})


class OwnedLifecycleError(RuntimeError):
    pass


NAMESPACE_NAME = ".villageragent.minecraft-judged-production-v4.gate-a.diagonal-s17-baseline_open"
_HANDLE_AUTHORITY = object()


class OwnedRunHandle:
    """Opaque retained-FD identity for one permanently non-reusable run."""

    def __init__(self, *, parent_fd, namespace_fd, binding, binding_identity,
                 parent_identity, namespace_identity, lock_identity, _authority=None):
        if _authority is not _HANDLE_AUTHORITY:
            raise OwnedLifecycleError("owned run handle construction rejected")
        self.__parent_fd = parent_fd
        self.__namespace_fd = namespace_fd
        self.__binding = MappingProxyType(dict(binding))
        self.__binding_identity = binding_identity
        self.__parent_identity = parent_identity
        self.__namespace_identity = namespace_identity
        self.__lock_identity = lock_identity
        self.__closed = False
        self.__released = False
        self.__blocked = False
        self._registered = {}

    @property
    def parent_fd(self):
        return self.__parent_fd

    @property
    def namespace_fd(self):
        return self.__namespace_fd

    @property
    def binding(self):
        return self.__binding

    @property
    def binding_identity(self):
        return self.__binding_identity

    @property
    def parent_identity(self):
        return self.__parent_identity

    @property
    def namespace_identity(self):
        return self.__namespace_identity

    @property
    def lock_identity(self):
        return self.__lock_identity

    @property
    def closed(self):
        return self.__closed

    @property
    def released(self):
        return self.__released

    @property
    def blocked(self):
        return self.__blocked

    def close(self) -> None:
        if not self.__closed:
            os.close(self.__namespace_fd)
            os.close(self.__parent_fd)
            self.__closed = True

    def _mark_released(self):
        self.__released = True

    def _mark_blocked(self):
        self.__blocked = True


def _common(binding: dict) -> dict:
    required = {
        "experiment_id", "gate", "run_id", "lease_id",
        "execution_revision", "premanifest_canonical",
    }
    if (
        not isinstance(binding, Mapping) or set(binding) != required
        or binding.get("experiment_id") != "minecraft-judged-production-v4"
        or binding.get("gate") != "A"
        or binding.get("run_id") != "diagonal-s17-baseline_open"
        or binding.get("execution_revision") != "25113661a6b09761ab47a05bd70bd8f0386e2b67"
        or binding.get("premanifest_canonical") != "bffaedbe296d18b1c4d0cd7bc0d073d011566e3415133d464e65492bb9c13f3a"
        or not isinstance(binding.get("lease_id"), str)
        or len(binding["lease_id"]) != 64
        or any(character not in "0123456789abcdef" for character in binding["lease_id"])
    ):
        raise OwnedLifecycleError("owned lifecycle binding rejected")
    return {key: binding[key] for key in sorted(required)}


def _write_exclusive(directory_fd: int, name: str, value: dict) -> None:
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    if len(encoded) > MAX_RECORD_BYTES:
        raise OwnedLifecycleError("owned lifecycle record rejected")
    try:
        descriptor = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600, dir_fd=directory_fd,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        raise OwnedLifecycleError("owned lifecycle creation rejected") from None


def _read_record(directory_fd: int, name: str) -> dict:
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(MAX_RECORD_BYTES + 1)
        value = json.loads(raw)
    except (OSError, ValueError, TypeError, UnicodeError):
        raise OwnedLifecycleError("owned lifecycle record rejected") from None
    if not raw or len(raw) > MAX_RECORD_BYTES or not isinstance(value, dict):
        raise OwnedLifecycleError("owned lifecycle record rejected")
    return value


def _replace_record(directory_fd: int, name: str, value: dict) -> None:
    temporary = name + ".next"
    _write_exclusive(directory_fd, temporary, value)
    try:
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    except OSError:
        raise OwnedLifecycleError("owned lifecycle transition rejected") from None


def _reserve_namespace(parent_fd: int, binding: dict) -> int:
    """Atomically reserve the only Gate A namespace before any other effect."""
    try:
        os.mkdir(NAMESPACE_NAME, 0o700, dir_fd=parent_fd)
        descriptor = os.open(
            NAMESPACE_NAME, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except OSError:
        raise OwnedLifecycleError("owned namespace reservation rejected") from None
    try:
        _initialize_records(descriptor, binding)
        os.fsync(descriptor)
        return descriptor
    except Exception:
        try:
            run_state = _read_record(descriptor, "run-state.json")
            _replace_record(descriptor, "run-state.json", {
                **run_state, "state": "blocked_cleanup",
                "generation": run_state.get("generation", 0) + 1,
            })
        except Exception:
            pass
        try:
            lease = _read_record(descriptor, "lease.json")
            _replace_record(descriptor, "lease.json", {**lease, "state": "blocked"})
        except Exception:
            pass
        os.close(descriptor)
        raise


def _initialize_records(directory_fd: int, binding: dict) -> None:
    """Create the one-shot initial records; no process-control API is exposed."""
    if type(directory_fd) is not int or directory_fd < 0:
        raise OwnedLifecycleError("owned lifecycle binding rejected")
    common = _common(binding)
    _write_exclusive(directory_fd, "lease.json", {
        "schema_version": LEASE_SCHEMA, **common, "state": "reserved",
    })
    _write_exclusive(directory_fd, "run-state.json", {
        "schema_version": RUN_STATE_SCHEMA, **common, "state": "prepared", "generation": 0,
    })
    _write_exclusive(directory_fd, "children.json", {
        "schema_version": CHILD_SCHEMA, **common, "generation": 0,
        "registered_total": 0, "reaped_total": 0, "children": [],
    })


def _register_process_group(directory_fd: int, binding: dict, identity: dict) -> None:
    """Register the exact externally supervised process group before launch handoff."""
    if set(identity) != {"pid", "start_ticks", "pgid", "session_id"} or any(
        type(identity[key]) is not int or identity[key] <= 0 for key in identity
    ):
        raise OwnedLifecycleError("owned child identity rejected")
    record = _read_record(directory_fd, "children.json")
    common = _common(binding)
    expected = {
        "schema_version": CHILD_SCHEMA, **common, "generation": 0,
        "registered_total": 0, "reaped_total": 0, "children": [],
    }
    if record != expected:
        raise OwnedLifecycleError("owned child registry rejected")
    child = {**identity, "role": "runtime_process_group", "state": "registered"}
    _replace_record(directory_fd, "children.json", {
        **expected, "generation": 1, "registered_total": 1, "children": [child],
    })


def _mark_process_group_reaped(directory_fd: int, binding: dict, identity: dict) -> None:
    if set(identity) != {"pid", "start_ticks", "pgid", "session_id"} or any(
        type(identity[key]) is not int or identity[key] <= 0 for key in identity
    ):
        raise OwnedLifecycleError("owned child identity rejected")
    record = _read_record(directory_fd, "children.json")
    common = _common(binding)
    child = {**identity, "role": "runtime_process_group", "state": "registered"}
    expected = {
        "schema_version": CHILD_SCHEMA, **common, "generation": 1,
        "registered_total": 1, "reaped_total": 0, "children": [child],
    }
    if record != expected:
        raise OwnedLifecycleError("owned child registry rejected")
    _replace_record(directory_fd, "children.json", {
        **expected, "generation": 2, "reaped_total": 1, "children": [],
    })


def _identity(info) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)


def acquire_owned_run(parent_fd: int, binding: dict) -> OwnedRunHandle:
    """Acquire namespace, exclusive lock, and active lease before the first effect."""
    if type(parent_fd) is not int or parent_fd < 0:
        raise OwnedLifecycleError("owned lifecycle binding rejected")
    common = _common(binding)
    try:
        retained_parent = os.dup(parent_fd)
        parent_info = os.fstat(retained_parent)
        try:
            parent_path = Path(os.readlink(f"/proc/self/fd/{retained_parent}"))
        except OSError:
            raise OwnedLifecycleError("owned lifecycle parent rejected") from None
        expected_parent = FIXED_PRIVATE_PARENT.resolve(strict=True)
        expected_info = os.stat(expected_parent, follow_symlinks=False)
        if (
            parent_path != expected_parent
            or not stat.S_ISDIR(parent_info.st_mode)
            or _identity(parent_info) != _identity(expected_info)
            or parent_info.st_uid != os.geteuid()
        ):
            raise OwnedLifecycleError("owned lifecycle parent rejected")
        namespace_fd = _reserve_namespace(retained_parent, binding)
        namespace_info = os.fstat(namespace_fd)
        _write_exclusive(namespace_fd, "lock", {
            "schema_version": LOCK_SCHEMA, **common, "state": "acquired",
        })
        lock_info = os.stat("lock", dir_fd=namespace_fd, follow_symlinks=False)
        lease = {"schema_version": LEASE_SCHEMA, **common, "state": "reserved"}
        if _read_record(namespace_fd, "lease.json") != lease:
            raise OwnedLifecycleError("owned lease identity rejected")
        _replace_record(namespace_fd, "lease.json", {**lease, "state": "active"})
        handle = OwnedRunHandle(
            parent_fd=retained_parent, namespace_fd=namespace_fd, binding=dict(binding),
            binding_identity=tuple(sorted(common.items())),
            parent_identity=_identity(parent_info),
            namespace_identity=_identity(namespace_info), lock_identity=_identity(lock_info),
            _authority=_HANDLE_AUTHORITY,
        )
        validate_owned_run(handle)
        return handle
    except Exception:
        namespace_descriptor = locals().get("namespace_fd")
        if isinstance(namespace_descriptor, int):
            try:
                run_state = _read_record(namespace_descriptor, "run-state.json")
                _replace_record(namespace_descriptor, "run-state.json", {
                    **run_state, "state": "blocked_cleanup",
                    "generation": run_state.get("generation", 0) + 1,
                })
            except Exception:
                pass
            try:
                lease_record = _read_record(namespace_descriptor, "lease.json")
                _replace_record(namespace_descriptor, "lease.json", {**lease_record, "state": "blocked"})
            except Exception:
                pass
        for descriptor_name in ("namespace_fd", "retained_parent"):
            descriptor = locals().get(descriptor_name)
            if isinstance(descriptor, int):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        raise


def validate_owned_run(handle: OwnedRunHandle) -> None:
    if not isinstance(handle, OwnedRunHandle) or handle.closed:
        raise OwnedLifecycleError("owned run handle rejected")
    try:
        parent = os.fstat(handle.parent_fd)
        namespace = os.fstat(handle.namespace_fd)
        pathname = os.stat(NAMESPACE_NAME, dir_fd=handle.parent_fd, follow_symlinks=False)
        lock = os.stat("lock", dir_fd=handle.namespace_fd, follow_symlinks=False)
    except OSError:
        raise OwnedLifecycleError("owned run identity drift") from None
    if (
        _identity(parent) != handle.parent_identity
        or _identity(namespace) != handle.namespace_identity
        or _identity(pathname) != handle.namespace_identity
        or _identity(lock) != handle.lock_identity
        or not stat.S_ISDIR(pathname.st_mode)
        or not stat.S_ISREG(lock.st_mode)
        or tuple(sorted(_common(handle.binding).items())) != handle.binding_identity
    ):
        raise OwnedLifecycleError("owned run identity drift")
    common = _common(handle.binding)
    expected_lease_state = "blocked" if handle.blocked else ("released" if handle.released else "active")
    if _read_record(handle.namespace_fd, "lock") != {
        "schema_version": LOCK_SCHEMA, **common, "state": "acquired",
    } or _read_record(handle.namespace_fd, "lease.json") != {
        "schema_version": LEASE_SCHEMA, **common, "state": expected_lease_state,
    }:
        raise OwnedLifecycleError("owned run identity drift")
    run_state = _read_record(handle.namespace_fd, "run-state.json")
    if (
        set(run_state) != {"schema_version", *common, "state", "generation"}
        or run_state.get("schema_version") != RUN_STATE_SCHEMA
        or any(run_state.get(name) != value for name, value in common.items())
        or run_state.get("state") not in {
            "prepared", "restore_started", "restore_finished", "executor_started",
            "executor_finished", "validation_started", "validation_finished",
            "cleanup_started", "postflight_verified", "success_clean", "failed_clean",
            "blocked_cleanup",
        }
        or type(run_state.get("generation")) is not int or run_state["generation"] < 0
    ):
        raise OwnedLifecycleError("owned run identity drift")
    _children_record(handle)


def transition_run(handle: OwnedRunHandle, expected_state: str, next_state: str) -> None:
    validate_owned_run(handle)
    common = _common(handle.binding)
    record = _read_record(handle.namespace_fd, "run-state.json")
    if (
        set(record) != {"schema_version", *common, "state", "generation"}
        or record.get("schema_version") != RUN_STATE_SCHEMA
        or any(record.get(key) != value for key, value in common.items())
        or record.get("state") != expected_state
        or type(record.get("generation")) is not int
        or record["generation"] < 0
        or (expected_state, next_state) not in _TRANSITIONS
    ):
        raise OwnedLifecycleError("owned run transition rejected")
    _replace_record(handle.namespace_fd, "run-state.json", {
        **record, "state": next_state, "generation": record["generation"] + 1,
    })


def _children_record(handle: OwnedRunHandle) -> dict:
    record = _read_record(handle.namespace_fd, "children.json")
    common = _common(handle.binding)
    children = record.get("children")
    required = {
        "schema_version", *common, "generation", "registered_total", "reaped_total", "children",
    }
    if (
        set(record) != required
        or record.get("schema_version") != CHILD_SCHEMA
        or any(record.get(name) != value for name, value in common.items())
        or type(record.get("generation")) is not int
        or type(record.get("registered_total")) is not int
        or type(record.get("reaped_total")) is not int
        or not isinstance(children, list)
        or record["registered_total"] < record["reaped_total"]
        or record["generation"] != record["registered_total"] + record["reaped_total"]
        or len(children) != record["registered_total"] - record["reaped_total"]
    ):
        raise OwnedLifecycleError("owned child registry rejected")
    identities = set()
    for child in children:
        if (
            not isinstance(child, dict)
            or set(child) != {"pid", "start_ticks", "pgid", "session_id", "role", "state"}
            or child.get("role") != "runtime_process_group" or child.get("state") != "registered"
            or any(type(child.get(name)) is not int or child[name] <= 0 for name in ("pid", "start_ticks", "pgid", "session_id"))
        ):
            raise OwnedLifecycleError("owned child registry rejected")
        identity = tuple(child[name] for name in ("pid", "start_ticks", "pgid", "session_id"))
        if identity in identities:
            raise OwnedLifecycleError("owned child registry rejected")
        identities.add(identity)
    if identities != set(handle._registered):
        raise OwnedLifecycleError("owned child registry rejected")
    return record


def register_owned_child(handle: OwnedRunHandle, identity: dict) -> None:
    validate_owned_run(handle)
    if set(identity) != {"pid", "start_ticks", "pgid", "session_id"} or any(
        type(identity[key]) is not int or identity[key] <= 0 for key in identity
    ):
        raise OwnedLifecycleError("owned child identity rejected")
    key = tuple(identity[name] for name in ("pid", "start_ticks", "pgid", "session_id"))
    if key in handle._registered:
        raise OwnedLifecycleError("owned child identity rejected")
    record = _children_record(handle)
    children = record.get("children")
    child = {**identity, "role": "runtime_process_group", "state": "registered"}
    if child in children:
        raise OwnedLifecycleError("owned child identity rejected")
    _replace_record(handle.namespace_fd, "children.json", {
        **record, "generation": record["generation"] + 1,
        "registered_total": record["registered_total"] + 1,
        "children": [*children, child],
    })
    handle._registered[key] = dict(identity)


def mark_owned_child_reaped(handle: OwnedRunHandle, identity: dict) -> None:
    validate_owned_run(handle)
    key = tuple(identity.get(name) for name in ("pid", "start_ticks", "pgid", "session_id"))
    if handle._registered.get(key) != identity:
        raise OwnedLifecycleError("owned child identity rejected")
    record = _children_record(handle)
    child = {**identity, "role": "runtime_process_group", "state": "registered"}
    children = record.get("children")
    if not isinstance(children, list) or children.count(child) != 1:
        raise OwnedLifecycleError("owned child registry rejected")
    _replace_record(handle.namespace_fd, "children.json", {
        **record, "generation": record["generation"] + 1,
        "reaped_total": record["reaped_total"] + 1,
        "children": [candidate for candidate in children if candidate != child],
    })
    del handle._registered[key]


def owned_child_count(handle: OwnedRunHandle) -> int:
    validate_owned_run(handle)
    record = _children_record(handle)
    children = record.get("children")
    if not isinstance(children, list) or len(children) != len(handle._registered):
        raise OwnedLifecycleError("owned child registry rejected")
    return len(children)


def release_owned_run(handle: OwnedRunHandle, postflight: dict, outcome: str) -> None:
    validate_owned_run(handle)
    if postflight != {
        "managed_containers": 0, "run_owned_children": 0,
        "runtime_result_reusable": False,
    } or outcome not in {"success", "failed"} or owned_child_count(handle) != 0:
        raise OwnedLifecycleError("owned clean release rejected")
    try:
        os.stat("runtime-result.json.tmp", dir_fd=handle.namespace_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    except OSError:
        raise OwnedLifecycleError("owned clean release rejected") from None
    else:
        raise OwnedLifecycleError("owned clean release rejected")
    common = _common(handle.binding)
    lease = _read_record(handle.namespace_fd, "lease.json")
    if lease != {"schema_version": LEASE_SCHEMA, **common, "state": "active"}:
        raise OwnedLifecycleError("owned clean release rejected")
    try:
        transition_run(handle, "postflight_verified", f"{outcome}_clean")
        _replace_record(handle.namespace_fd, "lease.json", {**lease, "state": "released"})
        lock_info = os.stat("lock", dir_fd=handle.namespace_fd, follow_symlinks=False)
        if not stat.S_ISREG(lock_info.st_mode):
            raise OSError
        os.unlink("lock", dir_fd=handle.namespace_fd)
        os.fsync(handle.namespace_fd)
    except OSError:
        raise OwnedLifecycleError("owned clean release rejected") from None
    handle._mark_released()


def block_owned_run(handle: OwnedRunHandle) -> None:
    lease_state = None
    try:
        lease_state = _read_record(handle.namespace_fd, "lease.json").get("state")
    except OwnedLifecycleError:
        pass
    if not handle.released and lease_state != "released":
        validate_owned_run(handle)
    common = _common(handle.binding)
    lease = _read_record(handle.namespace_fd, "lease.json")
    if set(lease) != {"schema_version", *common, "state"} or lease.get("schema_version") != LEASE_SCHEMA or any(
        lease.get(name) != value for name, value in common.items()
    ) or lease.get("state") not in {"active", "released", "blocked"}:
        raise OwnedLifecycleError("owned block transition rejected")
    if lease.get("state") != "blocked":
        _replace_record(handle.namespace_fd, "lease.json", {**lease, "state": "blocked"})
    record = _read_record(handle.namespace_fd, "run-state.json")
    if (
        set(record) != {"schema_version", *common, "state", "generation"}
        or record.get("schema_version") != RUN_STATE_SCHEMA
        or any(record.get(name) != value for name, value in common.items())
        or type(record.get("generation")) is not int
    ):
        raise OwnedLifecycleError("owned block transition rejected")
    if record.get("state") != "blocked_cleanup":
        _replace_record(handle.namespace_fd, "run-state.json", {
            **record, "state": "blocked_cleanup", "generation": record.get("generation", 0) + 1,
        })
    handle._mark_blocked()

# Compatibility names used by the v4 focused harness.
LifecycleError = OwnedLifecycleError
OneShotLifecycle = OwnedRunHandle
FIXED_PRIVATE_PARENT = Path("/tmp/opencode")
