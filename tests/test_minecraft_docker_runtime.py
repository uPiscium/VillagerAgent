import json
import os
import subprocess

import pytest

from benchmarks.minecraft.docker_runtime import (
    PINNED_IMAGE,
    PINNED_IMAGE_DIGEST,
    DockerAcquisitionRuntime,
    DockerRuntimeError,
    DockerServer,
    runtime_digest,
)
from benchmarks.minecraft.matrix import RUNTIME_ADAPTERS as MATRIX_RUNTIME_ADAPTERS
from benchmarks.minecraft.matrix import main as matrix_main
from benchmarks.minecraft.snapshot_acquisition import (
    RUNTIME_ADAPTERS,
    SOURCE_MARKER,
    SnapshotAcquisitionError,
    baseline_obstructed,
    baseline_open,
    central_wall_v1,
    main as acquisition_main,
)


class CommandFake:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[:3] == ["docker", "image", "inspect"]:
            payload = {
                "Id": PINNED_IMAGE_DIGEST,
                "RepoDigests": [f"itzg/minecraft-server@{PINNED_IMAGE_DIGEST}"],
                "Config": {"Labels": {"org.opencontainers.image.revision": "162bd9b5f19a0de2870407a4406506aeb0fe5a99"}},
            }
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        if argv[:2] == ["docker", "inspect"] and "--format" in argv:
            return subprocess.CompletedProcess(argv, 0, "healthy\n", "")
        if argv[:2] == ["docker", "port"]:
            return subprocess.CompletedProcess(argv, 0, "127.0.0.1:49152\n", "")
        if argv[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(argv, 1, "", "not found")
        return subprocess.CompletedProcess(argv, 0, "", "")


def test_server_uses_exact_digest_safe_bind_and_random_local_port(tmp_path):
    fake = CommandFake()
    server = DockerServer(tmp_path, runner=fake, memory="3G")

    assert server.create_start() == 49152

    create = next(call for call in fake.calls if call[:2] == ["docker", "create"])
    assert create[-1] == PINNED_IMAGE
    assert ["-p", "127.0.0.1::25565"] == create[create.index("-p"):create.index("-p") + 2]
    assert f"type=bind,src={tmp_path.resolve()},dst=/data" in create
    assert "VERSION=1.19.2" in create
    assert "ONLINE_MODE=FALSE" in create
    assert "MEMORY=3G" in create
    assert "UID=0" in create
    assert "GID=0" in create
    assert "SPAWN_PROTECTION=0" in create
    assert not any("RCON_PASSWORD" in item for item in create)
    assert not any(item == "pull" for call in fake.calls for item in call)


def test_runtime_digest_is_deterministic_composite():
    first = runtime_digest("1" * 64)
    assert first == runtime_digest("1" * 64)
    assert first != runtime_digest("2" * 64)
    assert len(first) == 64
    int(first, 16)


def test_cleanup_attempts_stop_remove_and_proves_absence(tmp_path):
    fake = CommandFake()
    server = DockerServer(tmp_path, runner=fake)
    server.created = server.running = True
    server.image_verified = True

    assert server.cleanup() is True
    assert any(call[:3] == ["docker", "stop", "--time"] for call in fake.calls)
    assert any(call[:3] == ["docker", "rm", "-f"] for call in fake.calls)
    assert any(call[:2] == ["docker", "inspect"] for call in fake.calls)
    assert fake.calls[-1][:3] == ["docker", "run", "--rm"]


def test_probe_validation_requires_all_commands_blocks_opening_and_paths():
    observed = {
        "commands_executed": len(baseline_obstructed.preparation_commands),
        "save_acknowledged": True,
        "blocks": [{"observed": item.block} for item in central_wall_v1.blocks],
        "opening": [{"observed": "minecraft:air"} for _ in central_wall_v1.opening],
        "probes": [{"reachable": True} for _ in range(3)],
    }
    DockerAcquisitionRuntime._validate_probe(baseline_obstructed, observed)
    observed["probes"][1]["reachable"] = False
    with pytest.raises(DockerRuntimeError, match="strict tolerance"):
        DockerAcquisitionRuntime._validate_probe(baseline_obstructed, observed)

    open_observed = {
        "commands_executed": len(baseline_open.preparation_commands),
        "save_acknowledged": True,
        "blocks": [
            {"y": item.y, "observed": "minecraft:air"}
            for item in central_wall_v1.blocks
        ],
        "opening": [{"observed": "minecraft:air"} for _ in central_wall_v1.opening],
        "probes": [{"reachable": True} for _ in range(3)],
    }
    DockerAcquisitionRuntime._validate_probe(baseline_open, open_observed)


def test_baselines_contain_distinct_in_world_markers():
    assert f"/scoreboard players set {SOURCE_MARKER} va_baseline 1" in baseline_open.preparation_commands
    assert "/scoreboard players set baseline_open va_baseline 1" in baseline_open.preparation_commands
    assert "/scoreboard players set baseline_obstructed va_baseline 1" in baseline_obstructed.preparation_commands
    assert baseline_obstructed.preparation_commands[-4:-2] == central_wall_v1.commands


def test_acquisition_main_lazily_registers_builtin(monkeypatch, tmp_path):
    calls = []

    class FailingRuntime:
        def prepare(self, _definition):
            raise SnapshotAcquisitionError("registered sentinel")

    def register(*, acquisition=False, matrix_premanifest=None):
        calls.append((acquisition, matrix_premanifest))
        RUNTIME_ADAPTERS["minecraft-1.19.2-local"] = FailingRuntime()

    monkeypatch.setattr("benchmarks.minecraft.docker_runtime.register_builtin_runtimes", register)
    RUNTIME_ADAPTERS.pop("minecraft-1.19.2-local", None)
    assert acquisition_main([
        "acquire", "baseline_open", str(tmp_path / "out"),
        "--runtime", "minecraft-1.19.2-local",
    ]) == 2
    assert calls == [(True, None)]


def test_prepare_failure_cleans_container_and_temporary_root(monkeypatch, tmp_path):
    archive = tmp_path / "source.tar.gz"
    archive.write_bytes(b"source")
    archive_hash = __import__("hashlib").sha256(archive.read_bytes()).hexdigest()
    cleanup = []
    roots = []

    class FailingServer:
        def __init__(self, data_root, **_kwargs):
            roots.append(data_root)

        def create_start(self):
            raise DockerRuntimeError("startup failed")

        def cleanup(self):
            cleanup.append(True)
            return True

    def restore(_descriptor, destination):
        (destination / "region").mkdir(parents=True)
        (destination / "level.dat").write_bytes(b"level")
        from benchmarks.minecraft.world_snapshot import RestoredWorld, WorldTreeIdentity
        return RestoredWorld(_descriptor, destination, WorldTreeIdentity("0" * 64, 1))

    monkeypatch.setattr("benchmarks.minecraft.docker_runtime.DEFAULT_SOURCE_ARCHIVE", archive.name)
    monkeypatch.setattr("benchmarks.minecraft.docker_runtime.SOURCE_ARCHIVE_SHA256", archive_hash)
    monkeypatch.setattr("benchmarks.minecraft.docker_runtime.DockerServer", FailingServer)
    monkeypatch.setattr("benchmarks.minecraft.docker_runtime.restore_world_snapshot", restore)
    monkeypatch.setattr("benchmarks.minecraft.docker_runtime._git_revision", lambda _root: "a" * 40)

    with pytest.raises(DockerRuntimeError, match="startup failed"):
        DockerAcquisitionRuntime(repo_root=tmp_path).prepare(baseline_open)
    assert cleanup == [True]
    assert len(roots) == 1 and not roots[0].exists()


def test_matrix_main_lazily_registers_builtin(monkeypatch, tmp_path):
    calls = []
    executor = object()

    def register(*, acquisition=False, matrix_premanifest=None):
        calls.append((acquisition, matrix_premanifest))
        MATRIX_RUNTIME_ADAPTERS["minecraft-1.19.2-local"] = executor

    monkeypatch.setattr("benchmarks.minecraft.docker_runtime.register_builtin_runtimes", register)
    monkeypatch.setattr(
        "benchmarks.minecraft.matrix_runner.run_finalized_matrix",
        lambda premanifest, output, **kwargs: {
            "gate_passed": kwargs["executor"] is executor,
            "premanifest": premanifest,
            "output": str(output),
        },
    )
    MATRIX_RUNTIME_ADAPTERS.pop("minecraft-1.19.2-local", None)
    premanifest = tmp_path / "premanifest.json"
    assert matrix_main([
        "run", str(premanifest), "--output-dir", str(tmp_path / "matrix"),
        "--runtime-adapter", "minecraft-1.19.2-local",
    ]) == 0
    assert calls == [(False, str(premanifest))]


@pytest.mark.skipif(os.environ.get("VILLAGER_RUN_DOCKER_INTEGRATION") != "1", reason="opt-in Docker integration")
def test_real_docker_pinned_image_identity(tmp_path):
    assert DockerServer(tmp_path).verify_image() == PINNED_IMAGE_DIGEST
