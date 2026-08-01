import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from benchmarks.minecraft.docker_runtime import (
    PINNED_IMAGE,
    PINNED_IMAGE_DIGEST,
    SERVER_JAR_SHA256,
    DockerAcquisitionRuntime,
    DockerMatrixExecutor,
    DockerRuntimeError,
    DockerServer,
    _offline_player_uuid,
    _run_with_experiment_manifest,
    _write_probe_operator,
    pinned_runtime_identity,
    register_builtin_runtimes,
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
    def __init__(self, ports=(49152,)):
        self.calls = []
        self.ports = iter(ports)

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
            return subprocess.CompletedProcess(
                argv, 0, f"127.0.0.1:{next(self.ports)}\n", ""
            )
        if argv[:2] == ["docker", "exec"] and "scoreboard players get" in argv[-1]:
            return subprocess.CompletedProcess(argv, 0, "marker has 1 [va_baseline]\n", "")
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
    assert runtime_digest(SERVER_JAR_SHA256) != (
        "63e59662fd8d8b79d99b9910225455af4addfe5e80d5a65023cbaa8ca37c73d0"
    )
    assert pinned_runtime_identity() == {
        "name": "minecraft-1.19.2-local",
        "image": PINNED_IMAGE,
        "digest": f"sha256:{runtime_digest(SERVER_JAR_SHA256)}",
    }


def test_matrix_registration_rejects_stale_runtime_composite(monkeypatch):
    identity = pinned_runtime_identity()
    spec = SimpleNamespace(
        runtime=SimpleNamespace(**identity),
        model=SimpleNamespace(
            provider="ollama",
            name="gemma4:12b",
            digest="model-digest",
        ),
        generation=SimpleNamespace(timeout_seconds=600),
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.docker_runtime.load_matrix_spec",
        lambda _path: spec,
    )
    monkeypatch.setenv("VILLAGER_MINECRAFT_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("VILLAGER_MINECRAFT_MODEL_NAME", "gemma4:12b")
    monkeypatch.setenv("VILLAGER_MINECRAFT_MODEL_DIGEST", "model-digest")
    MATRIX_RUNTIME_ADAPTERS.pop(identity["name"], None)

    register_builtin_runtimes(matrix_premanifest="premanifest.json")

    assert isinstance(MATRIX_RUNTIME_ADAPTERS.pop(identity["name"]), DockerMatrixExecutor)
    spec.runtime.digest = "sha256:" + "0" * 64
    with pytest.raises(DockerRuntimeError, match="pinned adapter"):
        register_builtin_runtimes(matrix_premanifest="premanifest.json")
    assert identity["name"] not in MATRIX_RUNTIME_ADAPTERS


def test_runtime_authorizes_probe_and_meta_judger_operators(tmp_path):
    _write_probe_operator(tmp_path)

    payload = json.loads((tmp_path / "ops.json").read_text(encoding="utf-8"))
    assert payload == [
        {
            "uuid": _offline_player_uuid("VAProbe"),
            "name": "VAProbe",
            "level": 4,
            "bypassesPlayerLimit": True,
        },
        {
            "uuid": _offline_player_uuid("meta_judger"),
            "name": "meta_judger",
            "level": 4,
            "bypassesPlayerLimit": True,
        },
    ]


def test_matrix_experiment_parent_manifest_preserves_child_attempt(tmp_path):
    experiment = tmp_path / "experiment"
    child_attempt = "child-attempt"

    def execute():
        child = experiment / "diagonal-s17-baseline_open"
        child.mkdir()
        (child / "attempt.json").write_text(
            json.dumps({"attempt_id": child_attempt}), encoding="utf-8"
        )
        (child / "summary.json").write_text("{}", encoding="utf-8")
        return {"error": None, "output_dir": str(child)}

    summary = _run_with_experiment_manifest(experiment, execute)

    manifest = json.loads(
        (experiment / "artifact_manifest.json").read_text(encoding="utf-8")
    )
    child = json.loads(
        (experiment / "diagonal-s17-baseline_open" / "attempt.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["error"] is None
    assert manifest["status"] == "completed"
    assert child["attempt_id"] == child_attempt


def test_restart_refreshes_dynamic_host_port_before_runtime_use(tmp_path):
    fake = CommandFake(ports=(49152, 49153))
    server = DockerServer(tmp_path, runner=fake)

    assert server.create_start() == 49152
    assert server.restart_and_verify_marker("baseline_open") == 49153
    assert server.port == 49153
    assert ["docker", "restart", "--time", "30", server.name] in fake.calls
    assert not any(call[:2] == ["docker", "stop"] for call in fake.calls)


def test_runtime_error_retains_sanitized_structured_command_diagnostics(tmp_path):
    stderr = "internal path /private/data\nport allocation failed\nfailed to start containers: safe-name\n"

    def failing(argv, **_kwargs):
        return subprocess.CompletedProcess(
            argv,
            1,
            "",
            stderr,
        )

    server = DockerServer(tmp_path, runner=failing)

    with pytest.raises(DockerRuntimeError) as captured:
        server._run(["docker", "start", "safe-name"])

    assert captured.value.failure_detail["runtime_diagnostics"] == {
        "operation": "start",
        "exit_code": 1,
        "stdout": {
            "safe_output": [], "raw_bytes": 0, "retained_safe_lines": 0,
            "redacted_line_count": 0, "dropped_line_count": 0,
            "truncated": False,
        },
        "stderr": {
            "safe_output": [
                "internal path [REDACTED]",
                "port allocation failed",
                "failed to start containers: safe-name",
            ],
            "raw_bytes": len(stderr.encode("utf-8")), "retained_safe_lines": 3,
            "redacted_line_count": 1, "dropped_line_count": 0,
            "truncated": False,
        },
    }


def test_runtime_error_distinguishes_empty_output_from_fully_redacted_output(tmp_path):
    def failing(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "internal /private/path\n")

    server = DockerServer(tmp_path, runner=failing)

    with pytest.raises(DockerRuntimeError) as captured:
        server._run(["docker", "restart", "safe-name"])

    diagnostics = captured.value.failure_detail["runtime_diagnostics"]
    assert diagnostics["stderr"]["safe_output"] == ["internal [REDACTED]"]
    assert diagnostics["stderr"]["raw_bytes"] > 0
    assert diagnostics["stderr"]["retained_safe_lines"] == 1
    assert diagnostics["stderr"]["redacted_line_count"] == 1
    assert diagnostics["stderr"]["dropped_line_count"] == 0
    assert diagnostics["stderr"]["truncated"] is False


def test_strict_timeout_diagnostics_count_and_sanitize_partial_streams(tmp_path):
    def timing_out(argv, **_kwargs):
        raise subprocess.TimeoutExpired(
            argv,
            60,
            output=b"safe stdout evidence\n",
            stderr=b"token: hidden\n",
        )

    server = DockerServer(tmp_path, runner=timing_out)

    with pytest.raises(DockerRuntimeError) as captured:
        server._run(
            ["docker", "restart", "safe-name"], strict_diagnostics=True
        )

    diagnostics = captured.value.failure_detail["runtime_diagnostics"]
    assert diagnostics["stdout"]["raw_bytes"] == len(b"safe stdout evidence\n")
    assert diagnostics["stderr"]["raw_bytes"] == len(b"token: hidden\n")
    assert diagnostics["stdout"]["safe_output"] == ["safe stdout evidence"]
    assert diagnostics["stderr"]["safe_output"] == ["[REDACTED]"]
    assert diagnostics["stderr"]["redacted_line_count"] == 1
    assert diagnostics["stdout"]["truncated"] is True


def test_strict_restart_diagnostics_replace_validated_container_identity(tmp_path):
    def failing(argv, **_kwargs):
        return subprocess.CompletedProcess(
            argv, 1, "", f"restart failed for {argv[-1]}\n"
        )

    server = DockerServer(tmp_path, runner=failing)

    with pytest.raises(DockerRuntimeError) as captured:
        server._run(
            ["docker", "restart", "--time", "30", server.name],
            strict_diagnostics=True,
        )

    diagnostics = captured.value.failure_detail["runtime_diagnostics"]
    assert diagnostics["stderr"]["safe_output"] == [
        "restart failed for <container>"
    ]
    assert server.name not in json.dumps(diagnostics)


def test_restart_failure_collects_bounded_sanitized_evidence_before_cleanup(tmp_path):
    class RestartFailureFake(CommandFake):
        def __call__(self, argv, **kwargs):
            self.calls.append(list(argv))
            if argv[:2] == ["docker", "restart"]:
                return subprocess.CompletedProcess(
                    argv, 1, "", "daemon socket /var/run/docker.sock\n"
                )
            if argv[:4] == ["docker", "inspect", "--type", "container"]:
                state = {
                    "State": {
                        "Status": "exited", "Running": False, "Paused": False,
                        "Restarting": False, "OOMKilled": False, "Dead": False,
                        "ExitCode": 1, "Error": "",
                        "StartedAt": "2026-08-01T01:00:00Z",
                        "FinishedAt": "2026-08-01T01:01:00Z",
                        "Health": {
                            "Status": "unhealthy", "FailingStreak": 4,
                            "Log": [{
                                "Start": "2026-08-01T01:00:30Z",
                                "End": "2026-08-01T01:00:31Z",
                                "ExitCode": 1,
                                "Output": "failed mount /data/world\n",
                            }],
                        },
                    },
                    "RestartCount": 0,
                }
                return subprocess.CompletedProcess(
                    argv, 0, json.dumps(state), "",
                )
            if argv[:2] == ["docker", "logs"]:
                lines = [f"safe diagnostic line {index}" for index in range(7)]
                lines.extend(
                    [
                        "Authorization: Bearer hidden",
                        "internal /private/path",
                        "0123456789abcdef0123456789abcdef",
                    ]
                )
                return subprocess.CompletedProcess(argv, 0, "\n".join(lines) + "\n", "")
            if argv[:2] == ["docker", "events"]:
                return subprocess.CompletedProcess(
                    argv, 0, "time 1 type container action die\n", ""
                )
            if argv[:3] == ["docker", "ps", "-a"]:
                return subprocess.CompletedProcess(
                    argv, 0, "id abc123 name safe state exited status stopped\n", ""
                )
            if argv[:2] == ["docker", "inspect"]:
                return subprocess.CompletedProcess(argv, 1, "", "not found")
            return subprocess.CompletedProcess(argv, 0, "", "")

    fake = RestartFailureFake()
    server = DockerServer(tmp_path, runner=fake)
    server.created = server.running = True

    with pytest.raises(DockerRuntimeError) as captured:
        server.restart_and_verify_marker("baseline_open")
    assert server.cleanup() is True

    diagnostics = captured.value.failure_detail["runtime_diagnostics"]
    assert diagnostics["operation"] == "restart"
    assert diagnostics["stderr"]["raw_bytes"] > 0
    assert diagnostics["stderr"]["safe_output"] == [
        "daemon socket [REDACTED]"
    ]
    assert diagnostics["stderr"]["redacted_line_count"] == 1
    evidence = diagnostics["restart_failure_evidence"]
    assert evidence["schema_version"] == 2
    assert evidence["collection_complete"] is True
    assert evidence["target_valid"] is True
    assert evidence["logs_tail"] == {
        "outcome": "ok",
        "exit_code": 0,
        "stdout": {
            "safe_output": [
                "safe diagnostic line 4", "safe diagnostic line 5",
                "safe diagnostic line 6", "[REDACTED]",
                "internal [REDACTED]",
            ],
            "raw_bytes": len(("\n".join(
                [f"safe diagnostic line {index}" for index in range(7)] + [
                    "Authorization: Bearer hidden", "internal /private/path",
                    "0123456789abcdef0123456789abcdef",
                ]) + "\n").encode("utf-8")),
            "retained_safe_lines": 5, "redacted_line_count": 3,
            "dropped_line_count": 4, "truncated": True,
        },
        "stderr": {
            "safe_output": [], "raw_bytes": 0, "retained_safe_lines": 0,
            "redacted_line_count": 0, "dropped_line_count": 0,
            "truncated": False,
        },
    }
    state = evidence["inspect_state"]["state"]
    assert state["started_at"] == "2026-08-01T01:00:00Z"
    assert state["finished_at"] == "2026-08-01T01:01:00Z"
    assert state["health"]["failing_streak"] == 4
    assert state["health"]["log"][0]["output"]["safe_output"] == [
        "failed mount [REDACTED]"
    ]
    serialized = json.dumps(evidence).lower()
    assert "bearer hidden" not in serialized
    assert "/private/path" not in serialized
    assert "0123456789abcdef0123456789abcdef" not in serialized

    events = next(call for call in fake.calls if call[:2] == ["docker", "events"])
    assert events[events.index("--since") + 1].isdigit()
    assert events[events.index("--until") + 1].isdigit()
    assert ["--filter", "type=container"] == events[
        events.index("--filter"):events.index("--filter") + 2
    ]
    assert f"container={server.name}" in events
    ps = next(call for call in fake.calls if call[:3] == ["docker", "ps", "-a"])
    assert f"name=^/{server.name}$" in ps
    restart_index = next(i for i, call in enumerate(fake.calls) if call[:2] == ["docker", "restart"])
    remove_index = next(i for i, call in enumerate(fake.calls) if call[:3] == ["docker", "rm", "-f"])
    for operation in ("inspect", "logs", "events", "ps"):
        diagnostic_index = next(
            i
            for i, call in enumerate(fake.calls)
            if i > restart_index
            and call[:2] == ["docker", operation]
            and not (operation == "inspect" and "--type" not in call)
        )
        assert diagnostic_index < remove_index


def test_restart_failure_diagnostics_never_mask_original_error(tmp_path):
    calls = []

    def failing(argv, **_kwargs):
        calls.append(list(argv))
        if argv[:2] == ["docker", "restart"]:
            return subprocess.CompletedProcess(argv, 1, "", "restart failed safely\n")
        raise OSError("diagnostic runner unavailable")

    server = DockerServer(tmp_path, runner=failing)

    with pytest.raises(DockerRuntimeError, match="restart failed safely") as captured:
        server.restart_and_verify_marker("baseline_open")

    evidence = captured.value.failure_detail["runtime_diagnostics"][
        "restart_failure_evidence"
    ]
    assert evidence["collection_complete"] is False
    assert all(
        evidence[key]["outcome"] == "runner_error"
        for key in ("inspect_state", "logs_tail", "events_window", "ps_exact_name")
    )
    assert "diagnostic runner unavailable" not in json.dumps(evidence)


def test_restart_failure_skips_collection_for_invalid_container_name(tmp_path):
    calls = []

    def failing(argv, **_kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 1, "", "restart failed safely\n")

    server = DockerServer(tmp_path, runner=failing)
    server.name = "unsafe.*"

    with pytest.raises(DockerRuntimeError) as captured:
        server.restart_and_verify_marker("baseline_open")

    evidence = captured.value.failure_detail["runtime_diagnostics"][
        "restart_failure_evidence"
    ]
    assert evidence["schema_version"] == 2
    assert evidence["collection_complete"] is False
    assert evidence["target_valid"] is False
    assert set(evidence) == {
        "schema_version", "collection_complete", "target_valid",
        "diagnostics_implementation_sha256",
        "inspect_state", "logs_tail", "events_window", "ps_exact_name",
    }
    assert all(evidence[key]["outcome"] == "not_attempted" for key in (
        "inspect_state", "logs_tail", "events_window", "ps_exact_name"
    ))
    assert calls == [["docker", "restart", "--time", "30", "unsafe.*"]]


def test_restart_failure_uses_stable_schema_when_collector_raises(tmp_path):
    def failing_restart(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "restart failed safely\n")

    def failing_collector(*_args):
        raise RuntimeError("collector failed")

    server = DockerServer(
        tmp_path,
        runner=failing_restart,
        diagnostic_collector=failing_collector,
    )

    with pytest.raises(DockerRuntimeError) as captured:
        server.restart_and_verify_marker("baseline_open")

    evidence = captured.value.failure_detail["runtime_diagnostics"][
        "restart_failure_evidence"
    ]
    assert evidence["collection_complete"] is False
    assert evidence["target_valid"] is True
    assert all(
        evidence[key]["outcome"] == "collector_error"
        for key in ("inspect_state", "logs_tail", "events_window", "ps_exact_name")
    )
    assert "collector failed" not in json.dumps(evidence)


@pytest.mark.parametrize("cleanup_result", [False, OSError("cleanup failed")])
def test_matrix_cleanup_failure_preserves_restart_evidence(
    monkeypatch, tmp_path, cleanup_result
):
    evidence = {"schema_version": 1, "collection_complete": True}

    class FailingServer:
        def __init__(self, data_root, **_kwargs):
            (data_root / "minecraft_server.1.19.2.jar").write_bytes(b"jar")

        def create_start(self):
            return 25565

        def restart_and_verify_marker(self, _baseline_id):
            error = DockerRuntimeError(
                "restart failed",
                diagnostics={"restart_failure_evidence": evidence},
            )
            raise error

        def cleanup(self):
            if isinstance(cleanup_result, Exception):
                raise cleanup_result
            return cleanup_result

    monkeypatch.setattr(
        "benchmarks.minecraft.docker_runtime.DockerServer", FailingServer
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.docker_runtime.canonical_world_tree_identity",
        lambda _world: "tree",
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.docker_runtime._sha",
        lambda _path, algorithm="sha256": (
            "f69c284232d7c7580bd89a5a4931c3581eae1378"
            if algorithm == "sha1"
            else SERVER_JAR_SHA256
        ),
    )
    executor = DockerMatrixExecutor({
        "runtime": {
            "digest": f"sha256:{runtime_digest(SERVER_JAR_SHA256)}",
        },
        "model": {},
        "generation": {"timeout_seconds": 60},
    })
    world = tmp_path / "world"
    world.mkdir()
    restored = SimpleNamespace(
        descriptor=SimpleNamespace(snapshot_id="baseline_open"),
        world_directory=world,
        tree_identity="tree",
    )
    run = SimpleNamespace(baseline_id="baseline_open")

    with pytest.raises(DockerRuntimeError, match="cleanup could not be verified") as captured:
        executor(run=run, restored_world=restored, output_dir=tmp_path / "output")

    detail = captured.value.failure_detail
    assert detail["runtime_diagnostics"]["restart_failure_evidence"] == evidence
    assert detail["cleanup"] == {"attempted": True, "passed": False}
    if isinstance(cleanup_result, Exception):
        assert detail["runtime_diagnostics"]["cleanup_error_type"] == "OSError"


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
    def probes(definition):
        return [
            {
                "variant_id": variant_id,
                "target": {
                    **target.as_dict(),
                    "tolerance": 1.0,
                    "position_convention": "entity_feet",
                },
                "position_convention": "entity_feet",
                "delta": {"x": 0.0, "y": 0.0, "z": 0.0},
                "support_block_type": "minecraft:stone",
                "support_block_collision_box": "block",
                "support_block_shapes": [[0, 0, 0, 1, 1, 1]],
                "falling": False,
                "reachable": True,
            }
            for variant_id, target in definition.targets
        ]

    observed = {
        "commands_executed": len(baseline_obstructed.preparation_commands),
        "save_acknowledged": True,
        "blocks": [{"observed": item.block} for item in central_wall_v1.blocks],
        "opening": [{"observed": "minecraft:air"} for _ in central_wall_v1.opening],
        "state": {"position_convention": "entity_feet"},
        "probes": probes(baseline_obstructed),
    }
    DockerAcquisitionRuntime._validate_probe(baseline_obstructed, observed)
    observed["state"].pop("position_convention")
    with pytest.raises(DockerRuntimeError, match="state position convention"):
        DockerAcquisitionRuntime._validate_probe(baseline_obstructed, observed)
    observed["state"]["position_convention"] = "entity_feet"
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
        "state": {"position_convention": "entity_feet"},
        "probes": probes(baseline_open),
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
        return executor

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


def test_matrix_executor_preserves_the_restored_snapshot(monkeypatch):
    monkeypatch.setenv("VILLAGER_MINECRAFT_MODEL_API_BASE", "http://model.test/v1")
    monkeypatch.setenv("VILLAGER_MINECRAFT_MODEL_API_KEY_ENV", "MODEL_KEY")
    monkeypatch.setenv("MODEL_KEY", "secret")
    executor = DockerMatrixExecutor({
        "model": {"name": "model", "digest": "sha256:model"},
        "generation": {"timeout_seconds": 60},
    })
    run = SimpleNamespace(
        prompt="Move to (5, -60, 5). You can go there directly.",
        evaluation_target=SimpleNamespace(
            as_dict=lambda: {"x": 5, "y": -60, "z": 5}
        ),
        initial_state=SimpleNamespace(
            as_dict=lambda: {"x": 14, "y": -59, "z": 5}
        ),
        run_id="diagonal-seed-0-open",
        baseline_id="baseline_open",
        snapshot_path="baseline-open.tar.gz",
        snapshot_sha256="a" * 64,
        seed=0,
        seed_scopes=SimpleNamespace(requested=("meta_judger",)),
        position_convention="entity_feet",
    )

    config = executor._config(run, 25565, None)

    assert config["world_initialization"] == "preserve_restored_snapshot"
    assert config["position_convention"] == "entity_feet"
    assert config["evaluation_arg"]["position_convention"] == "entity_feet"
    assert config["evaluation_arg"]["initial_state"] == {
        "x": 14,
        "y": -59,
        "z": 5,
        "position_convention": "entity_feet",
    }
    run.position_convention = "support_block"
    with pytest.raises(DockerRuntimeError, match="entity_feet"):
        executor._config(run, 25565, None)


@pytest.mark.skipif(os.environ.get("VILLAGER_RUN_DOCKER_INTEGRATION") != "1", reason="opt-in Docker integration")
def test_real_docker_pinned_image_identity(tmp_path):
    assert DockerServer(tmp_path).verify_image() == PINNED_IMAGE_DIGEST
