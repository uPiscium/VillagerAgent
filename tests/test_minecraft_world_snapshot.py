import hashlib
import io
import tarfile
from dataclasses import FrozenInstanceError

import pytest

from benchmarks.minecraft.world_snapshot import (
    InvalidWorldSnapshotStructureError,
    UnsafeWorldSnapshotError,
    WorldSnapshotDescriptor,
    WorldSnapshotDestinationError,
    WorldSnapshotIntegrityError,
    canonical_world_tree_identity,
    restore_world_snapshot,
)


def _archive(tmp_path, name="baseline.tar.gz", *, marker=b"baseline"):
    archive_path = tmp_path / name
    with tarfile.open(archive_path, "w:gz") as archive:
        _add_directory(archive, "world")
        _add_file(archive, "world/level.dat", b"level")
        _add_directory(archive, "world/region")
        _add_file(archive, "world/region/r.0.0.mca", marker)
    return archive_path


def _descriptor(archive_path, snapshot_id="baseline"):
    return WorldSnapshotDescriptor(
        snapshot_id=snapshot_id,
        archive_path=archive_path,
        archive_sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
    )


def _add_directory(archive, name):
    member = tarfile.TarInfo(name)
    member.type = tarfile.DIRTYPE
    archive.addfile(member)


def _add_file(archive, name, content):
    member = tarfile.TarInfo(name)
    member.size = len(content)
    archive.addfile(member, io.BytesIO(content))


def test_snapshot_descriptor_is_immutable_and_validated(tmp_path):
    descriptor = _descriptor(_archive(tmp_path))

    with pytest.raises(FrozenInstanceError):
        descriptor.snapshot_id = "changed"
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        WorldSnapshotDescriptor("baseline", tmp_path / "x", "A" * 64)
    with pytest.raises(ValueError, match="one relative path component"):
        WorldSnapshotDescriptor("baseline", tmp_path / "x", "a" * 64, "../world")


def test_archive_hash_mismatch_is_rejected_without_destination(tmp_path):
    descriptor = WorldSnapshotDescriptor("baseline", _archive(tmp_path), "0" * 64)
    destination = tmp_path / "run-world"

    with pytest.raises(WorldSnapshotIntegrityError, match="does not match"):
        restore_world_snapshot(descriptor, destination)

    assert not destination.exists()


@pytest.mark.parametrize(
    "member_name",
    ["../escape", "/absolute", "C:/windows", "C:\\windows", "\\\\server\\share"],
)
def test_unsafe_archive_paths_are_rejected(tmp_path, member_name):
    archive_path = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        _add_file(archive, member_name, b"escape")

    with pytest.raises(UnsafeWorldSnapshotError, match="unsafe archive member path"):
        restore_world_snapshot(_descriptor(archive_path), tmp_path / "run-world")

    assert not (tmp_path.parent / "escape").exists()


@pytest.mark.parametrize("link_type", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_archive_links_are_rejected(tmp_path, link_type):
    archive_path = tmp_path / "linked.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        _add_directory(archive, "world")
        _add_file(archive, "world/level.dat", b"level")
        _add_directory(archive, "world/region")
        link = tarfile.TarInfo("world/region/link")
        link.type = link_type
        link.linkname = "world/level.dat"
        archive.addfile(link)

    with pytest.raises(UnsafeWorldSnapshotError, match="links are not permitted"):
        restore_world_snapshot(_descriptor(archive_path), tmp_path / "run-world")


@pytest.mark.parametrize("nonempty", [False, True])
def test_restore_requires_a_fresh_destination(tmp_path, nonempty):
    descriptor = _descriptor(_archive(tmp_path))
    destination = tmp_path / "run-world"
    destination.mkdir()
    if nonempty:
        (destination / "prior-state").write_bytes(b"must not be reused")

    with pytest.raises(WorldSnapshotDestinationError, match="already exists"):
        restore_world_snapshot(descriptor, destination)


def test_archive_must_have_expected_top_level_world_structure(tmp_path):
    archive_path = tmp_path / "invalid-structure.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        _add_directory(archive, "not-world")
        _add_file(archive, "not-world/level.dat", b"level")
        _add_directory(archive, "not-world/region")

    with pytest.raises(InvalidWorldSnapshotStructureError, match="top-level 'world'"):
        restore_world_snapshot(_descriptor(archive_path), tmp_path / "run-world")


def test_each_restore_is_isolated_from_prior_run_mutation(tmp_path):
    descriptor = _descriptor(_archive(tmp_path))
    first = restore_world_snapshot(descriptor, tmp_path / "run-one")
    (first.world_directory / "region" / "r.0.0.mca").write_bytes(b"mutated")
    (first.world_directory / "run-created.dat").write_bytes(b"run state")

    second = restore_world_snapshot(descriptor, tmp_path / "run-two")

    assert (second.world_directory / "region" / "r.0.0.mca").read_bytes() == b"baseline"
    assert not (second.world_directory / "run-created.dat").exists()
    assert second.tree_identity.file_count == 2


def test_same_baseline_has_stable_canonical_tree_identity(tmp_path):
    descriptor = _descriptor(_archive(tmp_path))

    first = restore_world_snapshot(descriptor, tmp_path / "run-one")
    second = restore_world_snapshot(descriptor, tmp_path / "run-two")

    assert first.tree_identity == second.tree_identity
    assert canonical_world_tree_identity(first.world_directory) == first.tree_identity


def test_distinct_baselines_have_distinct_tree_identities(tmp_path):
    first_archive = _archive(tmp_path, "first.tar.gz", marker=b"first")
    second_archive = _archive(tmp_path, "second.tar.gz", marker=b"second")

    first = restore_world_snapshot(_descriptor(first_archive, "first"), tmp_path / "run-one")
    second = restore_world_snapshot(_descriptor(second_archive, "second"), tmp_path / "run-two")

    assert first.descriptor.archive_sha256 != second.descriptor.archive_sha256
    assert first.tree_identity.manifest_sha256 != second.tree_identity.manifest_sha256
