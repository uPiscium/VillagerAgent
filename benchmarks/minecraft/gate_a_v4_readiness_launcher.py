"""Authenticated read-only readiness launcher for the fixed Gate A adapter."""
from __future__ import annotations

import sys

if __name__ == "__main__" and (not sys.flags.isolated or not sys.dont_write_bytecode):
    sys.stdout.write('{"attempts":0,"execution_flags":{"canary":false,"five_run":false,"matrix":false,"production":false},"reason_code":"authentication_failed","status":"readiness_failed"}\n')
    raise SystemExit(3)

import hashlib
import importlib
import importlib.abc
import importlib.util
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path
from types import ModuleType


COMPONENT_SHA256 = {
    "admission": "7974a40c718b8a1dda84c8a2820a4275b098a532124f46aa4312f7b71ca6676a",
    "lifecycle": "90154c72d364e5d11f4f4a6bcd905db66d79ea66414665ca6a664591960b31a3",
    "managed_docker": "cab36f5435aba9eb1dbf9a9ddfcc6ced1c4a10b50c1215883fdc4d7202e38e4f",
    "coordinator": "490c16b87ff213675ee675c4fcf03ee4b7e25775c9a9f47224ccd48cf2289764",
    "child_supervisor": "23494707b03778e955990c1b5b7ee54e8c51b2e6bc2003815c82c2a4507e289e",
    "child_bootstrap": "4118c53453fbffdcdf9537266f2472dec307ba276e52323b483ec81d2c38c747",
    "real_adapter": "5289d5aa5ee969e45873b20965b0c5b93b5a32944c64f270524ae966437eb5d9",
    "docker_contract": "ebf181d73d28e24ec8d257d06f3107d1f25211bfb26dedfd999507870fb41d01",
}
COMPONENT_FILES = {
    "admission": "gate_a_v4_owned_admission.py",
    "lifecycle": "gate_a_v4_owned_lifecycle.py",
    "managed_docker": "gate_a_v4_managed_docker.py",
    "coordinator": "gate_a_v4_owned_execution.py",
    "child_supervisor": "gate_a_v4_child_supervisor.py",
    "child_bootstrap": "gate_a_v4_child_supervisor_bootstrap.py",
    "real_adapter": "gate_a_v4_real_adapter.py",
    "docker_contract": "gate_a_v4_docker_contract.py",
}
EXECUTION_ROOT = Path(
    "/home/upiscium/Documents/Research/VillagerAgent/.worktrees/issue-506-execution-2511366"
)
PREMANIFEST = Path("/tmp/opencode/issue-506-v4-25113661-private/premanifest.json")
PRIVATE_PARENT = Path("/tmp/opencode")
DOCKER_EXECUTABLE = Path(
    "/nix/store/3a2hvsxqwqz9zfi44jhpi58172gdgf8p-docker-29.6.2/bin/docker"
)
EXPECTED_FIXED_ORIGINS = {
    "env.runtime_execution": "env/runtime_execution.py",
    "benchmarks.minecraft.docker_runtime": "benchmarks/minecraft/docker_runtime.py",
    "benchmarks.minecraft.experiment": "benchmarks/minecraft/experiment.py",
    "benchmarks.minecraft.matrix_spec": "benchmarks/minecraft/matrix_spec.py",
    "benchmarks.minecraft.matrix_validation": "benchmarks/minecraft/matrix_validation.py",
    "benchmarks.minecraft.world_snapshot": "benchmarks/minecraft/world_snapshot.py",
}
FIXED_CRITICAL_SHA256 = {
    "benchmarks/minecraft/docker_runtime.py": "9376d78badba0716ca02c5cae80eeca4fbd5e3be2c4bbd2502aa961351b7a1b7",
    "benchmarks/minecraft/matrix_validation.py": "6d5b1e089c69a365a90aa04531e06eae323b716a83faa5c1bea0b732293a4764",
}
FIXED_GIT = "/nix/store/c0277k5giric1mn9dklllavbzvxl6hzb-git-2.53.0/bin/git"
FIXED_REVISION = "25113661a6b09761ab47a05bd70bd8f0386e2b67"
_FIXED_SOURCE_PREFIXES = (
    "env/", "pipeline/", "model/", "type_define/", "rl_env/",
    "benchmarks/common/", "benchmarks/minecraft/",
)


class ReadinessError(RuntimeError):
    pass


def _load_authenticated(path: Path, digest: str, name: str):
    try:
        source = path.read_bytes()
    except OSError:
        raise ReadinessError("component_authentication_failed") from None
    if hashlib.sha256(source).hexdigest() != digest or name in sys.modules:
        raise ReadinessError("component_authentication_failed")
    module = ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        exec(compile(source, str(path), "exec"), module.__dict__, module.__dict__)
    except Exception:
        sys.modules.pop(name, None)
        raise ReadinessError("component_authentication_failed") from None
    return module


def _load_components(root: Path):
    loaded = {}
    try:
        for key, filename in COMPONENT_FILES.items():
            loaded[key] = _load_authenticated(
                root / filename, COMPONENT_SHA256[key], f"authenticated_issue507_{key}",
            )
        return loaded
    except BaseException:
        for key in loaded:
            sys.modules.pop(f"authenticated_issue507_{key}", None)
        raise


def _git_environment():
    return {
        "PATH": "/nonexistent", "HOME": "/nonexistent/va-gate-a-git-home", "LC_ALL": "C",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "core.fsmonitor",
        "GIT_CONFIG_VALUE_0": "false", "GIT_CONFIG_KEY_1": "core.hooksPath",
        "GIT_CONFIG_VALUE_1": "/dev/null",
    }


def _git(*arguments, check=True, text=True):
    return subprocess.run(
        [FIXED_GIT, *arguments], cwd=EXECUTION_ROOT, env=_git_environment(),
        stdin=subprocess.DEVNULL, capture_output=True, text=text, timeout=30, check=check,
    )


def _authenticate_fixed_checkout():
    try:
        head = _git("rev-parse", "HEAD").stdout.strip()
        status = _git("status", "--porcelain", "--untracked-files=all").stdout
        branch = _git("symbolic-ref", "-q", "HEAD", check=False)
    except (OSError, subprocess.SubprocessError):
        raise ReadinessError("fixed_checkout_authentication_failed") from None
    if head != FIXED_REVISION or status or branch.returncode == 0:
        raise ReadinessError("fixed_checkout_authentication_failed")
    for relative, expected in FIXED_CRITICAL_SHA256.items():
        try:
            observed = hashlib.sha256((EXECUTION_ROOT / relative).read_bytes()).hexdigest()
        except OSError:
            raise ReadinessError("fixed_checkout_authentication_failed") from None
        if observed != expected:
            raise ReadinessError("fixed_checkout_authentication_failed")


def _fixed_source_snapshot():
    """Read importable project Python from fixed Git blobs, never worktree source."""
    try:
        names = _git("ls-tree", "-r", "--name-only", "-z", FIXED_REVISION, text=False).stdout
        selected = [
            raw.decode("utf-8") for raw in names.split(b"\0") if raw and raw.endswith(b".py")
            and (
                b"/" not in raw or raw == b"benchmarks/__init__.py"
                or any(raw.decode("utf-8").startswith(prefix) for prefix in _FIXED_SOURCE_PREFIXES)
            )
        ]
        archive = _git("archive", "--format=tar", FIXED_REVISION, "--", *selected, text=False).stdout
    except (OSError, UnicodeDecodeError, subprocess.SubprocessError):
        raise ReadinessError("fixed_source_authentication_failed") from None
    if not selected or len(archive) > 32 * 1024 * 1024:
        raise ReadinessError("fixed_source_authentication_failed")
    sources = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
            for member in stream.getmembers():
                if not member.isfile() or member.name not in selected or member.size > 2 * 1024 * 1024:
                    continue
                extracted = stream.extractfile(member)
                if extracted is None:
                    raise ReadinessError("fixed_source_authentication_failed")
                sources[member.name] = extracted.read()
    except (OSError, tarfile.TarError):
        raise ReadinessError("fixed_source_authentication_failed") from None
    if set(sources) != set(selected):
        raise ReadinessError("fixed_source_authentication_failed")
    return sources


class _SnapshotSourceLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, sources):
        self._modules = {}
        for relative, source in sources.items():
            parts = relative.removesuffix(".py").split("/")
            package = parts[-1] == "__init__"
            if package:
                parts.pop()
            name = ".".join(parts)
            self._modules[name] = (source, relative, package)

    @property
    def module_names(self):
        return frozenset(self._modules)

    def authenticated_source_sha256(self, fullname):
        record = self._modules.get(fullname)
        return hashlib.sha256(record[0]).hexdigest() if record is not None else None

    def find_spec(self, fullname, path=None, target=None):
        record = self._modules.get(fullname)
        if record is None:
            return None
        return importlib.util.spec_from_loader(fullname, self, is_package=record[2])

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        source, relative, package = self._modules[module.__name__]
        filename = str(EXECUTION_ROOT / relative)
        module.__file__ = filename
        if package:
            module.__path__ = [str(Path(filename).parent)]
        exec(compile(source, filename, "exec"), module.__dict__, module.__dict__)


def _load_fixed_runtime():
    _authenticate_fixed_checkout()
    source_loader = _SnapshotSourceLoader(_fixed_source_snapshot())
    if source_loader.module_names.intersection(sys.modules):
        raise ReadinessError("fixed_import_origin_failed")
    sys.meta_path.insert(0, source_loader)
    if str(EXECUTION_ROOT) in sys.path:
        raise ReadinessError("fixed_import_origin_failed")
    sys.path.insert(0, str(EXECUTION_ROOT))
    modules = {}
    for name, relative in EXPECTED_FIXED_ORIGINS.items():
        module = importlib.import_module(name)
        if Path(module.__file__) != EXECUTION_ROOT / relative or module.__loader__ is not source_loader:
            raise ReadinessError("fixed_import_origin_failed")
        modules[name] = module
    return modules


def _readiness_record():
    if len(sys.argv) != 1:
        raise ReadinessError("arguments_rejected")
    component_root = Path(__file__).resolve(strict=True).parent
    components = _load_components(component_root)
    fixed = _load_fixed_runtime()
    admission = components["admission"]
    host_bindings = admission._host_bindings(
        EXECUTION_ROOT, PREMANIFEST,
        component_root / COMPONENT_FILES["docker_contract"], DOCKER_EXECUTABLE,
    )
    observed = admission.diagnostic_admission(
        admission.owned_paths(PRIVATE_PARENT), host_bindings,
    )
    status = 0 if observed.get("status") == "admission_passed" else 3
    if status:
        return status, {"status": "readiness_failed", "reason_code": observed.get("reason_code", "unexpected_failure")}
    record = {
        "status": "ready_for_gate_a", "admission": "passed",
        "component_identity": "match", "execution_revision": "match",
        "runtime_identity": "match", "premanifest_identity": "match",
        "model_inventory": "match", "docker_identity": "match",
        "managed_containers": 0, "run_owned_children": 0, "attempts": 0,
        "execution_authority": False,
    }
    return status, record


def main() -> int:
    try:
        status, record = _readiness_record()
        print(json.dumps(record, sort_keys=True, separators=(",", ":")))
        return status
    except Exception as exc:
        reason = exc.args[0] if isinstance(exc, ReadinessError) and exc.args else "unexpected_failure"
        print(json.dumps({
            "status": "readiness_failed", "reason_code": reason,
            "attempts": 0, "judged_attempts": 0,
            "execution_flags": {
                "canary": False, "five_run": False, "matrix": False, "production": False,
            },
        }, sort_keys=True, separators=(",", ":")))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
