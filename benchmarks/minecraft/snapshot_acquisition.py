from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol, Sequence

from benchmarks.minecraft.matrix_variants import VARIANT_ORDER, MovementTarget, get_movement_variant
from benchmarks.minecraft.position_contract import PositionConvention
from benchmarks.minecraft.world_snapshot import (
    WorldSnapshotDescriptor,
    canonical_world_tree_identity,
    restore_world_snapshot,
)


ACQUISITION_SCHEMA_VERSION = 1
WORLD_DIRECTORY = "world"
REQUIRED_WORLD_ENTRIES = ("level.dat", "region")
RUNTIME_LOCK_NAMES = frozenset({"session.lock", "server.lock", "world.lock"})
TEMPORARY_NAMES = frozenset({"uid.dat_old"})
TEMPORARY_SUFFIXES = (".lock", ".tmp", ".temp", ".swp", "~")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SOURCE_ARCHIVE_SHA256 = "8519378f5d71195ac67294acb318994ef660afdba92eada7289faa9be9f74673"
SOURCE_MARKER = f"src_{SOURCE_ARCHIVE_SHA256[:16]}"
_ABSOLUTE_PATH_RE = re.compile(r"(?:^|[\s\"'=])(?:/[A-Za-z0-9_.-]+/|[A-Za-z]:[\\/])")
_CREDENTIAL_RE = re.compile(
    r"(?i)(?:api[_-]?key|authorization|password|passwd|secret|token)\s*[:=]\s*[^\s,;}]+"
)


class SnapshotAcquisitionError(RuntimeError):
    pass


class UnsafeSnapshotSourceError(SnapshotAcquisitionError):
    pass


class SnapshotApprovalError(SnapshotAcquisitionError):
    pass


class AcquisitionRuntimeUnavailableError(SnapshotAcquisitionError):
    pass


@dataclass(frozen=True)
class BlockPlacement:
    x: int
    y: int
    z: int
    block: str

    def as_dict(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y, "z": self.z, "block": self.block}


@dataclass(frozen=True)
class ObstacleProfile:
    profile_id: str
    commands: tuple[str, ...]
    blocks: tuple[BlockPlacement, ...]
    opening: tuple[BlockPlacement, ...]

    @property
    def changed_block_count(self) -> int:
        return len(self.blocks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "commands": list(self.commands),
            "blocks": [block.as_dict() for block in self.blocks],
            "opening": [block.as_dict() for block in self.opening],
            "changed_block_count": self.changed_block_count,
        }


@dataclass(frozen=True)
class InventoryState:
    representation: str = "occupied_slots_v1"
    slots: tuple[tuple[int, str, int], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "representation": self.representation,
            "slots": [
                {"slot": slot, "item": item, "count": count}
                for slot, item, count in self.slots
            ],
        }


@dataclass(frozen=True)
class BaselineInitialState:
    position: MovementTarget
    yaw: float
    pitch: float
    dimension: str
    game_mode: str
    inventory: InventoryState
    health: int
    hunger: int
    time: int
    weather: str
    difficulty: str
    hostile_mobs_removed: bool
    hostile_mob_spawning: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "position": self.position.as_dict(),
            "yaw": self.yaw,
            "pitch": self.pitch,
            "dimension": self.dimension,
            "game_mode": self.game_mode,
            "inventory": self.inventory.as_dict(),
            "health": self.health,
            "hunger": self.hunger,
            "time": self.time,
            "weather": self.weather,
            "difficulty": self.difficulty,
            "hostile_mobs_removed": self.hostile_mobs_removed,
            "hostile_mob_spawning": self.hostile_mob_spawning,
        }


_CENTRAL_WALL_BLOCKS = tuple(
    BlockPlacement(12, y, z, "minecraft:stone")
    for y in range(-59, -56)
    for z in range(3, 8)
    if not (y in {-59, -58} and z == 7)
)
_CENTRAL_WALL_OPENING = tuple(
    BlockPlacement(12, y, 7, "minecraft:air") for y in (-59, -58)
)
central_wall_v1 = ObstacleProfile(
    profile_id="central_wall_v1",
    commands=(
        "/fill 12 -60 3 12 -57 7 minecraft:stone",
        "/fill 12 -59 7 12 -58 7 minecraft:air",
    ),
    blocks=_CENTRAL_WALL_BLOCKS,
    opening=_CENTRAL_WALL_OPENING,
)


@dataclass(frozen=True)
class BaselineDefinition:
    baseline_id: str
    initial_state: BaselineInitialState
    targets: tuple[tuple[str, MovementTarget], ...]
    obstacle_profile: ObstacleProfile | None
    preparation_commands: tuple[str, ...]

    @property
    def position_convention(self) -> str:
        return PositionConvention.ENTITY_FEET.value

    @property
    def initial_position(self) -> MovementTarget:
        return self.initial_state.position

    def target(self, variant_id: str) -> MovementTarget:
        try:
            return dict(self.targets)[variant_id]
        except KeyError as exc:
            raise ValueError(f"unknown baseline target: {variant_id}") from exc

    def as_dict(self) -> dict[str, Any]:
        return {
            "baseline_id": self.baseline_id,
            "initial_state": self.initial_state.as_dict(),
            "targets": {name: target.as_dict() for name, target in self.targets},
            "obstacle_profile": (
                self.obstacle_profile.as_dict() if self.obstacle_profile is not None else None
            ),
            "preparation_commands": list(self.preparation_commands),
        }


_INITIAL_STATE = BaselineInitialState(
    position=MovementTarget(14, -59, 5),
    yaw=0.0,
    pitch=0.0,
    dimension="minecraft:overworld",
    game_mode="survival",
    inventory=InventoryState(),
    health=20,
    hunger=20,
    time=6000,
    weather="clear",
    difficulty="normal",
    hostile_mobs_removed=True,
    hostile_mob_spawning=False,
)
_CANONICAL_TARGETS = tuple(
    (variant_id, get_movement_variant(variant_id).target) for variant_id in VARIANT_ORDER
)
_COMMON_PREPARATION_COMMANDS = (
    "/execute in minecraft:overworld run forceload add 4 2 21 19",
    "/fill 4 -61 2 21 -61 19 minecraft:stone",
    "/fill 4 -60 2 21 -56 19 minecraft:air",
    "/fill 9 -60 2 15 -60 8 minecraft:stone",
    "/gamemode survival @p",
    "/clear @p",
    "/effect clear @p",
    "/attribute @p minecraft:generic.max_health base set 20",
    "/effect give @p minecraft:instant_health 1 255 true",
    "/effect give @p minecraft:saturation 1 255 true",
    "/effect clear @p",
    "/gamerule doDaylightCycle false",
    "/gamerule doWeatherCycle false",
    "/gamerule doMobSpawning false",
    "/gamerule spawnRadius 0",
    "/setworldspawn 14 -59 5 0",
    "/time set 6000",
    "/weather clear",
    "/difficulty peaceful",
    "/kill @e[type=minecraft:item]",
    "/difficulty normal",
    "/scoreboard objectives remove va_baseline",
    "/scoreboard objectives add va_baseline dummy",
    f"/scoreboard players set {SOURCE_MARKER} va_baseline 1",
)
baseline_open = BaselineDefinition(
    baseline_id="baseline_open",
    initial_state=_INITIAL_STATE,
    targets=_CANONICAL_TARGETS,
    obstacle_profile=None,
    preparation_commands=(
        *_COMMON_PREPARATION_COMMANDS,
        "/scoreboard players set baseline_open va_baseline 1",
        "/execute in minecraft:overworld run tp @p 14.0 -59.0 5.0 0 0",
        "/execute in minecraft:overworld run forceload remove 4 2 21 19",
    ),
)
baseline_obstructed = BaselineDefinition(
    baseline_id="baseline_obstructed",
    initial_state=_INITIAL_STATE,
    targets=_CANONICAL_TARGETS,
    obstacle_profile=central_wall_v1,
    preparation_commands=(
        *_COMMON_PREPARATION_COMMANDS,
        "/scoreboard players set baseline_obstructed va_baseline 1",
        *central_wall_v1.commands,
        "/execute in minecraft:overworld run tp @p 14.0 -59.0 5.0 0 0",
        "/execute in minecraft:overworld run forceload remove 4 2 21 19",
    ),
)
BASELINE_DEFINITIONS: Mapping[str, BaselineDefinition] = {
    baseline_open.baseline_id: baseline_open,
    baseline_obstructed.baseline_id: baseline_obstructed,
}
OBSTACLE_PROFILES: Mapping[str, ObstacleProfile] = {
    central_wall_v1.profile_id: central_wall_v1,
}


@dataclass(frozen=True)
class RuntimeIdentity:
    name: str
    version: str
    digest: str
    minecraft_version: str


@dataclass(frozen=True)
class SourceIdentity:
    source_id: str
    cloned_world: Path
    cloned_from: str
    clone_evidence: str
    source_revision: str
    world_sha256: str
    process_state: str
    active_locks: tuple[str, ...] = ()


@dataclass(frozen=True)
class QuiescenceEvidence:
    server_stopped: bool
    save_complete: bool
    process_checked: bool
    observed_process_state: str


@dataclass(frozen=True)
class ReachabilityProbeResult:
    variant_id: str
    target: MovementTarget
    reachable: bool
    evidence: str
    position_convention: str = PositionConvention.ENTITY_FEET.value


@dataclass(frozen=True)
class CleanupEvidence:
    completed: bool
    details: str


@dataclass(frozen=True)
class SemanticObservation:
    obstacle_profile_id: str | None
    changed_block_count: int


@dataclass(frozen=True)
class PreparedBaseline:
    runtime: RuntimeIdentity
    source: SourceIdentity
    quiescence: QuiescenceEvidence
    preparation_commands: tuple[str, ...]
    observed_initial_state: BaselineInitialState
    semantic_observation: SemanticObservation
    probes: tuple[ReachabilityProbeResult, ...]
    cleanup: CleanupEvidence


class AcquisitionRuntime(Protocol):
    """Environment authority used only when explicitly registered by an integrator."""

    def prepare(self, definition: BaselineDefinition) -> PreparedBaseline:
        ...

    def release(self, prepared: PreparedBaseline) -> None:
        ...


@dataclass(frozen=True)
class AcquisitionResult:
    baseline_id: str
    output_directory: Path
    archive_path: Path
    archive_sha256: str
    tree_sha256: str
    approved: bool


@dataclass(frozen=True)
class ValidationResult:
    baseline_id: str
    approved: bool
    checks: Mapping[str, bool]
    findings: tuple[str, ...]
    manifest: Mapping[str, Any]


RUNTIME_ADAPTERS: dict[str, AcquisitionRuntime] = {}


def register_acquisition_runtime(name: str, runtime: AcquisitionRuntime) -> None:
    if not name.strip():
        raise ValueError("runtime name must not be empty")
    RUNTIME_ADAPTERS[name] = runtime


def parse_obstacle_profile(payload: str | bytes | Mapping[str, Any]) -> ObstacleProfile:
    raw = json.loads(payload) if isinstance(payload, (str, bytes)) else dict(payload)
    required = {"profile_id", "commands", "blocks", "opening"}
    if set(raw) not in (required, required | {"changed_block_count"}):
        raise ValueError("obstacle profile fields are incomplete or unknown")
    commands = _string_tuple(raw["commands"], "commands")
    blocks = tuple(_parse_block(item) for item in _list(raw["blocks"], "blocks"))
    opening = tuple(_parse_block(item) for item in _list(raw["opening"], "opening"))
    profile = ObstacleProfile(str(raw["profile_id"]), commands, blocks, opening)
    if "changed_block_count" in raw and raw["changed_block_count"] != profile.changed_block_count:
        raise ValueError("obstacle profile changed_block_count is inconsistent")
    registered = OBSTACLE_PROFILES.get(profile.profile_id)
    if registered is None or profile != registered:
        raise ValueError(f"unknown or non-canonical obstacle profile: {profile.profile_id}")
    return profile


def acquire_baseline(
    definition: BaselineDefinition,
    runtime: AcquisitionRuntime,
    output_directory: str | Path,
) -> AcquisitionResult:
    if definition.baseline_id not in BASELINE_DEFINITIONS or (
        BASELINE_DEFINITIONS[definition.baseline_id] != definition
    ):
        raise SnapshotAcquisitionError("baseline definition is not canonical")
    output = Path(output_directory)
    if output.exists() or output.is_symlink():
        raise SnapshotAcquisitionError(f"acquisition output already exists: {output}")

    prepared = runtime.prepare(definition)
    try:
        return _capture_prepared_baseline(definition, prepared, output)
    finally:
        runtime.release(prepared)


def _capture_prepared_baseline(
    definition: BaselineDefinition,
    prepared: PreparedBaseline,
    output: Path,
) -> AcquisitionResult:
    _validate_prepared(definition, prepared)
    source = prepared.source.cloned_world
    included, excluded = _snapshot_file_policy(source)

    output.mkdir(parents=True)
    archive_path = output / f"{definition.baseline_id}.tar.gz"
    _write_deterministic_archive(source, included, archive_path)
    archive_sha256 = _file_sha256(archive_path)

    restore_directory = output / ".restore-check"
    restored = restore_world_snapshot(
        WorldSnapshotDescriptor(definition.baseline_id, archive_path, archive_sha256),
        restore_directory,
    )
    file_manifest = _world_file_manifest(restored.world_directory)
    shutil.rmtree(restore_directory)

    _write_json(output / "restored_world_manifest.json", {
        "schema_version": ACQUISITION_SCHEMA_VERSION,
        "world_directory": WORLD_DIRECTORY,
        "files": file_manifest,
        "file_count": len(file_manifest),
        "tree_sha256": restored.tree_identity.manifest_sha256,
    })
    _write_jsonl(output / "baseline_setup_commands.jsonl", prepared.preparation_commands)
    _write_json(output / "baseline_manifest.json", {
        "schema_version": ACQUISITION_SCHEMA_VERSION,
        "baseline": definition.as_dict(),
        "archive": {
            "file": archive_path.name,
            "sha256": archive_sha256,
            "world_directory": WORLD_DIRECTORY,
            "included_file_count": len(included),
            "excluded": excluded,
            "exclusion_policy": _exclusion_policy(),
        },
        "tree_sha256": restored.tree_identity.manifest_sha256,
        "file_count": restored.tree_identity.file_count,
        "setup_profile_sha256": _setup_profile_sha256(definition),
    })
    provenance = _provenance_dict(
        definition,
        prepared,
        archive_sha256=archive_sha256,
        tree_sha256=restored.tree_identity.manifest_sha256,
        file_count=restored.tree_identity.file_count,
        approved=False,
    )
    _write_json(output / "baseline_provenance.json", provenance)
    provisional = validate_baseline(output, write_report=False, require_recorded_approval=False)
    provenance["approved"] = provisional.approved
    _write_json(output / "baseline_provenance.json", provenance)
    validation = validate_baseline(output, write_report=False)
    _write_json(output / "baseline_validation.json", _validation_dict(validation))
    return AcquisitionResult(
        definition.baseline_id,
        output,
        archive_path,
        archive_sha256,
        restored.tree_identity.manifest_sha256,
        validation.approved,
    )


def validate_baseline(
    output_directory: str | Path,
    *,
    write_report: bool = True,
    require_recorded_approval: bool = True,
) -> ValidationResult:
    output = Path(output_directory)
    findings: list[str] = []
    checks: dict[str, bool] = {}
    try:
        manifest = _read_json(output / "baseline_manifest.json")
        provenance = _read_json(output / "baseline_provenance.json")
        world_manifest = _read_json(output / "restored_world_manifest.json")
        commands = _read_jsonl(output / "baseline_setup_commands.jsonl")
        baseline_raw = manifest["baseline"]
        definition = BASELINE_DEFINITIONS[baseline_raw["baseline_id"]]
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        result = ValidationResult("unknown", False, {"complete_artifacts": False}, (str(exc),), {})
        if write_report and output.is_dir():
            _write_json(output / "baseline_validation.json", _validation_dict(result))
        return result

    checks["complete_provenance"] = _complete_provenance(provenance)
    checks["canonical_definition"] = _canonical_json(baseline_raw) == _canonical_json(
        definition.as_dict()
    )
    checks["preparation_commands"] = commands == list(definition.preparation_commands)
    checks["exact_initial_state"] = _canonical_json(
        provenance.get("observed_initial_state")
    ) == _canonical_json(definition.initial_state.as_dict())
    variant_reachability = _variant_reachability(
        provenance.get("reachability_probes"), definition
    )
    for variant_id, reachable in variant_reachability.items():
        checks[f"probe_{variant_id}_reachable"] = reachable
    checks["all_three_probes_reachable"] = all(variant_reachability.values())
    checks["cleanup_complete"] = provenance.get("cleanup", {}).get("completed") is True
    checks["quiescent_clone"] = _serialized_source_is_safe(provenance)
    checks["semantic_observation"] = _semantic_observation_valid(
        provenance.get("semantic_observation"), definition
    )

    archive_path = output / str(manifest.get("archive", {}).get("file", ""))
    expected_archive_hash = manifest.get("archive", {}).get("sha256")
    checks["archive_hash"] = (
        isinstance(expected_archive_hash, str)
        and _SHA256_RE.fullmatch(expected_archive_hash) is not None
        and archive_path.is_file()
        and _file_sha256(archive_path) == expected_archive_hash
    )
    checks["deterministic_archive"] = (
        checks["archive_hash"] and _archive_is_normalized(archive_path)
    )
    checks["manifest_provenance_consistent"] = _manifest_provenance_consistent(
        manifest, provenance, definition
    )
    checks["canonical_provenance_hashes"] = (
        provenance.get("acquisition_tool_sha256") == _file_sha256(Path(__file__))
        and provenance.get("setup_profile_sha256") == _setup_profile_sha256(definition)
    )
    restore_root = Path(tempfile.mkdtemp(prefix="baseline-validate-"))
    try:
        if checks["archive_hash"]:
            restored = restore_world_snapshot(
                WorldSnapshotDescriptor(definition.baseline_id, archive_path, expected_archive_hash),
                restore_root / WORLD_DIRECTORY,
            )
            actual_files = _world_file_manifest(restored.world_directory)
            checks["safe_restore"] = True
            checks["world_structure"] = all(
                (restored.world_directory / entry).exists() for entry in REQUIRED_WORLD_ENTRIES
            )
            checks["world_manifest"] = (
                world_manifest.get("files") == actual_files
                and world_manifest.get("file_count") == restored.tree_identity.file_count
                and world_manifest.get("tree_sha256") == restored.tree_identity.manifest_sha256
                and manifest.get("tree_sha256") == restored.tree_identity.manifest_sha256
                and manifest.get("file_count") == restored.tree_identity.file_count
            )
        else:
            checks.update(safe_restore=False, world_structure=False, world_manifest=False)
    except Exception as exc:
        checks.update(safe_restore=False, world_structure=False, world_manifest=False)
        findings.append(f"restore: {type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(restore_root, ignore_errors=True)

    path_findings, credential_findings = _scan_artifacts(output)
    checks["zero_path_findings"] = not path_findings
    checks["zero_credential_findings"] = not credential_findings
    findings.extend(path_findings)
    findings.extend(credential_findings)
    findings.extend(name for name, passed in checks.items() if not passed)
    approval_without_record = all(checks.values())
    if require_recorded_approval:
        checks["recorded_approval_consistent"] = provenance.get("approved") is approval_without_record
    result = ValidationResult(
        definition.baseline_id,
        all(checks.values()),
        checks,
        tuple(findings),
        manifest,
    )
    if write_report:
        _write_json(output / "baseline_validation.json", _validation_dict(result))
    return result


def report_baseline_pair(
    open_directory: str | Path,
    obstructed_directory: str | Path,
    output_directory: str | Path,
) -> Mapping[str, Any]:
    first = validate_baseline(open_directory)
    second = validate_baseline(obstructed_directory)
    output = Path(output_directory)
    if output.exists() or output.is_symlink():
        raise SnapshotApprovalError(f"pair report output already exists: {output}")
    if first.baseline_id != "baseline_open" or second.baseline_id != "baseline_obstructed":
        raise SnapshotApprovalError("pair must be baseline_open followed by baseline_obstructed")
    if not first.approved or not second.approved:
        raise SnapshotApprovalError("both baselines must be individually approved")

    first_baseline = first.manifest["baseline"]
    second_baseline = second.manifest["baseline"]
    first_observation = _read_json(Path(open_directory) / "baseline_provenance.json")[
        "semantic_observation"
    ]
    second_observation = _read_json(Path(obstructed_directory) / "baseline_provenance.json")[
        "semantic_observation"
    ]
    checks = {
        "archive_hashes_distinct": first.manifest["archive"]["sha256"] != second.manifest["archive"]["sha256"],
        "tree_hashes_distinct": first.manifest["tree_sha256"] != second.manifest["tree_sha256"],
        "same_initial_state": first_baseline["initial_state"] == second_baseline["initial_state"],
        "same_targets": first_baseline["targets"] == second_baseline["targets"],
        "semantic_difference_central_wall_v1": (
            first_observation == {"obstacle_profile_id": None, "changed_block_count": 0}
            and second_observation == {
                "obstacle_profile_id": "central_wall_v1",
                "changed_block_count": central_wall_v1.changed_block_count,
            }
            and second_observation["changed_block_count"] > 0
        ),
    }
    if not all(checks.values()):
        raise SnapshotApprovalError(
            "baseline pair is not distinct and semantically approved: "
            + ", ".join(name for name, passed in checks.items() if not passed)
        )
    report = {
        "schema_version": ACQUISITION_SCHEMA_VERSION,
        "approved": True,
        "baselines": [first.baseline_id, second.baseline_id],
        "checks": checks,
        "archive_sha256": [first.manifest["archive"]["sha256"], second.manifest["archive"]["sha256"]],
        "tree_sha256": [first.manifest["tree_sha256"], second.manifest["tree_sha256"]],
        "changed_block_count": second_observation["changed_block_count"],
    }
    output.mkdir(parents=True)
    _write_json(output / "baseline_diff_report.json", report)
    _atomic_write_text(
        output / "baseline_approval_report.md",
        "# Minecraft baseline approval\n\n"
        "Status: APPROVED\n\n"
        "The archives and restored trees are distinct, while initial state and canonical targets match. "
        f"`central_wall_v1` changes {report['changed_block_count']} blocks.\n",
    )
    return report


def _validate_prepared(definition: BaselineDefinition, prepared: PreparedBaseline) -> None:
    source = prepared.source
    path = source.cloned_world
    if path.is_symlink() or not path.is_dir():
        raise UnsafeSnapshotSourceError("snapshot source must be a real cloned world directory")
    if not source.cloned_from or not source.clone_evidence or source.cloned_from == source.source_id:
        raise UnsafeSnapshotSourceError("explicit independent clone evidence is required")
    if source.process_state != "stopped" or source.active_locks:
        raise UnsafeSnapshotSourceError("source process is live or unknown, or reports active locks")
    q = prepared.quiescence
    if not (
        q.server_stopped is True
        and q.save_complete is True
        and q.process_checked is True
        and q.observed_process_state == "stopped"
    ):
        raise UnsafeSnapshotSourceError("complete stopped/quiesced evidence is required")
    if prepared.preparation_commands != definition.preparation_commands:
        raise SnapshotApprovalError("observed preparation commands differ from the baseline definition")
    if _canonical_json(prepared.observed_initial_state.as_dict()) != _canonical_json(
        definition.initial_state.as_dict()
    ):
        raise SnapshotApprovalError(
            "observed initial state differs from the baseline definition: expected "
            f"{_canonical_json(definition.initial_state.as_dict())}, observed "
            f"{_canonical_json(prepared.observed_initial_state.as_dict())}"
        )
    if not _semantic_observation_object_valid(prepared.semantic_observation, definition):
        raise SnapshotApprovalError("observed obstacle semantics differ from the baseline definition")
    if not _probe_objects_valid(prepared.probes, definition):
        raise SnapshotApprovalError("all three canonical reachability probes must pass")
    if prepared.cleanup.completed is not True or not prepared.cleanup.details:
        raise SnapshotApprovalError("runtime cleanup evidence is incomplete")
    if not all((prepared.runtime.name, prepared.runtime.version, source.source_id)):
        raise SnapshotApprovalError("runtime/source identity is incomplete")
    if prepared.runtime.minecraft_version != "1.19.2":
        raise SnapshotApprovalError("Minecraft version must be 1.19.2")
    if not _SHA256_RE.fullmatch(prepared.runtime.digest):
        raise SnapshotApprovalError("runtime digest must be a lowercase SHA-256 digest")
    if not _SHA256_RE.fullmatch(source.world_sha256):
        raise SnapshotApprovalError("source world identity must be a lowercase SHA-256 digest")
    if canonical_world_tree_identity(path).manifest_sha256 != source.world_sha256:
        raise SnapshotApprovalError("source world identity does not match the cloned world tree")
    if not _GIT_SHA_RE.fullmatch(source.source_revision):
        raise SnapshotApprovalError("source revision must be a full lowercase Git SHA")


def _snapshot_file_policy(world: Path) -> tuple[list[Path], list[str]]:
    if not (world / "level.dat").is_file() or not (world / "region").is_dir():
        raise UnsafeSnapshotSourceError("source is not a Minecraft world")
    included: list[Path] = []
    excluded: list[str] = []
    for path in sorted(world.rglob("*"), key=lambda value: value.relative_to(world).as_posix()):
        relative = path.relative_to(world).as_posix()
        if path.is_symlink():
            raise UnsafeSnapshotSourceError(f"source contains symbolic link: {relative}")
        if not (path.is_file() or path.is_dir()):
            raise UnsafeSnapshotSourceError(f"source contains non-regular entry: {relative}")
        if _is_excluded(relative, path.name):
            excluded.append(relative)
            continue
        included.append(path)
    return included, excluded


def _is_excluded(relative: str, name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in RUNTIME_LOCK_NAMES
        or lowered in TEMPORARY_NAMES
        or lowered.endswith(TEMPORARY_SUFFIXES)
        or any(part in {"logs", "crash-reports"} for part in PurePosixPath(relative).parts)
    )


def _exclusion_policy() -> dict[str, Any]:
    return {
        "runtime_lock_names": sorted(RUNTIME_LOCK_NAMES),
        "temporary_names": sorted(TEMPORARY_NAMES),
        "temporary_suffixes": list(TEMPORARY_SUFFIXES),
        "runtime_directories": ["logs", "crash-reports"],
        "level_datapacks_and_config": "included",
    }


def _write_deterministic_archive(world: Path, entries: Sequence[Path], destination: Path) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        _add_tar_directory(archive, WORLD_DIRECTORY)
        for path in entries:
            relative = path.relative_to(world).as_posix()
            archive_name = f"{WORLD_DIRECTORY}/{relative}"
            if path.is_dir():
                _add_tar_directory(archive, archive_name)
            else:
                info = tarfile.TarInfo(archive_name)
                stat = path.stat()
                info.size = stat.st_size
                _normalize_tar_info(info, is_directory=False, executable=bool(stat.st_mode & 0o111))
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        with temporary.open("xb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                compressed.write(buffer.getvalue())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _add_tar_directory(archive: tarfile.TarFile, name: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    _normalize_tar_info(info, is_directory=True, executable=False)
    archive.addfile(info)


def _normalize_tar_info(info: tarfile.TarInfo, *, is_directory: bool, executable: bool) -> None:
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = 0o755 if is_directory or executable else 0o644
    info.pax_headers = {}


def _world_file_manifest(world: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(world).as_posix(),
            "size": path.stat().st_size,
            "sha256": _file_sha256(path),
        }
        for path in sorted(world.rglob("*"), key=lambda value: value.relative_to(world).as_posix())
        if path.is_file()
    ]


def _provenance_dict(
    definition: BaselineDefinition,
    prepared: PreparedBaseline,
    *,
    archive_sha256: str,
    tree_sha256: str,
    file_count: int,
    approved: bool,
) -> dict[str, Any]:
    return {
        "schema_version": ACQUISITION_SCHEMA_VERSION,
        "baseline_id": definition.baseline_id,
        "source_revision": prepared.source.source_revision,
        "source_world_identity": prepared.source.world_sha256,
        "minecraft_version": prepared.runtime.minecraft_version,
        "runtime_digest": prepared.runtime.digest,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "acquisition_tool_sha256": _file_sha256(Path(__file__)),
        "setup_profile_sha256": _setup_profile_sha256(definition),
        "archive_sha256": archive_sha256,
        "restored_tree_sha256": tree_sha256,
        "restored_file_count": file_count,
        "runtime": vars(prepared.runtime),
        "source": {
            "source_id": prepared.source.source_id,
            "cloned_from": prepared.source.cloned_from,
            "clone_evidence": prepared.source.clone_evidence,
            "process_state": prepared.source.process_state,
            "active_locks": list(prepared.source.active_locks),
        },
        "quiescence": vars(prepared.quiescence),
        "observed_initial_state": prepared.observed_initial_state.as_dict(),
        "semantic_observation": vars(prepared.semantic_observation),
        "reachability_probes": [
            {
                "variant_id": probe.variant_id,
                "target": probe.target.as_dict(),
                "position_convention": probe.position_convention,
                "reachable": probe.reachable,
                "evidence": probe.evidence,
            }
            for probe in prepared.probes
        ],
        "cleanup": vars(prepared.cleanup),
        "approved": approved,
    }


def _complete_provenance(provenance: Mapping[str, Any]) -> bool:
    try:
        runtime = provenance["runtime"]
        source = provenance["source"]
        quiescence = provenance["quiescence"]
        cleanup = provenance["cleanup"]
        created_at = datetime.fromisoformat(str(provenance["created_at"]).replace("Z", "+00:00"))
        digest_fields = (
            "source_world_identity",
            "runtime_digest",
            "acquisition_tool_sha256",
            "setup_profile_sha256",
            "archive_sha256",
            "restored_tree_sha256",
        )
        return (
            isinstance(provenance["baseline_id"], str)
            and _GIT_SHA_RE.fullmatch(provenance["source_revision"]) is not None
            and provenance["minecraft_version"] == "1.19.2"
            and all(_SHA256_RE.fullmatch(provenance[field]) is not None for field in digest_fields)
            and isinstance(provenance["restored_file_count"], int)
            and provenance["restored_file_count"] >= 2
            and created_at.tzinfo is not None
            and str(provenance["created_at"]).endswith("Z")
            and isinstance(provenance["approved"], bool)
            and all(str(runtime[key]).strip() for key in ("name", "version", "digest", "minecraft_version"))
            and all(
            str(source[key]).strip() for key in ("source_id", "cloned_from", "clone_evidence", "process_state")
            )
            and all(key in quiescence for key in ("server_stopped", "save_complete", "process_checked", "observed_process_state"))
            and bool(cleanup.get("details"))
        )
    except (KeyError, TypeError, ValueError):
        return False


def _manifest_provenance_consistent(
    manifest: Mapping[str, Any],
    provenance: Mapping[str, Any],
    definition: BaselineDefinition,
) -> bool:
    try:
        return (
            provenance["baseline_id"] == definition.baseline_id == manifest["baseline"]["baseline_id"]
            and provenance["archive_sha256"] == manifest["archive"]["sha256"]
            and provenance["restored_tree_sha256"] == manifest["tree_sha256"]
            and provenance["restored_file_count"] == manifest["file_count"]
            and provenance["setup_profile_sha256"] == manifest["setup_profile_sha256"]
            and provenance["runtime_digest"] == provenance["runtime"]["digest"]
            and provenance["minecraft_version"] == provenance["runtime"]["minecraft_version"]
        )
    except (KeyError, TypeError):
        return False


def _serialized_source_is_safe(provenance: Mapping[str, Any]) -> bool:
    try:
        source = provenance["source"]
        q = provenance["quiescence"]
        return (
            source["source_id"] != source["cloned_from"]
            and source["process_state"] == "stopped"
            and source["active_locks"] == []
            and q["server_stopped"] is True
            and q["save_complete"] is True
            and q["process_checked"] is True
            and q["observed_process_state"] == "stopped"
        )
    except (KeyError, TypeError):
        return False


def _probe_objects_valid(probes: Sequence[ReachabilityProbeResult], definition: BaselineDefinition) -> bool:
    return len(probes) == 3 and all(
        probe.variant_id == variant_id
        and probe.target == target
        and probe.reachable is True
        and probe.position_convention == definition.position_convention
        and isinstance(probe.evidence, str)
        and bool(probe.evidence)
        for probe, (variant_id, target) in zip(probes, definition.targets)
    )


def _semantic_observation_object_valid(
    observation: SemanticObservation, definition: BaselineDefinition
) -> bool:
    expected_id = definition.obstacle_profile.profile_id if definition.obstacle_profile else None
    expected_count = definition.obstacle_profile.changed_block_count if definition.obstacle_profile else 0
    return (
        (observation.obstacle_profile_id is None or isinstance(observation.obstacle_profile_id, str))
        and isinstance(observation.changed_block_count, int)
        and not isinstance(observation.changed_block_count, bool)
        and observation.obstacle_profile_id == expected_id
        and observation.changed_block_count == expected_count
    )


def _semantic_observation_valid(raw: Any, definition: BaselineDefinition) -> bool:
    if not isinstance(raw, dict) or set(raw) != {"obstacle_profile_id", "changed_block_count"}:
        return False
    return _semantic_observation_object_valid(
        SemanticObservation(raw["obstacle_profile_id"], raw["changed_block_count"]),
        definition,
    )


def _variant_reachability(
    raw: Any, definition: BaselineDefinition
) -> dict[str, bool]:
    results = {variant_id: False for variant_id in VARIANT_ORDER}
    if not isinstance(raw, list) or len(raw) != len(definition.targets):
        return results
    for probe, (variant_id, target) in zip(raw, definition.targets):
        results[variant_id] = isinstance(probe, dict) and (
            probe.get("variant_id") == variant_id
            and probe.get("target") == target.as_dict()
            and probe.get("reachable") is True
            and bool(probe.get("evidence"))
        )
    return results


def _archive_is_normalized(path: Path) -> bool:
    try:
        raw = path.read_bytes()
        if len(raw) < 10 or raw[:2] != b"\x1f\x8b" or raw[3] & 0x08 or raw[4:8] != b"\0\0\0\0":
            return False
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
        names = [member.name for member in members]
        return (
            bool(names)
            and names == sorted(names)
            and names[0] == WORLD_DIRECTORY
            and all(name == WORLD_DIRECTORY or name.startswith(f"{WORLD_DIRECTORY}/") for name in names)
            and all(
                member.mtime == 0
                and member.uid == 0
                and member.gid == 0
                and member.uname == ""
                and member.gname == ""
                and member.mode == (0o755 if member.isdir() or member.mode & 0o111 else 0o644)
                for member in members
            )
        )
    except (OSError, tarfile.TarError):
        return False


def _scan_artifacts(output: Path) -> tuple[list[str], list[str]]:
    path_findings: list[str] = []
    credential_findings: list[str] = []
    for path in sorted(output.glob("*.json*")):
        if path.name == "baseline_validation.json":
            continue
        text = path.read_text(encoding="utf-8")
        if _ABSOLUTE_PATH_RE.search(text):
            path_findings.append(f"path finding in {path.name}")
        if _CREDENTIAL_RE.search(text):
            credential_findings.append(f"credential finding in {path.name}")
    return path_findings, credential_findings


def _validation_dict(result: ValidationResult) -> dict[str, Any]:
    checks = dict(result.checks)
    reachability = {
        variant_id: checks.get(f"probe_{variant_id}_reachable", False)
        for variant_id in VARIANT_ORDER
    }
    return {
        "schema_version": ACQUISITION_SCHEMA_VERSION,
        "baseline_id": result.baseline_id,
        "archive_valid": checks.get("archive_hash", False) and checks.get("deterministic_archive", False),
        "safe_extraction_valid": checks.get("safe_restore", False),
        "world_structure_valid": checks.get("world_structure", False) and checks.get("world_manifest", False),
        "initial_state_valid": checks.get("exact_initial_state", False),
        "variant_reachability": reachability,
        "cleanup_valid": checks.get("cleanup_complete", False),
        "absolute_path_findings": not checks.get("zero_path_findings", False),
        "credential_findings": not checks.get("zero_credential_findings", False),
        "approved": result.approved,
        "checks": checks,
        "findings": list(result.findings),
    }


def _setup_profile_sha256(definition: BaselineDefinition) -> str:
    encoded = json.dumps(
        {
            "initial_state": definition.initial_state.as_dict(),
            "preparation_commands": list(definition.preparation_commands),
            "obstacle_profile": (
                definition.obstacle_profile.as_dict()
                if definition.obstacle_profile is not None
                else None
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _parse_block(raw: Any) -> BlockPlacement:
    if not isinstance(raw, dict) or set(raw) != {"x", "y", "z", "block"}:
        raise ValueError("block placement must contain x, y, z, and block")
    if any(isinstance(raw[key], bool) or not isinstance(raw[key], int) for key in ("x", "y", "z")):
        raise ValueError("block coordinates must be integers")
    if not isinstance(raw["block"], str) or not raw["block"]:
        raise ValueError("block name must be non-empty")
    return BlockPlacement(raw["x"], raw["y"], raw["z"], raw["block"])


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return value


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    items = _list(value, field)
    if not all(isinstance(item, str) and item for item in items):
        raise ValueError(f"{field} entries must be non-empty strings")
    return tuple(items)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_write_text(
        path,
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True) + "\n",
    )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _write_jsonl(path: Path, commands: Sequence[str]) -> None:
    _atomic_write_text(
        path,
        "".join(json.dumps({"order": index, "command": command}, sort_keys=True) + "\n" for index, command in enumerate(commands, 1)),
    )


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_jsonl(path: Path) -> list[str]:
    return [json.loads(line)["command"] for line in path.read_text(encoding="utf-8").splitlines()]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Acquire and approve Minecraft baseline snapshots")
    commands = parser.add_subparsers(dest="command", required=True)
    acquire = commands.add_parser("acquire")
    acquire.add_argument("baseline_id", choices=tuple(BASELINE_DEFINITIONS))
    acquire.add_argument("output")
    acquire.add_argument("--runtime", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("output")
    report = commands.add_parser("report")
    report.add_argument("open_output")
    report.add_argument("obstructed_output")
    report.add_argument("report_output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "acquire":
            if args.runtime == "minecraft-1.19.2-local":
                from benchmarks.minecraft.docker_runtime import register_builtin_runtimes

                register_builtin_runtimes(acquisition=True)
            runtime = RUNTIME_ADAPTERS.get(args.runtime)
            if runtime is None:
                raise AcquisitionRuntimeUnavailableError(
                    f"no registered acquisition runtime is available for {args.runtime!r}"
                )
            result = acquire_baseline(BASELINE_DEFINITIONS[args.baseline_id], runtime, args.output)
            return 0 if result.approved else 1
        if args.command == "validate":
            return 0 if validate_baseline(args.output).approved else 1
        report_baseline_pair(args.open_output, args.obstructed_output, args.report_output)
        return 0
    except SnapshotAcquisitionError as exc:
        print(f"snapshot acquisition failed: {exc}")
        return 2


if __name__ == "__main__":
    from benchmarks.minecraft.snapshot_acquisition import main as package_main

    raise SystemExit(package_main())
