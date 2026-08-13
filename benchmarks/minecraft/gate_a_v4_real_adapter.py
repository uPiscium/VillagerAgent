"""Fixed-context local adapter for the single authorized Gate A v4 canary."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

FIXED_EXECUTION_ROOT = Path("/home/upiscium/Documents/Research/VillagerAgent/.worktrees/issue-506-execution-2511366")
FIXED_PREMANIFEST = Path("/tmp/opencode/issue-506-v4-25113661-private/premanifest.json")
FIXED_PRIVATE_PARENT = Path("/tmp/opencode")
FIXED_NAMESPACE = ".villageragent.minecraft-judged-production-v4.gate-a.diagonal-s17-baseline_open"
FIXED_REVISION = "25113661a6b09761ab47a05bd70bd8f0386e2b67"
FIXED_CHILD_MANIFEST = "ce8c30e13ddef9251d64a3f833625e509dd9590b163229f52fe585444794ae5d"
FIXED_PREMANIFEST_BYTES = "222afe434cace4e7609cddaae578284ba1d2a1b1ed0dd927a4a6155ade71192f"
FIXED_PREMANIFEST_CANONICAL = "bffaedbe296d18b1c4d0cd7bc0d073d011566e3415133d464e65492bb9c13f3a"
FIXED_RUNTIME_DIGEST = "sha256:25441b6e08ce2eff2a71dd6330ff4ddfaa6e5c9f1aa89e508e2580a16b262e0f"
FIXED_MODEL_DIGEST = "4eb23ef187e2c5462566d6a1d3bbbc2f1346d0b4327cbb66d58fffbcc9b2b05c"
FIXED_DOCKER_CONTRACT = "ebf181d73d28e24ec8d257d06f3107d1f25211bfb26dedfd999507870fb41d01"
FIXED_DOCKER_EXECUTABLE = Path("/nix/store/3a2hvsxqwqz9zfi44jhpi58172gdgf8p-docker-29.6.2/bin/docker")
FIXED_MODEL_API_BASE = "http://10.255.255.5:11434"
FIXED_MODEL_PROVIDER = "ollama"
FIXED_MODEL_NAME = "gemma4:12b"
FIXED_RUN_ID = "diagonal-s17-baseline_open"
EXPECTED_ORIGINS = MappingProxyType({
    "env.runtime_execution": "env/runtime_execution.py",
    "benchmarks.minecraft.docker_runtime": "benchmarks/minecraft/docker_runtime.py",
    "benchmarks.minecraft.experiment": "benchmarks/minecraft/experiment.py",
    "benchmarks.minecraft.matrix_spec": "benchmarks/minecraft/matrix_spec.py",
    "benchmarks.minecraft.matrix_validation": "benchmarks/minecraft/matrix_validation.py",
    "benchmarks.minecraft.world_snapshot": "benchmarks/minecraft/world_snapshot.py",
})
EXECUTOR_IMPLEMENTATION_SHA256 = "9376d78badba0716ca02c5cae80eeca4fbd5e3be2c4bbd2502aa961351b7a1b7"
VALIDATION_IMPLEMENTATION_SHA256 = "6d5b1e089c69a365a90aa04531e06eae323b716a83faa5c1bea0b732293a4764"
FIXED_ORIGINS = MappingProxyType({k.rsplit(".", 1)[-1]: v for k, v in EXPECTED_ORIGINS.items()})


class RealAdapterError(RuntimeError):
    pass


class GateARunCompositionAuthority:
    """Nominal host authority to compose the fixed outer run envelope."""
    def __new__(cls, *args, **kwargs):
        del args, kwargs
        raise TypeError("operator capability minting is unavailable")

    def __init__(self, *args, **kwargs):
        del args, kwargs
        raise TypeError("operator capability minting is unavailable")

    def consume(self):
        raise TypeError("operator capability minting is unavailable")


@dataclass(frozen=True)
class RuntimeModules:
    runtime_execution: Any
    docker_runtime: Any
    experiment: Any
    matrix_spec: Any
    matrix_validation: Any
    world_snapshot: Any
    origins: MappingProxyType = FIXED_ORIGINS

    def verify(self):
        if self.origins != FIXED_ORIGINS:
            raise RealAdapterError("runtime origins rejected")
        loader_identity = None
        for name, relative in EXPECTED_ORIGINS.items():
            module = getattr(self, name.rsplit(".", 1)[-1])
            if Path(module.__file__) != FIXED_EXECUTION_ROOT / relative:
                raise RealAdapterError("fixed module origin rejected")
            loader = getattr(module, "__loader__", None)
            if loader is None or getattr(getattr(module, "__spec__", None), "loader", None) is not loader:
                raise RealAdapterError("fixed module loader rejected")
            if loader_identity is None:
                loader_identity = loader
            elif loader is not loader_identity:
                raise RealAdapterError("fixed module loader rejected")
            source_digest = getattr(loader, "authenticated_source_sha256", lambda unused: None)(name)
            if source_digest != _sha256(FIXED_EXECUTION_ROOT / relative):
                raise RealAdapterError("fixed module source rejected")
        fixed_hashes = {
            "benchmarks/minecraft/docker_runtime.py": EXECUTOR_IMPLEMENTATION_SHA256,
            "benchmarks/minecraft/matrix_validation.py": VALIDATION_IMPLEMENTATION_SHA256,
        }
        if any(_sha256(FIXED_EXECUTION_ROOT / relative) != digest
               for relative, digest in fixed_hashes.items()):
            raise RealAdapterError("fixed implementation hash rejected")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping(value) -> dict:
    try:
        return vars(value)
    except TypeError:
        raise RealAdapterError("fixed context rejected") from None


def _check_model_environment() -> None:
    expected = {
        "VILLAGER_MINECRAFT_MODEL_API_BASE": FIXED_MODEL_API_BASE,
        "VILLAGER_MINECRAFT_MODEL_PROVIDER": FIXED_MODEL_PROVIDER,
        "VILLAGER_MINECRAFT_MODEL_NAME": FIXED_MODEL_NAME,
        "VILLAGER_MINECRAFT_MODEL_DIGEST": FIXED_MODEL_DIGEST,
    }
    if any(os.environ.get(key) != value for key, value in expected.items()):
        raise RealAdapterError("fixed model environment rejected")


class FixedGateAAdapter:
    def __init__(self, authorization: GateARunCompositionAuthority, modules: RuntimeModules):
        raise TypeError("adapter construction requires consumed operator capability")

    def _initialize(self, authorization, modules):
        if type(authorization) is not GateARunCompositionAuthority or not isinstance(modules, RuntimeModules):
            raise TypeError("strict consumed capability and runtime modules required")
        modules.verify()
        if _sha256(FIXED_PREMANIFEST) != FIXED_PREMANIFEST_BYTES:
            raise RealAdapterError("fixed premanifest rejected")
        _check_model_environment()
        execution = modules.runtime_execution.RuntimeExecution.resolve(FIXED_EXECUTION_ROOT)
        execution.verify()
        if execution.manifest_sha256 != FIXED_CHILD_MANIFEST or len(execution.assets) != 125:
            raise RealAdapterError("fixed child runtime rejected")
        spec = modules.matrix_spec.load_matrix_spec(FIXED_PREMANIFEST, repo_root=FIXED_EXECUTION_ROOT)
        selected = tuple(run for run in spec.runs if run.run_id == FIXED_RUN_ID)
        if (spec.revision != FIXED_REVISION or spec.lifecycle_state != "finalized"
                or spec.premanifest_sha256 != FIXED_PREMANIFEST_CANONICAL
                or _mapping(spec.runtime).get("digest") != FIXED_RUNTIME_DIGEST
                or _mapping(spec.model).get("digest") != FIXED_MODEL_DIGEST or len(selected) != 1):
            raise RealAdapterError("fixed canary context rejected")
        self._modules, self._lifecycle, self._supervisor_module = modules, None, None
        self._docker_environment = None
        self._execution, self._spec, self._run = execution, spec, selected[0]
        self._states = {}

    def attach_host(self, lifecycle, supervisor_module, docker_contract):
        self._lifecycle, self._supervisor_module = lifecycle, supervisor_module
        if _sha256(Path(docker_contract.__file__)) != FIXED_DOCKER_CONTRACT:
            raise RealAdapterError("fixed Docker contract rejected")
        self._docker_environment = docker_contract.bind_environment()
        return self

    def _root(self, handle) -> Path:
        self._lifecycle.validate_owned_run(handle)
        expected = FIXED_PRIVATE_PARENT / FIXED_NAMESPACE
        descriptor, observed = os.fstat(handle.namespace_fd), os.stat(expected, follow_symlinks=False)
        if not stat.S_ISDIR(observed.st_mode) or (descriptor.st_dev, descriptor.st_ino, descriptor.st_uid) != (observed.st_dev, observed.st_ino, observed.st_uid):
            raise RealAdapterError("owned data root rejected")
        return expected

    def owned_data_root(self, handle): return self._root(handle)

    def docker_runner_factory(self, handle, children):
        key = id(handle)
        if key in self._states: raise RealAdapterError("fixed adapter reused")
        supervisor = self._supervisor_module.OwnedChildSupervisor(children, FIXED_DOCKER_EXECUTABLE, self._docker_environment)
        self._states[key] = {"supervisor": supervisor, "children": children, "phase": "bound"}
        return supervisor.docker_runner

    def restore_baseline_open_once(self, handle):
        state = self._states.get(id(handle))
        if state is None or state["phase"] != "bound": raise RealAdapterError("fixed restore order rejected")
        self._lifecycle.validate_owned_run(handle)
        try: os.mkdir("work", mode=0o700, dir_fd=handle.namespace_fd)
        except OSError: raise RealAdapterError("owned work destination rejected") from None
        run = self._run
        descriptor = self._modules.world_snapshot.WorldSnapshotDescriptor(run.baseline_id, FIXED_EXECUTION_ROOT / run.snapshot_path, run.snapshot_sha256)
        restored = self._modules.world_snapshot.restore_world_snapshot(descriptor, self._root(handle) / "work" / "world")
        state["phase"] = "restored"
        return restored

    def execute_diagonal_s17_once(self, handle, restored, managed_runner, children):
        state = self._states.get(id(handle))
        if state is None or state["phase"] != "restored" or state["children"] is not children or not callable(managed_runner): raise RealAdapterError("fixed execution order rejected")
        self._lifecycle.validate_owned_run(handle)
        try: os.mkdir("output", mode=0o700, dir_fd=handle.namespace_fd)
        except OSError: raise RealAdapterError("owned output destination rejected") from None
        _check_model_environment()
        identity = {"runtime": _mapping(self._spec.runtime), "model": _mapping(self._spec.model), "generation": _mapping(self._spec.generation)}
        executor = self._modules.docker_runtime.DockerMatrixExecutor(identity, runner=managed_runner, execution_root=self._execution)
        state["phase"] = "attempted"
        with state["supervisor"].runtime_hook(self._modules.experiment):
            bundle = executor(run=self._run, restored_world=restored, output_dir=self._root(handle) / "output")
        state["phase"], state["bundle"] = "executed", bundle
        return bundle

    def validate_diagonal_s17_once(self, handle, bundle):
        state = self._states.get(id(handle))
        if state is None or state["phase"] != "executed": raise RealAdapterError("fixed validation order rejected")
        run = self._run
        result = self._modules.matrix_validation.validate_matrix_run(bundle, tolerance=run.target_tolerance, expected_target=run.evaluation_target.as_dict(), expected_completion_policy=run.expected_completion_policy, expected_completion_semantics=run.expected_completion_semantics, expected_position_convention=run.position_convention, expected_seed_contract={"seed": run.seed, "requested_scopes": list(run.seed_scopes.requested)})
        if not isinstance(result, dict) or result.get("passed") is not True: raise RealAdapterError("fixed validation rejected")
        state["phase"], state["validation"] = "validated", result
        return True

    def cleanup_children(self, children):
        state = next((x for x in self._states.values() if x["children"] is children), None)
        if state is not None: state["supervisor"].cleanup()

    def invalidate_runtime_result(self, handle):
        self._root(handle)
        for name in ("runtime-result.json.tmp", "runtime-result.json"):
            try: os.unlink(name, dir_fd=handle.namespace_fd)
            except FileNotFoundError: pass
            except OSError: raise RealAdapterError("runtime result invalidation rejected") from None
        os.fsync(handle.namespace_fd)

    def finalize_runtime_result(self, handle, outcome):
        if outcome not in {"success", "failed_quarantined", "cleanup_blocked_quarantined"}: raise RealAdapterError("runtime result outcome rejected")
        self._root(handle)
        encoded = (json.dumps({"schema_version": 1, "run_id": FIXED_RUN_ID, "outcome": outcome, "reusable": False}, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
        try:
            descriptor = os.open("runtime-result.json.tmp", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=handle.namespace_fd)
            with os.fdopen(descriptor, "wb") as stream: stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
            os.rename("runtime-result.json.tmp", "runtime-result.json", src_dir_fd=handle.namespace_fd, dst_dir_fd=handle.namespace_fd); os.fsync(handle.namespace_fd)
        except OSError: raise RealAdapterError("runtime result finalization rejected") from None

    def postflight(self, handle, docker_proof, children):
        self._root(handle)
        if getattr(docker_proof, "managed_containers", None) != 0 or children.count() != 0: raise RealAdapterError("fixed postflight rejected")
        return {"managed_containers": 0, "run_owned_children": 0, "runtime_result_reusable": False}


def bind_fixed_adapter(authorization: GateARunCompositionAuthority, modules: RuntimeModules, lifecycle=None, supervisor_module=None, docker_contract=None) -> FixedGateAAdapter:
    if type(authorization) is not GateARunCompositionAuthority: raise TypeError("run composition authority required")
    authorization.consume()
    adapter = object.__new__(FixedGateAAdapter)
    adapter._initialize(authorization, modules)
    if lifecycle is not None: adapter.attach_host(lifecycle, supervisor_module, docker_contract)
    return adapter
