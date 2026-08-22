"""Schema and admission validation for Minecraft collection plans."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class CollectionSpecError(ValueError):
    """Raised when a collection plan is malformed or unsafe to admit."""


COLLECTION_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class EnvironmentBindings:
    docker_host_env: str | None
    docker_context_env: str | None
    model_api_base_env: str
    model_api_key_env: str
    lock_root: str


@dataclass(frozen=True)
class ApprovedProductionLaneSpec:
    lane_id: str
    kind: str
    data_class: str
    approved_experiment: str
    control_plane_worktree: str
    control_plane_revision: str
    execution_worktree: str
    execution_revision: str
    python_executable: str
    output_root: str
    batch_count: int
    batch_timeout_seconds: float
    environment: EnvironmentBindings
    resource_groups: tuple[str, ...]


@dataclass(frozen=True)
class ExternallyManagedLaneSpec:
    lane_id: str
    kind: str
    data_class: str
    resource_groups: tuple[str, ...]


@dataclass(frozen=True)
class CollectionPlan:
    schema_version: int
    session_id: str
    max_parallel_lanes: int
    lanes: tuple[ApprovedProductionLaneSpec | ExternallyManagedLaneSpec, ...]


_TOP_KEYS = {"schema_version", "session_id", "max_parallel_lanes", "lanes"}
_APPROVED_KEYS = {
    "lane_id", "kind", "data_class", "approved_experiment", "control_plane_worktree",
    "control_plane_revision", "execution_worktree", "execution_revision",
    "python_executable", "output_root", "batch_count", "batch_timeout_seconds",
    "environment", "resource_groups",
}
_EXTERNAL_KEYS = {"lane_id", "kind", "data_class", "resource_groups"}
_ENV_KEYS = {
    "docker_host_env", "docker_context_env", "model_api_base_env",
    "model_api_key_env", "lock_root",
}
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


def parse_collection_plan(payload: str | bytes | Mapping[str, Any]) -> CollectionPlan:
    if isinstance(payload, (str, bytes)):
        try:
            raw = json.loads(payload, object_pairs_hook=_object_no_duplicates,
                             parse_constant=_reject_constant)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CollectionSpecError("collection plan must be valid JSON") from exc
    elif isinstance(payload, Mapping):
        raw = dict(payload)
    else:
        raise CollectionSpecError("collection plan must be JSON or a mapping")
    _keys(raw, _TOP_KEYS, "collection plan")
    lanes_raw = _array(raw["lanes"], "lanes")
    lanes = tuple(_parse_lane(item) for item in lanes_raw)
    plan = CollectionPlan(raw["schema_version"], raw["session_id"],
                          raw["max_parallel_lanes"], lanes)
    return validate_collection_plan(plan)


def load_collection_plan(path: str | Path) -> CollectionPlan:
    try:
        payload = Path(path).read_bytes()
    except OSError as exc:
        raise CollectionSpecError(f"unable to read collection plan: {path}") from exc
    return parse_collection_plan(payload)


def validate_collection_plan(plan: CollectionPlan) -> CollectionPlan:
    if not isinstance(plan, CollectionPlan):
        raise CollectionSpecError("plan must be a CollectionPlan")
    if isinstance(plan.schema_version, bool) or plan.schema_version != COLLECTION_SCHEMA_VERSION:
        raise CollectionSpecError(f"unsupported collection schema version: {plan.schema_version!r}")
    if not isinstance(plan.schema_version, int):
        raise CollectionSpecError("schema_version must be an integer")
    _identifier(plan.session_id, "session_id")
    _positive_int(plan.max_parallel_lanes, "max_parallel_lanes")
    if not isinstance(plan.lanes, tuple) or not plan.lanes:
        raise CollectionSpecError("lanes must be a non-empty array")
    if plan.max_parallel_lanes > len(plan.lanes):
        raise CollectionSpecError("max_parallel_lanes cannot exceed lane count")
    ids: set[str] = set()
    approved_paths: list[tuple[str, str]] = []
    for lane in plan.lanes:
        if not isinstance(lane, (ApprovedProductionLaneSpec, ExternallyManagedLaneSpec)):
            raise CollectionSpecError("lanes contain an unsupported lane object")
        if not isinstance(lane.kind, str):
            raise CollectionSpecError("lane kind must be a string")
        expected_kind = "approved-production" if isinstance(lane, ApprovedProductionLaneSpec) else "externally-managed"
        if lane.kind != expected_kind:
            raise CollectionSpecError(f"lane kind must be {expected_kind!r}")
        _identifier(lane.lane_id, "lane_id")
        if lane.lane_id in ids:
            raise CollectionSpecError(f"duplicate lane_id: {lane.lane_id}")
        ids.add(lane.lane_id)
        if not isinstance(lane.resource_groups, tuple) or any(not isinstance(group, str) for group in lane.resource_groups):
            raise CollectionSpecError(f"lane {lane.lane_id} resource_groups must be a tuple of strings")
        if not lane.resource_groups or len(set(lane.resource_groups)) != len(lane.resource_groups):
            raise CollectionSpecError(f"lane {lane.lane_id} resource_groups must be nonempty and unique")
        for group in lane.resource_groups:
            _resource_group(group)
        if isinstance(lane, ApprovedProductionLaneSpec):
            _validate_approved(lane, approved_paths)
        else:
            _identifier(lane.data_class, "data_class")
    return plan


def collection_plan_to_dict(plan: CollectionPlan) -> dict[str, Any]:
    validate_collection_plan(plan)
    return {
        "schema_version": plan.schema_version,
        "session_id": plan.session_id,
        "max_parallel_lanes": plan.max_parallel_lanes,
        "lanes": [_lane_dict(lane) for lane in plan.lanes],
    }


def collection_plan_sha256(plan: CollectionPlan) -> str:
    encoded = json.dumps(collection_plan_to_dict(plan), sort_keys=True,
                         separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _parse_lane(raw: Any) -> ApprovedProductionLaneSpec | ExternallyManagedLaneSpec:
    if not isinstance(raw, dict):
        raise CollectionSpecError("lane must be an object")
    kind = raw.get("kind")
    if kind == "approved-production":
        _keys(raw, _APPROVED_KEYS, "approved production lane")
        environment = raw["environment"]
        _keys(environment, _ENV_KEYS, "environment")
        env = EnvironmentBindings(**environment)
        return ApprovedProductionLaneSpec(
            **{key: raw[key] for key in _APPROVED_KEYS - {"environment", "resource_groups"}},
            environment=env, resource_groups=_tuple(raw["resource_groups"], "resource_groups"),
        )
    if kind == "externally-managed":
        _keys(raw, _EXTERNAL_KEYS, "externally managed lane")
        return ExternallyManagedLaneSpec(
            lane_id=raw["lane_id"], kind=raw["kind"], data_class=raw["data_class"],
            resource_groups=_tuple(raw["resource_groups"], "resource_groups"))
    raise CollectionSpecError(f"unsupported lane kind: {kind!r}")


def _validate_approved(lane: ApprovedProductionLaneSpec, paths: list[tuple[str, str]]) -> None:
    if not isinstance(lane.approved_experiment, str):
        raise CollectionSpecError("approved_experiment must be a string")
    _identifier(lane.approved_experiment, "approved_experiment")
    if not isinstance(lane.data_class, str):
        raise CollectionSpecError("data_class must be a string")
    _identifier(lane.data_class, "data_class")
    for name in ("control_plane_worktree", "execution_worktree", "python_executable", "output_root"):
        if not isinstance(getattr(lane, name), str):
            raise CollectionSpecError(f"{name} must be a string")
    canonical = {name: _absolute_path(getattr(lane, name), name)
                 for name in ("control_plane_worktree", "execution_worktree", "python_executable", "output_root")}
    for name in ("control_plane_revision", "execution_revision"):
        if not isinstance(getattr(lane, name), str) or not _SHA.fullmatch(getattr(lane, name)):
            raise CollectionSpecError(f"{name} must be a full lowercase Git SHA")
    _positive_int(lane.batch_count, "batch_count")
    if isinstance(lane.batch_timeout_seconds, bool) or not isinstance(lane.batch_timeout_seconds, (int, float)) or not math.isfinite(lane.batch_timeout_seconds) or lane.batch_timeout_seconds <= 0:
        raise CollectionSpecError("batch_timeout_seconds must be a positive finite number")
    env = lane.environment
    _validate_environment(env)
    docker = [env.docker_host_env, env.docker_context_env]
    if not any(docker):
        raise CollectionSpecError("at least one Docker environment reference is required")
    env_names = []
    for name in ("docker_host_env", "docker_context_env", "model_api_base_env", "model_api_key_env"):
        value = getattr(env, name)
        if value is None and name in ("model_api_base_env", "model_api_key_env"):
            raise CollectionSpecError(f"{name} is required")
        if value is not None and (not isinstance(value, str) or not _ENV_NAME.fullmatch(value)):
            raise CollectionSpecError(f"{name} must be a valid environment variable name")
        if value is not None:
            env_names.append(value)
    if len(env_names) != len(set(env_names)):
        raise CollectionSpecError("environment variable references must be distinct")
    lock_root = _absolute_path(env.lock_root, "lock_root")
    all_paths = (("control_plane_worktree", canonical["control_plane_worktree"]),
                 ("execution_worktree", canonical["execution_worktree"]),
                 ("output_root", canonical["output_root"]), ("lock_root", lock_root))
    for index, (name, path) in enumerate(all_paths):
        for other_name, other_path in all_paths[index + 1:]:
            _check_path_overlap(path, name, other_path, other_name)
        for old_path, old_name in paths:
            _check_path_overlap(path, f"{lane.lane_id}:{name}", old_path, old_name)
    paths.extend((path, f"{lane.lane_id}:{name}") for name, path in all_paths)


def _lane_dict(lane: ApprovedProductionLaneSpec | ExternallyManagedLaneSpec) -> dict[str, Any]:
    result = dict(vars(lane))
    if isinstance(lane, ApprovedProductionLaneSpec):
        result["environment"] = dict(vars(lane.environment))
    result["resource_groups"] = list(lane.resource_groups)
    return result


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CollectionSpecError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise CollectionSpecError(f"nonfinite JSON constant: {value}")


def _keys(raw: Any, expected: set[str], context: str) -> None:
    if not isinstance(raw, dict):
        raise CollectionSpecError(f"{context} must be an object")
    actual = set(raw)
    if actual != expected:
        raise CollectionSpecError(f"{context} schema mismatch; missing={sorted(expected - actual)}, extra={sorted(actual - expected)}")


def _array(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise CollectionSpecError(f"{context} must be an array")
    return value


def _tuple(value: Any, context: str) -> tuple[str, ...]:
    values = _array(value, context)
    if any(not isinstance(item, str) for item in values):
        raise CollectionSpecError(f"{context} must contain strings")
    return tuple(values)


def _positive_int(value: Any, context: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CollectionSpecError(f"{context} must be a positive integer")


def _identifier(value: Any, context: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip() or not _IDENTIFIER.fullmatch(value):
        raise CollectionSpecError(f"{context} must be a nonempty, unpadded identifier")


def _absolute_path(value: Any, context: str) -> str:
    if (not isinstance(value, str) or value == "/" or not value.startswith("/") or
            value.endswith("/") or "//" in value or "\\" in value or "~" in value or
            any(ord(char) < 32 or ord(char) == 127 for char in value) or
            any(part in {"", ".", ".."} for part in value.split("/")[1:])):
        raise CollectionSpecError(f"{context} must be an absolute, normalized POSIX path")
    return value


def _check_path_overlap(left: str, left_name: str, right: str, right_name: str) -> None:
    if left == right or left.startswith(right + "/") or right.startswith(left + "/"):
        raise CollectionSpecError(f"path overlap: {left_name} and {right_name}")


def _resource_group(value: Any) -> None:
    if not isinstance(value, str) or value.count(":") != 1:
        raise CollectionSpecError("resource groups must be namespaced as kind:value")
    kind, name = value.split(":")
    if not kind or not name or not _IDENTIFIER.fullmatch(kind) or not _IDENTIFIER.fullmatch(name):
        raise CollectionSpecError("resource groups must be namespaced as kind:value")


def _validate_environment(env: Any) -> None:
    if not isinstance(env, EnvironmentBindings):
        raise CollectionSpecError("environment must be EnvironmentBindings")
    for name in ("docker_host_env", "docker_context_env"):
        value = getattr(env, name)
        if value is not None and not isinstance(value, str):
            raise CollectionSpecError(f"{name} must be a string or null")
    for name in ("model_api_base_env", "model_api_key_env", "lock_root"):
        if not isinstance(getattr(env, name), str):
            raise CollectionSpecError(f"{name} must be a string")
