import json
import os
import tarfile
from dataclasses import replace

import pytest

from benchmarks.minecraft.matrix_variants import VARIANT_ORDER, get_movement_variant
from benchmarks.minecraft.snapshot_acquisition import (
    CleanupEvidence,
    PreparedBaseline,
    QuiescenceEvidence,
    ReachabilityProbeResult,
    RuntimeIdentity,
    SemanticObservation,
    SnapshotApprovalError,
    SourceIdentity,
    UnsafeSnapshotSourceError,
    acquire_baseline,
    baseline_obstructed,
    baseline_open,
    central_wall_v1,
    main,
    parse_obstacle_profile,
    report_baseline_pair,
    validate_baseline,
)
from benchmarks.minecraft.world_snapshot import canonical_world_tree_identity


class FakeRuntime:
    def __init__(
        self,
        world,
        *,
        process_state="stopped",
        cloned_from="shared-world",
        active_locks=(),
    ):
        self.world = world
        self.process_state = process_state
        self.cloned_from = cloned_from
        self.active_locks = active_locks
        self.calls = []

    def prepare(self, definition):
        self.calls.append(("prepare", definition.baseline_id))
        probes = tuple(
            ReachabilityProbeResult(
                variant_id,
                get_movement_variant(variant_id).target,
                True,
                f"fake probe {variant_id}",
            )
            for variant_id in VARIANT_ORDER
        )
        return PreparedBaseline(
            runtime=RuntimeIdentity("fake", "1", "1" * 64, "1.19.2"),
            source=SourceIdentity(
                source_id="clone-world",
                cloned_world=self.world,
                cloned_from=self.cloned_from,
                clone_evidence="fake copy completed while stopped",
                source_revision="a" * 40,
                world_sha256=(
                    "2" * 64
                    if self.world.is_symlink()
                    else canonical_world_tree_identity(self.world).manifest_sha256
                ),
                process_state=self.process_state,
                active_locks=self.active_locks,
            ),
            quiescence=QuiescenceEvidence(True, True, True, self.process_state),
            preparation_commands=definition.preparation_commands,
            observed_initial_state=definition.initial_state,
            semantic_observation=SemanticObservation(
                definition.obstacle_profile.profile_id if definition.obstacle_profile else None,
                definition.obstacle_profile.changed_block_count if definition.obstacle_profile else 0,
            ),
            probes=probes,
            cleanup=CleanupEvidence(True, "fake runtime stopped and temporary player removed"),
        )

    def release(self, _prepared):
        pass


def _world(tmp_path, name, marker=b"open"):
    world = tmp_path / name
    (world / "region").mkdir(parents=True)
    (world / "datapacks" / "pack" / "data").mkdir(parents=True)
    (world / "serverconfig").mkdir()
    (world / "level.dat").write_bytes(b"level")
    (world / "region" / "r.0.0.mca").write_bytes(marker)
    (world / "datapacks" / "pack" / "pack.mcmeta").write_text("{}")
    (world / "serverconfig" / "level.toml").write_text("setting=true")
    (world / "ignored.tmp").write_bytes(os.urandom(4))
    return world


def _acquire(tmp_path, definition, name, marker):
    world = _world(tmp_path, f"{name}-clone", marker)
    return acquire_baseline(definition, FakeRuntime(world), tmp_path / name)


def test_baseline_definitions_use_canonical_targets_and_profile_parses():
    assert baseline_open.initial_position.as_dict() == {"x": 14, "y": -59, "z": 5}
    assert dict(baseline_open.targets) == {
        name: get_movement_variant(name).target for name in VARIANT_ORDER
    }
    assert baseline_obstructed.obstacle_profile == central_wall_v1
    assert central_wall_v1.changed_block_count > 0
    assert parse_obstacle_profile(json.dumps(central_wall_v1.as_dict())) == central_wall_v1
    state = baseline_open.initial_state.as_dict()
    assert state == {
        "position": {"x": 14, "y": -59, "z": 5},
        "yaw": 0.0,
        "pitch": 0.0,
        "dimension": "minecraft:overworld",
        "game_mode": "survival",
        "inventory": {"representation": "occupied_slots_v1", "slots": []},
        "health": 20,
        "hunger": 20,
        "time": 6000,
        "weather": "clear",
        "difficulty": "normal",
        "hostile_mobs_removed": True,
        "hostile_mob_spawning": False,
    }
    commands = baseline_open.preparation_commands
    assert any("tp @p 14.0 -59.0 5.0 0 0" in command for command in commands)
    assert any("gamemode survival" in command for command in commands)
    assert any("doMobSpawning false" in command for command in commands)
    assert any("type=minecraft:item" in command for command in commands)
    assert baseline_obstructed.preparation_commands[-4:-2] == central_wall_v1.commands


def test_deterministic_archive_and_level_specific_content(tmp_path):
    first = _acquire(tmp_path, baseline_open, "first", b"same")
    second_world = _world(tmp_path, "second-clone", b"same")
    os.utime(second_world / "level.dat", (123456789, 123456789))
    second = acquire_baseline(baseline_open, FakeRuntime(second_world), tmp_path / "second")

    assert first.archive_sha256 == second.archive_sha256
    assert first.tree_sha256 == second.tree_sha256
    with tarfile.open(first.archive_path, "r:gz") as archive:
        members = archive.getmembers()
        assert [member.name for member in members] == sorted(member.name for member in members)
        assert all((member.mtime, member.uid, member.gid, member.uname, member.gname) == (0, 0, 0, "", "") for member in members)
        names = {member.name for member in members}
        assert "world/datapacks/pack/pack.mcmeta" in names
        assert "world/serverconfig/level.toml" in names
        assert "world/ignored.tmp" not in names
    assert first.archive_path.read_bytes()[4:8] == b"\x00\x00\x00\x00"


def test_prepare_capture_restore_validate_writes_required_artifacts(tmp_path):
    runtime = FakeRuntime(_world(tmp_path, "clone"))
    result = acquire_baseline(baseline_open, runtime, tmp_path / "result")

    assert runtime.calls == [("prepare", "baseline_open")]
    assert result.approved
    assert validate_baseline(result.output_directory).approved
    assert {
        "restored_world_manifest.json",
        "baseline_setup_commands.jsonl",
        "baseline_manifest.json",
        "baseline_provenance.json",
        "baseline_validation.json",
    } <= {path.name for path in result.output_directory.iterdir()}
    file_paths = {
        item["path"]
        for item in json.loads((result.output_directory / "restored_world_manifest.json").read_text())["files"]
    }
    assert "datapacks/pack/pack.mcmeta" in file_paths
    provenance = json.loads((result.output_directory / "baseline_provenance.json").read_text())
    assert provenance["approved"] is True
    assert provenance["baseline_id"] == "baseline_open"
    assert provenance["source_revision"] == "a" * 40
    assert provenance["minecraft_version"] == "1.19.2"
    for key in (
        "source_world_identity",
        "runtime_digest",
        "acquisition_tool_sha256",
        "setup_profile_sha256",
        "archive_sha256",
        "restored_tree_sha256",
    ):
        assert len(provenance[key]) == 64
        assert provenance[key] == provenance[key].lower()
    validation = json.loads((result.output_directory / "baseline_validation.json").read_text())
    assert validation["archive_valid"] is True
    assert validation["safe_extraction_valid"] is True
    assert validation["world_structure_valid"] is True
    assert validation["initial_state_valid"] is True
    assert validation["variant_reachability"] == {name: True for name in VARIANT_ORDER}
    assert validation["cleanup_valid"] is True
    assert validation["absolute_path_findings"] is False
    assert validation["credential_findings"] is False


def test_missing_provenance_fails_approval(tmp_path):
    result = _acquire(tmp_path, baseline_open, "result", b"open")
    (result.output_directory / "baseline_provenance.json").unlink()
    validation = validate_baseline(result.output_directory)
    assert not validation.approved
    assert validation.checks == {"complete_artifacts": False}


def test_approval_rejects_failed_probe(tmp_path):
    runtime = FakeRuntime(_world(tmp_path, "clone"))
    prepared = runtime.prepare(baseline_open)
    runtime.prepare = lambda definition: replace(
        prepared,
        probes=(replace(prepared.probes[0], reachable=False), *prepared.probes[1:]),
    )
    with pytest.raises(SnapshotApprovalError, match="all three"):
        acquire_baseline(baseline_open, runtime, tmp_path / "result")


def test_approval_rejects_initial_state_and_semantic_drift(tmp_path):
    runtime = FakeRuntime(_world(tmp_path, "state-clone"))
    prepared = runtime.prepare(baseline_open)
    runtime.prepare = lambda definition: replace(
        prepared,
        observed_initial_state=replace(prepared.observed_initial_state, hunger=19),
    )
    with pytest.raises(SnapshotApprovalError, match="initial state"):
        acquire_baseline(baseline_open, runtime, tmp_path / "state-result")

    semantic_runtime = FakeRuntime(_world(tmp_path, "semantic-clone"))
    semantic_prepared = semantic_runtime.prepare(baseline_obstructed)
    semantic_runtime.prepare = lambda definition: replace(
        semantic_prepared,
        semantic_observation=replace(
            semantic_prepared.semantic_observation,
            changed_block_count=central_wall_v1.changed_block_count - 1,
        ),
    )
    with pytest.raises(SnapshotApprovalError, match="obstacle semantics"):
        acquire_baseline(baseline_obstructed, semantic_runtime, tmp_path / "semantic-result")


def test_approval_rejects_mismatched_source_tree_identity(tmp_path):
    runtime = FakeRuntime(_world(tmp_path, "identity-clone"))
    prepared = runtime.prepare(baseline_open)
    runtime.prepare = lambda definition: replace(
        prepared,
        source=replace(prepared.source, world_sha256="2" * 64),
    )

    with pytest.raises(SnapshotApprovalError, match="does not match"):
        acquire_baseline(baseline_open, runtime, tmp_path / "identity-result")


@pytest.mark.parametrize("unsafe", ["live", "unknown"])
def test_live_or_unknown_source_is_refused(tmp_path, unsafe):
    runtime = FakeRuntime(_world(tmp_path, "clone"), process_state=unsafe)
    with pytest.raises(UnsafeSnapshotSourceError, match="live or unknown"):
        acquire_baseline(baseline_open, runtime, tmp_path / "result")


def test_shared_original_and_symlink_source_are_refused(tmp_path):
    shared = _world(tmp_path, "shared")
    runtime = FakeRuntime(shared, cloned_from="clone-world")
    with pytest.raises(UnsafeSnapshotSourceError, match="clone evidence"):
        acquire_baseline(baseline_open, runtime, tmp_path / "shared-result")

    link = tmp_path / "linked"
    link.symlink_to(shared, target_is_directory=True)
    with pytest.raises(UnsafeSnapshotSourceError, match="real cloned"):
        acquire_baseline(baseline_open, FakeRuntime(link), tmp_path / "link-result")


def test_inactive_session_lock_is_excluded_but_reported_active_lock_is_refused(tmp_path):
    world = _world(tmp_path, "clone")
    (world / "session.lock").write_bytes(b"lock")
    result = acquire_baseline(baseline_open, FakeRuntime(world), tmp_path / "result")
    with tarfile.open(result.archive_path, "r:gz") as archive:
        assert "world/session.lock" not in archive.getnames()
    manifest = json.loads((result.output_directory / "baseline_manifest.json").read_text())
    assert "session.lock" in manifest["archive"]["excluded"]

    active_world = _world(tmp_path, "active-clone")
    with pytest.raises(UnsafeSnapshotSourceError, match="active locks"):
        acquire_baseline(
            baseline_open,
            FakeRuntime(active_world, active_locks=("session.lock",)),
            tmp_path / "active-result",
        )


def test_manifest_provenance_mismatch_and_recorded_approval_fail_validation(tmp_path):
    result = _acquire(tmp_path, baseline_open, "result", b"open")
    provenance_path = result.output_directory / "baseline_provenance.json"
    provenance = json.loads(provenance_path.read_text())
    provenance["archive_sha256"] = "0" * 64
    provenance_path.write_text(json.dumps(provenance))
    validation = validate_baseline(result.output_directory)
    assert not validation.approved
    assert validation.checks["manifest_provenance_consistent"] is False

    provenance["archive_sha256"] = json.loads(
        (result.output_directory / "baseline_manifest.json").read_text()
    )["archive"]["sha256"]
    provenance["approved"] = False
    provenance_path.write_text(json.dumps(provenance))
    validation = validate_baseline(result.output_directory)
    assert not validation.approved
    assert validation.checks["recorded_approval_consistent"] is False


def test_runtime_identity_requires_lowercase_digests_and_full_revision(tmp_path):
    runtime = FakeRuntime(_world(tmp_path, "clone"))
    prepared = runtime.prepare(baseline_open)
    runtime.prepare = lambda definition: replace(
        prepared,
        runtime=replace(prepared.runtime, digest="A" * 64),
    )
    with pytest.raises(SnapshotApprovalError, match="lowercase SHA-256"):
        acquire_baseline(baseline_open, runtime, tmp_path / "result")


def test_pair_approval_and_same_hash_alias_rejection(tmp_path):
    open_result = _acquire(tmp_path, baseline_open, "open", b"open")
    obstructed = _acquire(tmp_path, baseline_obstructed, "obstructed", b"wall")
    report = report_baseline_pair(open_result.output_directory, obstructed.output_directory, tmp_path / "pair")
    assert report["approved"]
    assert (tmp_path / "pair" / "baseline_diff_report.json").is_file()
    assert (tmp_path / "pair" / "baseline_approval_report.md").is_file()

    alias = _acquire(tmp_path, baseline_obstructed, "alias", b"open")
    with pytest.raises(SnapshotApprovalError, match="archive_hashes_distinct"):
        report_baseline_pair(open_result.output_directory, alias.output_directory, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_cli_acquire_fails_closed_without_registered_runtime(tmp_path, capsys):
    assert main(["acquire", "baseline_open", str(tmp_path / "out"), "--runtime", "missing"]) == 2
    assert "no registered acquisition runtime" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()
