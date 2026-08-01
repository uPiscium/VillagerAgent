from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from benchmarks.minecraft.matrix_spec import MatrixRunSpec, load_matrix_spec
from benchmarks.common.run_artifacts import finalize_run_directory, prepare_run_directory
from benchmarks.minecraft.snapshot_acquisition import (
    BASELINE_DEFINITIONS,
    SOURCE_ARCHIVE_SHA256,
    SOURCE_MARKER,
    BaselineDefinition,
    BaselineInitialState,
    CleanupEvidence,
    InventoryState,
    PreparedBaseline,
    QuiescenceEvidence,
    ReachabilityProbeResult,
    RuntimeIdentity,
    SemanticObservation,
    SnapshotAcquisitionError,
    SourceIdentity,
    central_wall_v1,
)
from benchmarks.minecraft.world_snapshot import (
    RestoredWorld,
    WorldSnapshotDescriptor,
    canonical_world_tree_identity,
    restore_world_snapshot,
)
from env.world_initialization import PRESERVE_RESTORED_SNAPSHOT
from benchmarks.minecraft.position_contract import PositionConvention
from benchmarks.minecraft.docker_diagnostics import (
    BoundedDiagnosticExecutor,
    DiagnosticCommandResult,
    collect_restart_failure_evidence,
    empty_restart_failure_evidence,
    is_valid_container_name,
    output_bytes as _output_bytes,
    sanitize_output as _sanitize_output,
)


ADAPTER_ID = "minecraft-1.19.2-local"
MINECRAFT_VERSION = "1.19.2"
PINNED_IMAGE_DIGEST = "sha256:5b3e96bcd7dace8ab7be89c245dc9ba0b1573fdef2f01d3e101ac40e7843fa70"
PINNED_IMAGE = f"docker.io/itzg/minecraft-server@{PINNED_IMAGE_DIGEST}"
IMAGE_SOURCE_REVISION = "162bd9b5f19a0de2870407a4406506aeb0fe5a99"
SERVER_METADATA_SHA1 = "ed548106acf3ac7e8205a6ee8fd2710facfa164f"
SERVER_JAR_SHA1 = "f69c284232d7c7580bd89a5a4931c3581eae1378"
SERVER_JAR_SHA256 = "b26727069ef5f61c704add9a378ac90e3d271fd7876c0bd3dcfbe9fd0bec4d96"
DEFAULT_SOURCE_ARCHIVE = Path("result/minecraft/issue_243_assets/meta-move-world-v1.tar.gz")
PROBE_PATH = Path(__file__).with_name("docker_probe.js")
DOCKER_DIAGNOSTICS_PATH = Path(__file__).with_name("docker_diagnostics.py")
PACKAGE_LOCK_PATH = Path(__file__).resolve().parents[2] / "package-lock.json"
SERVER_JAR_PATH = Path("minecraft_server.1.19.2.jar")
_SAFE_OUTPUT = re.compile(r"[\x20-\x7e]{1,300}\Z")


class DockerRuntimeError(SnapshotAcquisitionError):
    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None):
        super().__init__(message)
        self.failure_detail = {
            "reason": "docker_runtime_error",
            "runtime_diagnostics": diagnostics or {},
        }


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run_with_experiment_manifest(
    experiment_dir: Path, execute: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    producer = "benchmarks.minecraft.docker_runtime"
    attempt_id = prepare_run_directory(experiment_dir, producer=producer)
    try:
        summary = execute()
    except BaseException:
        finalize_run_directory(
            experiment_dir,
            attempt_id=attempt_id,
            producer=producer,
            status="failed",
            stamp_nested=False,
        )
        raise
    finalize_run_directory(
        experiment_dir,
        attempt_id=attempt_id,
        producer=producer,
        status="failed" if summary.get("error") is not None else "completed",
        stamp_nested=False,
    )
    return summary


def _sha(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_digest(server_jar_sha256: str) -> str:
    components = {
        "adapter": _sha(Path(__file__)),
        "diagnostics": _sha(DOCKER_DIAGNOSTICS_PATH),
        "image": PINNED_IMAGE_DIGEST,
        "image_source_revision": IMAGE_SOURCE_REVISION,
        "node_dependencies": _sha(PACKAGE_LOCK_PATH),
        "probe": _sha(PROBE_PATH),
        "server_jar": server_jar_sha256,
        "server_metadata_sha1": SERVER_METADATA_SHA1,
    }
    encoded = json.dumps(components, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def pinned_runtime_identity() -> dict[str, str]:
    return {
        "name": ADAPTER_ID,
        "image": PINNED_IMAGE,
        "digest": f"sha256:{runtime_digest(SERVER_JAR_SHA256)}",
    }


class DockerServer:
    def __init__(self, data_root: Path, *, runner: Runner | None = None, memory: str = "2G", diagnostic_executor: Callable[..., DiagnosticCommandResult] | None = None, diagnostic_collector: Callable[..., dict[str, Any]] | None = None):
        self.data_root = data_root
        self.runner = runner or subprocess.run
        self.diagnostic_executor = diagnostic_executor or BoundedDiagnosticExecutor(runner)
        self.diagnostic_collector = diagnostic_collector or collect_restart_failure_evidence
        self.memory = memory
        self.name = f"va-mc-{uuid.uuid4().hex}"
        self.image_verified = False
        self.created = False
        self.running = False
        self.port: int | None = None

    def _run(
        self,
        argv: list[str],
        *,
        timeout: float = 30,
        check: bool = True,
        strict_diagnostics: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        bounded_counts: tuple[int, int, bool, bool] | None = None
        try:
            if strict_diagnostics:
                bounded = self.diagnostic_executor(argv, timeout=timeout)
                returncode = bounded.returncode
                bounded_stdout = bounded.stdout
                bounded_stderr = bounded.stderr
                stdout_bytes = bounded.stdout_bytes
                stderr_bytes = bounded.stderr_bytes
                outcome = bounded.outcome
                if outcome == "timeout":
                    error = subprocess.TimeoutExpired(
                        argv,
                        timeout,
                        output=bounded_stdout,
                        stderr=bounded_stderr,
                    )
                    error.raw_stdout_bytes = stdout_bytes
                    error.raw_stderr_bytes = stderr_bytes
                    raise error
                bounded_counts = (
                    stdout_bytes,
                    stderr_bytes,
                    bounded.stdout_truncated,
                    bounded.stderr_truncated,
                )
                result = subprocess.CompletedProcess(
                    argv, returncode, bounded_stdout, bounded_stderr
                )
            else:
                result = self.runner(
                    argv,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
        except (OSError, subprocess.SubprocessError) as exc:
            output = _sanitize_output(
                _output_bytes(getattr(exc, "stdout", None)),
                _output_bytes(getattr(exc, "stderr", None)),
                strict=strict_diagnostics,
                stdout_bytes=getattr(exc, "raw_stdout_bytes", None),
                stderr_bytes=getattr(exc, "raw_stderr_bytes", None),
                pre_truncated=isinstance(exc, subprocess.TimeoutExpired),
                safe_replacements=(self.name,) if is_valid_container_name(self.name) else (),
            )
            raise DockerRuntimeError(
                f"runtime command failed: {type(exc).__name__}",
                diagnostics={
                    "operation": argv[1] if len(argv) > 1 else "unknown",
                    "error_type": type(exc).__name__,
                    **output,
                },
            ) from exc
        stdout = _output_bytes(result.stdout)
        stderr = _output_bytes(result.stderr)
        normalized = subprocess.CompletedProcess(
            argv,
            result.returncode,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )
        if check and result.returncode != 0:
            stdout_bytes, stderr_bytes, stdout_truncated, stderr_truncated = (
                bounded_counts
                if bounded_counts is not None
                else (None, None, False, False)
            )
            output = _sanitize_output(
                stdout,
                stderr,
                strict=strict_diagnostics,
                stdout_bytes=stdout_bytes,
                stderr_bytes=stderr_bytes,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
                safe_replacements=(self.name,) if is_valid_container_name(self.name) else (),
            )
            safe_output = (
                output["stderr"]["safe_output"]
                or output["stdout"]["safe_output"]
            )
            candidate = safe_output[-1] if safe_output else ""
            safe = candidate if _SAFE_OUTPUT.fullmatch(candidate) else "command rejected"
            raise DockerRuntimeError(
                f"runtime command failed: {safe}",
                diagnostics={
                    "operation": argv[1] if len(argv) > 1 else "unknown",
                    "exit_code": result.returncode,
                    **output,
                },
            )
        return normalized

    def verify_image(self) -> str:
        result = self._run(["docker", "image", "inspect", PINNED_IMAGE, "--format", "{{json .}}"])
        try:
            image = json.loads(result.stdout)
            image_id = image["Id"]
            digests = image["RepoDigests"]
            source_revision = image["Config"]["Labels"]["org.opencontainers.image.revision"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise DockerRuntimeError("pinned image inspection was invalid") from exc
        if PINNED_IMAGE_DIGEST not in {str(item).rsplit("@", 1)[-1] for item in digests}:
            raise DockerRuntimeError("local image does not have the required RepoDigest")
        if image_id != PINNED_IMAGE_DIGEST or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise DockerRuntimeError("local image ID does not match the pinned digest")
        if source_revision != IMAGE_SOURCE_REVISION:
            raise DockerRuntimeError("local image source revision does not match the pinned identity")
        self.image_verified = True
        return image_id

    def create_start(self) -> int:
        self.verify_image()
        _write_probe_operator(self.data_root)
        argv = [
            "docker", "create", "--name", self.name,
            "--mount", f"type=bind,src={self.data_root.resolve()},dst=/data",
            "-p", "127.0.0.1::25565",
            "-e", "EULA=TRUE", "-e", "TYPE=VANILLA", "-e", f"VERSION={MINECRAFT_VERSION}",
            "-e", "ONLINE_MODE=FALSE", "-e", f"MEMORY={self.memory}",
            "-e", "UID=0", "-e", "GID=0",
            "-e", "SPAWN_PROTECTION=0", "-e", "ENABLE_RCON=true", PINNED_IMAGE,
        ]
        self._run(argv, timeout=60)
        self.created = True
        self._run(["docker", "start", self.name], timeout=60)
        self.running = True
        self._wait_healthy()
        return self._published_port()

    def _published_port(self) -> int:
        ports = self._run(["docker", "port", self.name, "25565/tcp"]).stdout.strip()
        try:
            self.port = int(ports.rsplit(":", 1)[-1])
        except ValueError as exc:
            raise DockerRuntimeError("Docker did not publish a valid Minecraft port") from exc
        return self.port

    def _wait_healthy(self, timeout: float = 180) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self._run(
                ["docker", "inspect", self.name, "--format", "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}"],
                check=False,
            )
            status = result.stdout.strip()
            if result.returncode == 0 and status == "healthy":
                return
            if status in {"dead", "exited"}:
                raise DockerRuntimeError("Minecraft container exited before becoming healthy")
            time.sleep(1)
        raise DockerRuntimeError("Minecraft container health check timed out")

    def rcon(self, command: str) -> str:
        result = self._run(
            ["docker", "exec", self.name, "rcon-cli", command],
            timeout=30,
        )
        return result.stdout.strip()

    def stop(self) -> None:
        if self.running:
            self._run(["docker", "stop", "--time", "30", self.name], timeout=45, check=False)
            self.running = False

    def restart_and_verify_marker(self, baseline_id: str) -> int:
        restart_started = time.time()
        try:
            self._run(
                ["docker", "restart", "--time", "30", self.name],
                timeout=60,
                strict_diagnostics=True,
            )
        except DockerRuntimeError as exc:
            try:
                evidence = self.diagnostic_collector(
                    self.name, restart_started, self.diagnostic_executor
                )
            except Exception:
                evidence = empty_restart_failure_evidence(
                    target_valid=is_valid_container_name(self.name),
                    outcome="collector_error",
                )
            diagnostics = exc.failure_detail.setdefault("runtime_diagnostics", {})
            diagnostics["restart_failure_evidence"] = evidence
            raise
        self.running = True
        self._wait_healthy()
        port = self._published_port()
        for marker in (baseline_id, SOURCE_MARKER):
            response = self.rcon(f"scoreboard players get {marker} va_baseline")
            if not response.endswith("has 1 [va_baseline]"):
                raise DockerRuntimeError("restored world scoreboard marker verification failed")
        return port

    def cleanup(self) -> bool:
        self.stop()
        if self.created:
            self._run(["docker", "rm", "-f", self.name], timeout=30, check=False)
        inspect = self._run(["docker", "inspect", self.name], check=False)
        self.created = False
        ownership_ok = True
        if self.image_verified and self.data_root.exists():
            ownership = self._run(
                [
                    "docker", "run", "--rm", "--user", "0:0", "--entrypoint", "chown",
                    "--mount", f"type=bind,src={self.data_root.resolve()},dst=/data",
                    PINNED_IMAGE, "-R", "0:0", "/data",
                ],
                timeout=120,
                check=False,
            )
            ownership_ok = ownership.returncode == 0
        return inspect.returncode != 0 and ownership_ok


class DockerAcquisitionRuntime:
    def __init__(self, *, repo_root: Path | None = None, runner: Runner | None = None):
        self.repo_root = repo_root or Path(__file__).resolve().parents[2]
        self.runner = runner or subprocess.run
        self.diagnostic_executor = BoundedDiagnosticExecutor(runner)

    def prepare(self, definition: BaselineDefinition) -> PreparedBaseline:
        if BASELINE_DEFINITIONS.get(definition.baseline_id) != definition:
            raise DockerRuntimeError("baseline definition is not canonical")
        archive = self.repo_root / DEFAULT_SOURCE_ARCHIVE
        if not archive.is_file() or _sha(archive) != SOURCE_ARCHIVE_SHA256:
            raise DockerRuntimeError("approved source archive is missing or has the wrong identity")
        source_revision = _git_revision(self.repo_root)
        data_root = Path(tempfile.mkdtemp(prefix="va-minecraft-acquire-"))
        server = DockerServer(
            data_root, runner=self.runner, diagnostic_executor=self.diagnostic_executor
        )
        success = False
        try:
            restored = restore_world_snapshot(
                WorldSnapshotDescriptor("approved-source", archive, SOURCE_ARCHIVE_SHA256), data_root / "world"
            )
            port = server.create_start()
            jar = data_root / SERVER_JAR_PATH
            if not jar.is_file() or _sha(jar, "sha1") != SERVER_JAR_SHA1:
                raise DockerRuntimeError("downloaded Minecraft server JAR failed official SHA-1 verification")
            jar_sha256 = _sha(jar)
            observation = self._probe(definition, port, server)
            self._verify_server_settings(server)
            server.restart_and_verify_marker(definition.baseline_id)
            if "Saved the game" not in server.rcon("save-all flush"):
                raise DockerRuntimeError("save-all flush was not acknowledged")
            cleanup_ok = server.cleanup()
            if not cleanup_ok or _active_session_lock(restored.world_directory):
                raise DockerRuntimeError("runtime cleanup or world lock verification failed")
            tree = canonical_world_tree_identity(restored.world_directory)
            success = True
            return self._prepared(
                definition,
                restored,
                observation,
                jar_sha256,
                tree.manifest_sha256,
                source_revision,
            )
        finally:
            if not success:
                server.cleanup()
                shutil.rmtree(data_root, ignore_errors=True)

    def release(self, prepared: PreparedBaseline) -> None:
        world = prepared.source.cloned_world
        root = world.parent
        if world.name != "world" or not root.name.startswith("va-minecraft-acquire-"):
            raise DockerRuntimeError("refusing to release an unowned acquisition clone")
        shutil.rmtree(root)
        if root.exists():
            raise DockerRuntimeError("acquisition clone release could not be verified")

    def _probe(
        self, definition: BaselineDefinition, port: int, server: DockerServer
    ) -> dict[str, Any]:
        payload = {
            "initial": definition.initial_position.as_dict(),
            "position_convention": definition.position_convention,
            "blocks": [item.as_dict() for item in central_wall_v1.blocks],
            "opening": [item.as_dict() for item in central_wall_v1.opening],
            "targets": [
                {"variant_id": name, **target.as_dict(), "tolerance": 1.0, "position_convention": definition.position_convention}
                for name, target in definition.targets
            ],
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode("ascii")
        ).decode("ascii")
        try:
            process = subprocess.Popen(
                ["node", str(PROBE_PATH), "127.0.0.1", str(port), encoded],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise DockerRuntimeError(f"Mineflayer probe failed: {type(exc).__name__}") from exc
        try:
            ready = _readline_with_timeout(process, 35)
            if ready != "READY\n":
                raise DockerRuntimeError("Mineflayer probe did not become ready")
            for command in definition.preparation_commands:
                server.rcon(command.removeprefix("/"))
            if process.stdin is None:
                raise DockerRuntimeError("Mineflayer probe input is unavailable")
            process.stdin.write("OBSERVE\n")
            process.stdin.flush()
            stdout, _stderr = process.communicate(timeout=180)
        except (DockerRuntimeError, subprocess.SubprocessError, OSError) as exc:
            if process.poll() is None:
                process.kill()
                process.wait()
            if isinstance(exc, DockerRuntimeError):
                raise
            raise DockerRuntimeError(f"Mineflayer probe failed: {type(exc).__name__}") from exc
        if process.returncode != 0:
            raise DockerRuntimeError("Mineflayer probe failed")
        try:
            observed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise DockerRuntimeError("Mineflayer probe returned invalid evidence") from exc
        observed["commands_executed"] = len(definition.preparation_commands)
        observed["save_acknowledged"] = "Saved the game" in server.rcon("save-all flush")
        self._validate_probe(definition, observed)
        return observed

    @staticmethod
    def _validate_probe(definition: BaselineDefinition, observed: dict[str, Any]) -> None:
        if definition.position_convention != PositionConvention.ENTITY_FEET.value:
            raise DockerRuntimeError("baseline position convention is not entity_feet")
        if observed.get("state", {}).get("position_convention") != definition.position_convention:
            raise DockerRuntimeError("probe state position convention did not match")
        if observed.get("commands_executed") != len(definition.preparation_commands):
            raise DockerRuntimeError("probe did not execute every preparation command")
        if observed.get("save_acknowledged") is not True:
            raise DockerRuntimeError("probe save-all flush was not acknowledged")
        wall = central_wall_v1
        expected_blocks = wall.blocks
        expected_opening = wall.opening
        def expected_block(item: dict[str, Any]) -> str:
            return "minecraft:stone" if definition.obstacle_profile else "minecraft:air"

        mismatches = [
            item for item in observed.get("blocks", [])
            if item.get("observed") != expected_block(item)
        ]
        if mismatches:
            item = mismatches[0]
            raise DockerRuntimeError(
                "obstacle block mismatch at "
                f"{item.get('x')},{item.get('y')},{item.get('z')}: "
                f"expected {expected_block(item)}, observed {item.get('observed')}; "
                f"probe position {observed.get('state', {}).get('position')}"
            )
        if len(observed.get("blocks", [])) != len(expected_blocks):
            raise DockerRuntimeError("obstacle block evidence was incomplete")
        if any(item.get("observed") != "minecraft:air" for item in observed.get("opening", [])) or len(observed.get("opening", [])) != len(expected_opening):
            raise DockerRuntimeError("obstacle opening evidence did not match")
        probes = observed.get("probes")
        expected_targets = {
            name: target.as_dict() for name, target in definition.targets
        }
        probe_contract_valid = isinstance(probes, list) and len(probes) == 3 and all(
            item.get("reachable") is True
            and item.get("position_convention") == definition.position_convention
            and item.get("target", {}).get("position_convention") == definition.position_convention
            and item.get("variant_id") in expected_targets
            and item.get("support_block_type") not in {
                None,
                "minecraft:air",
                "minecraft:water",
                "minecraft:lava",
            }
            and item.get("support_block_collision_box") == "block"
            and item.get("support_block_shapes") == [[0, 0, 0, 1, 1, 1]]
            and item.get("falling") is False
            and all(
                item.get("target", {}).get(axis) == expected_targets[item["variant_id"]][axis]
                for axis in ("x", "y", "z")
            )
            and all(
                isinstance(item.get("delta", {}).get(axis), (int, float))
                and math.isfinite(item["delta"][axis])
                and item["delta"][axis] < item.get("target", {}).get("tolerance", 0)
                for axis in ("x", "y", "z")
            )
            for item in probes
        )
        if not probe_contract_valid:
            evidence = [
                {
                    "variant_id": item.get("variant_id"),
                    "delta": item.get("delta"),
                    "error": item.get("error"),
                }
                for item in probes
            ] if isinstance(probes, list) else []
            raise DockerRuntimeError(
                "canonical pathfinding evidence did not pass strict tolerance: "
                + json.dumps(evidence, sort_keys=True, separators=(",", ":"))
            )

    @staticmethod
    def _verify_server_settings(server: DockerServer) -> None:
        expected = {
            "difficulty": "The difficulty is Normal",
            "gamerule doDaylightCycle": "Gamerule doDaylightCycle is currently set to: false",
            "gamerule doWeatherCycle": "Gamerule doWeatherCycle is currently set to: false",
            "gamerule doMobSpawning": "Gamerule doMobSpawning is currently set to: false",
            "gamerule spawnRadius": "Gamerule spawnRadius is currently set to: 0",
        }
        for command, text in expected.items():
            response = server.rcon(command)
            if text not in response:
                raise DockerRuntimeError(
                    f"server state query {command!r} expected {text!r}, observed {response!r}"
                )

    @staticmethod
    def _prepared(
        definition: BaselineDefinition,
        restored: RestoredWorld,
        observed: dict[str, Any],
        jar_sha256: str,
        tree_sha256: str,
        source_revision: str,
    ) -> PreparedBaseline:
        state = observed["state"]
        if state.get("hostile_mob_count") != 0:
            raise DockerRuntimeError("hostile mob removal was not observed")
        position = state["position"]
        initial = BaselineInitialState(
            position=type(definition.initial_position)(position["x"], position["y"], position["z"]),
            yaw=float(state["yaw"]), pitch=float(state["pitch"]), dimension=state["dimension"], game_mode=state["game_mode"],
            inventory=InventoryState(slots=tuple((item["slot"], item["item"], item["count"]) for item in state["inventory"])),
            health=state["health"], hunger=state["hunger"], time=state["time"], weather=state["weather"],
            difficulty="normal", hostile_mobs_removed=True, hostile_mob_spawning=False,
        )
        probes = tuple(
            ReachabilityProbeResult(
                item["variant_id"], definition.target(item["variant_id"]), True,
                "pathfinder strict deltas x={x:.3f},y={y:.3f},z={z:.3f}".format(**item["delta"]),
                definition.position_convention,
            )
            for item in observed["probes"]
        )
        profile = definition.obstacle_profile
        return PreparedBaseline(
            runtime=RuntimeIdentity(ADAPTER_ID, "Vanilla 1.19.2 / Java 17", runtime_digest(jar_sha256), MINECRAFT_VERSION),
            source=SourceIdentity(
                source_id=f"{definition.baseline_id}-{tree_sha256[:12]}", cloned_world=restored.world_directory,
                cloned_from=f"approved-source-{SOURCE_ARCHIVE_SHA256[:12]}",
                clone_evidence="verified archive restored into fresh runtime data root",
                source_revision=source_revision, world_sha256=tree_sha256, process_state="stopped", active_locks=(),
            ),
            quiescence=QuiescenceEvidence(True, True, True, "stopped"),
            preparation_commands=definition.preparation_commands,
            observed_initial_state=initial,
            semantic_observation=SemanticObservation(profile.profile_id if profile else None, profile.changed_block_count if profile else 0),
            probes=probes,
            cleanup=CleanupEvidence(True, "container stopped and removed; no running process or active world lock"),
        )


class DockerMatrixExecutor:
    def __init__(self, identity: dict[str, Any], *, runner: Runner | None = None):
        self.matrix_identity = identity
        self.runner = runner or subprocess.run
        self.diagnostic_executor = BoundedDiagnosticExecutor(runner)

    def __call__(self, *, run: MatrixRunSpec, restored_world: RestoredWorld, output_dir: Path) -> Path:
        from benchmarks.minecraft.experiment import run_minecraft_experiment

        definition = BASELINE_DEFINITIONS.get(run.baseline_id)
        if definition is None or restored_world.descriptor.snapshot_id != run.baseline_id:
            raise DockerRuntimeError("matrix baseline identity is not canonical")
        if canonical_world_tree_identity(restored_world.world_directory) != restored_world.tree_identity:
            raise DockerRuntimeError("independently restored world tree changed before startup")
        server = DockerServer(
            restored_world.world_directory.parent,
            runner=self.runner,
            diagnostic_executor=self.diagnostic_executor,
        )
        attempt_started = False
        try:
            port = server.create_start()
            jar = restored_world.world_directory.parent / SERVER_JAR_PATH
            if not jar.is_file() or _sha(jar, "sha1") != SERVER_JAR_SHA1:
                raise DockerRuntimeError("matrix server JAR failed official identity verification")
            expected_digest = self.matrix_identity["runtime"]["digest"].removeprefix("sha256:")
            if runtime_digest(_sha(jar)) != expected_digest:
                raise DockerRuntimeError("matrix runtime composite digest does not match the premanifest")
            port = server.restart_and_verify_marker(run.baseline_id)
            config = self._config(run, port, restored_world)
            config_path = output_dir / "matrix_launch_config.json"
            config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            experiment_dir = output_dir / "bundle"
            attempt_started = True
            summary = _run_with_experiment_manifest(
                experiment_dir,
                lambda: run_minecraft_experiment(
                    config_path=config_path,
                    output_root=experiment_dir,
                    run_name=run.run_id,
                    execute=True,
                    execute_timeout_seconds=self.matrix_identity["generation"]["timeout_seconds"],
                    task_selection_policy="dual-dag",
                    command_text="minecraft finalized matrix executor",
                ),
            )
            if summary.get("error") is not None:
                raise DockerRuntimeError("judged Minecraft experiment failed")
            result = Path(summary["output_dir"])
        except BaseException as exc:
            failure_detail = getattr(exc, "failure_detail", None)
            if not isinstance(failure_detail, dict):
                failure_detail = {
                    "reason": "matrix_runtime_error",
                    "runtime_diagnostics": {"error_type": type(exc).__name__},
                }
                setattr(exc, "failure_detail", failure_detail)
            try:
                cleanup_ok = server.cleanup()
            except Exception as cleanup_exc:
                diagnostics = dict(failure_detail.get("runtime_diagnostics", {}))
                diagnostics["cleanup_error_type"] = type(cleanup_exc).__name__
                error = DockerRuntimeError(
                    "matrix server cleanup could not be verified",
                    diagnostics=diagnostics,
                )
                error.failure_detail.update({
                    "attempt_started": attempt_started,
                    "cleanup": {"attempted": True, "passed": False},
                })
                raise error from cleanup_exc
            if cleanup_ok:
                failure_detail.update({
                    "attempt_started": attempt_started,
                    "cleanup": {"attempted": True, "passed": True},
                })
                raise
            error = DockerRuntimeError(
                "matrix server cleanup could not be verified",
                diagnostics=dict(failure_detail.get("runtime_diagnostics", {})),
            )
            error.failure_detail.update({
                "attempt_started": attempt_started,
                "cleanup": {"attempted": True, "passed": cleanup_ok},
            })
            raise error from exc
        if not server.cleanup():
            raise DockerRuntimeError("matrix server cleanup could not be verified")
        return result

    def _config(self, run: MatrixRunSpec, port: int, _restored: RestoredWorld) -> dict[str, Any]:
        from benchmarks.minecraft.experiment import DEFAULT_JUDGED_REQUIRED_ARTIFACTS

        model = self.matrix_identity["model"]
        generation = self.matrix_identity["generation"]
        api_base = os.environ.get("VILLAGER_MINECRAFT_MODEL_API_BASE")
        key_env = os.environ.get("VILLAGER_MINECRAFT_MODEL_API_KEY_ENV")
        if not api_base or not key_env or not os.environ.get(key_env):
            raise DockerRuntimeError("matrix model endpoint or credential environment is unavailable")
        if run.position_convention != PositionConvention.ENTITY_FEET.value:
            raise DockerRuntimeError("matrix run position convention must be entity_feet")
        return {
            "task_type": "meta", "task_idx": 0, "agent_num": 1, "task_goal": run.prompt,
            "task_scenario": "move", "host": "127.0.0.1", "port": port, "task_name": run.run_id,
            "evaluation_arg": {"target": "", **run.evaluation_target.as_dict(), "position_convention": run.position_convention, "initial_state": {**run.initial_state.as_dict(), "position_convention": run.position_convention}, "facing": "", "item_position": "inventory", "tool": "", "action": "", "step": 1, "other_arg": []},
            "world_id": run.baseline_id, "world_snapshot_path": run.snapshot_path,
            "world_snapshot_sha256": run.snapshot_sha256, "server_version": MINECRAFT_VERSION,
            "world_initialization": PRESERVE_RESTORED_SNAPSHOT,
            "position_convention": run.position_convention,
            "server_protocol": "760", "api_model": model["name"], "api_base": api_base,
            "api_key_env": key_env, "model_digest": model["digest"],
            "max_task_num": 1,
            "generation": generation,
            "required_artifacts": list(DEFAULT_JUDGED_REQUIRED_ARTIFACTS),
            "seed_contract": {"seed": run.seed, "requested_scopes": list(run.seed_scopes.requested)},
        }


def register_builtin_runtimes(
    *, acquisition: bool = False, matrix_premanifest: str | Path | None = None
) -> DockerMatrixExecutor | None:
    matrix_executor = None
    if acquisition:
        from benchmarks.minecraft.snapshot_acquisition import register_acquisition_runtime

        register_acquisition_runtime(ADAPTER_ID, DockerAcquisitionRuntime())
    if matrix_premanifest is not None:
        from benchmarks.minecraft.matrix import RUNTIME_ADAPTERS

        spec = load_matrix_spec(matrix_premanifest)
        expected_runtime = pinned_runtime_identity()
        if vars(spec.runtime) != expected_runtime:
            raise DockerRuntimeError(
                "finalized premanifest runtime identity does not match the pinned adapter"
            )
        if spec.model.provider != "ollama":
            raise DockerRuntimeError("the judged Minecraft runtime currently requires an Ollama-compatible model")
        for field, env_name in (("provider", "VILLAGER_MINECRAFT_MODEL_PROVIDER"), ("name", "VILLAGER_MINECRAFT_MODEL_NAME"), ("digest", "VILLAGER_MINECRAFT_MODEL_DIGEST")):
            if os.environ.get(env_name) != getattr(spec.model, field):
                raise DockerRuntimeError("configured matrix model identity does not match the premanifest")
        matrix_executor = DockerMatrixExecutor({
            "runtime": vars(spec.runtime), "model": vars(spec.model), "generation": vars(spec.generation),
        })
        RUNTIME_ADAPTERS[ADAPTER_ID] = matrix_executor
    return matrix_executor


def _active_session_lock(world: Path) -> bool:
    lock = world / "session.lock"
    if not lock.exists():
        return False
    try:
        with lock.open("rb") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        return False
    except (OSError, BlockingIOError):
        return True


def _offline_player_uuid(name: str) -> str:
    digest = bytearray(hashlib.md5(f"OfflinePlayer:{name}".encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def _write_probe_operator(data_root: Path) -> None:
    payload = [
        {
            "uuid": _offline_player_uuid(name),
            "name": name,
            "level": 4,
            "bypassesPlayerLimit": True,
        }
        for name in ("VAProbe", "meta_judger")
    ]
    path = data_root / "ops.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _readline_with_timeout(process: subprocess.Popen[str], timeout: float) -> str:
    if process.stdout is None:
        raise DockerRuntimeError("Mineflayer probe output is unavailable")
    result: list[str] = []
    thread = threading.Thread(target=lambda: result.append(process.stdout.readline()), daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        process.kill()
        process.wait()
        raise DockerRuntimeError("Mineflayer probe readiness timed out")
    return result[0] if result else ""


def _git_revision(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DockerRuntimeError("repository source revision is unavailable") from exc
    revision = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise DockerRuntimeError("repository source revision is invalid")
    return revision
