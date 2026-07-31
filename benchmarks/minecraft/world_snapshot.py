from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class WorldSnapshotError(RuntimeError):
    pass


class WorldSnapshotIntegrityError(WorldSnapshotError):
    pass


class UnsafeWorldSnapshotError(WorldSnapshotError):
    pass


class InvalidWorldSnapshotStructureError(WorldSnapshotError):
    pass


class WorldSnapshotDestinationError(WorldSnapshotError):
    pass


@dataclass(frozen=True)
class WorldSnapshotDescriptor:
    """Immutable identity and layout expected from a baseline archive."""

    snapshot_id: str
    archive_path: Path
    archive_sha256: str
    world_directory: str = "world"

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip():
            raise ValueError("snapshot_id must be a non-empty string")
        if self.snapshot_id != self.snapshot_id.strip():
            raise ValueError("snapshot_id must not have surrounding whitespace")
        if not isinstance(self.archive_path, (str, Path)):
            raise TypeError("archive_path must be a path")
        archive_path = Path(self.archive_path)
        if not str(archive_path):
            raise ValueError("archive_path must not be empty")
        object.__setattr__(self, "archive_path", archive_path)
        if not isinstance(self.archive_sha256, str) or not _SHA256_RE.fullmatch(
            self.archive_sha256
        ):
            raise ValueError("archive_sha256 must be a lowercase SHA-256 digest")
        _validate_single_path_component(self.world_directory, field="world_directory")


@dataclass(frozen=True)
class WorldTreeIdentity:
    manifest_sha256: str
    file_count: int


@dataclass(frozen=True)
class RestoredWorld:
    descriptor: WorldSnapshotDescriptor
    world_directory: Path
    tree_identity: WorldTreeIdentity


def restore_world_snapshot(
    descriptor: WorldSnapshotDescriptor,
    destination: str | Path,
) -> RestoredWorld:
    """Restore a verified baseline into a new destination without state reuse."""
    if not isinstance(descriptor, WorldSnapshotDescriptor):
        raise TypeError("descriptor must be a WorldSnapshotDescriptor")

    destination = Path(destination)
    parent = destination.parent
    if destination.exists() or destination.is_symlink():
        raise WorldSnapshotDestinationError(
            f"world snapshot destination already exists: {destination}"
        )
    parent.mkdir(parents=True, exist_ok=True)
    if not parent.is_dir():
        raise WorldSnapshotDestinationError(
            f"world snapshot destination parent is not a directory: {parent}"
        )

    actual_archive_sha256 = _file_sha256(descriptor.archive_path)
    if actual_archive_sha256 != descriptor.archive_sha256:
        raise WorldSnapshotIntegrityError(
            "world snapshot archive SHA-256 does not match its descriptor"
        )

    staging_root = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=parent))
    try:
        with tarfile.open(descriptor.archive_path, mode="r:gz") as archive:
            members = archive.getmembers()
            _validate_archive_members(members)
            _extract_members(archive, members, staging_root)

        staged_world = _validate_world_structure(staging_root, descriptor.world_directory)
        tree_identity = canonical_world_tree_identity(staged_world)
        if destination.exists() or destination.is_symlink():
            raise WorldSnapshotDestinationError(
                f"world snapshot destination already exists: {destination}"
            )
        os.replace(staged_world, destination)
    except (WorldSnapshotError, OSError, tarfile.TarError):
        raise
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

    return RestoredWorld(
        descriptor=descriptor,
        world_directory=destination,
        tree_identity=tree_identity,
    )


def canonical_world_tree_identity(world_directory: str | Path) -> WorldTreeIdentity:
    """Hash every restored file; no world metadata is excluded as volatile."""
    world_directory = Path(world_directory)
    if not world_directory.is_dir() or world_directory.is_symlink():
        raise InvalidWorldSnapshotStructureError("world directory must be a real directory")

    manifest = []
    file_count = 0
    for path in sorted(world_directory.rglob("*"), key=lambda item: item.as_posix()):
        relative_path = path.relative_to(world_directory).as_posix()
        if path.is_symlink():
            raise InvalidWorldSnapshotStructureError(
                f"restored world contains a symbolic link: {relative_path}"
            )
        if path.is_dir():
            manifest.append({"path": relative_path, "type": "directory"})
            continue
        if not path.is_file():
            raise InvalidWorldSnapshotStructureError(
                f"restored world contains a non-regular file: {relative_path}"
            )
        manifest.append({
            "path": relative_path,
            "sha256": _file_sha256(path),
            "size": path.stat().st_size,
            "type": "file",
        })
        file_count += 1

    encoded_manifest = b"".join(
        json.dumps(entry, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for entry in manifest
    )
    return WorldTreeIdentity(
        manifest_sha256=hashlib.sha256(encoded_manifest).hexdigest(),
        file_count=file_count,
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise WorldSnapshotIntegrityError(f"cannot read world snapshot file: {path}") from exc
    return digest.hexdigest()


def _validate_single_path_component(value: str, *, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"{field} must be one relative path component")
    if _WINDOWS_DRIVE_RE.match(value):
        raise ValueError(f"{field} must not be a Windows drive path")


def _validate_archive_members(members: list[tarfile.TarInfo]) -> None:
    seen_paths = set()
    for member in members:
        name = member.name
        if not name or "\\" in name or name.startswith(("/", "//")):
            raise UnsafeWorldSnapshotError(f"unsafe archive member path: {name!r}")
        if _WINDOWS_DRIVE_RE.match(name):
            raise UnsafeWorldSnapshotError(f"unsafe archive member path: {name!r}")
        path = PurePosixPath(name)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise UnsafeWorldSnapshotError(f"unsafe archive member path: {name!r}")
        normalized = path.as_posix()
        if normalized in seen_paths:
            raise UnsafeWorldSnapshotError(f"duplicate archive member path: {name!r}")
        seen_paths.add(normalized)
        if member.issym() or member.islnk():
            raise UnsafeWorldSnapshotError(
                f"archive links are not permitted: {name!r}"
            )
        if not (member.isdir() or member.isfile()):
            raise UnsafeWorldSnapshotError(
                f"archive member is not a regular file or directory: {name!r}"
            )


def _extract_members(
    archive: tarfile.TarFile,
    members: list[tarfile.TarInfo],
    staging_root: Path,
) -> None:
    for member in members:
        output_path = staging_root.joinpath(*PurePosixPath(member.name).parts)
        if member.isdir():
            output_path.mkdir(parents=True, exist_ok=True)
            continue
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            raise UnsafeWorldSnapshotError(
                f"archive file conflicts with an existing path: {member.name!r}"
            )
        source = archive.extractfile(member)
        if source is None:
            raise UnsafeWorldSnapshotError(
                f"archive regular file has no content: {member.name!r}"
            )
        with source, output_path.open("xb") as destination:
            shutil.copyfileobj(source, destination)


def _validate_world_structure(staging_root: Path, world_directory: str) -> Path:
    top_level_entries = list(staging_root.iterdir())
    if len(top_level_entries) != 1 or top_level_entries[0].name != world_directory:
        raise InvalidWorldSnapshotStructureError(
            f"archive must contain exactly one top-level {world_directory!r} directory"
        )
    world_root = top_level_entries[0]
    if not world_root.is_dir() or world_root.is_symlink():
        raise InvalidWorldSnapshotStructureError("top-level world entry must be a directory")
    if not (world_root / "level.dat").is_file():
        raise InvalidWorldSnapshotStructureError("world must contain a level.dat file")
    if not (world_root / "region").is_dir():
        raise InvalidWorldSnapshotStructureError("world must contain a region directory")
    return world_root
