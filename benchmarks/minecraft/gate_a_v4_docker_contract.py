"""Read-only, fail-closed admission contract for the rootless Docker daemon.

This boundary deliberately uses only the standard library.  In particular, it
does not use the caller's Docker configuration or expose any endpoint, path, or
daemon identifier in its public result.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, MutableMapping


DOCKER_VERSION = "29.6.2"
DAEMON_ID_SHA256 = "c2e450f1fd794b8144b53be5c4be1777bafdc719a18ad749c03a0a93d122d31f"
DOCKER_EXECUTABLE_SHA256 = "f1dcc6a66f2b9d022d17ecb9e9e9939ef1e9062ae4f292e434ed90f6b43d431b"
DOCKER_CONFIG = "/nonexistent/va-minecraft-docker-config"
OUTPUT_LIMIT = 65536
TRUSTED_STORE = "/nix/store/"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class DockerContractError(ValueError):
    """A deliberately non-sensitive contract failure."""


@dataclass(frozen=True)
class DockerIdentity:
    """Private comparison material; do not serialize this object publicly."""

    daemon_sha256: str
    executable_sha256: str
    client_version: str
    server_version: str
    context: str
    platform: str
    socket_device: int
    socket_inode: int
    executable_device: int
    executable_inode: int


@dataclass(frozen=True)
class DockerContractResult:
    identity: DockerIdentity
    report: Mapping[str, Any]


def endpoint(euid: int | None = None) -> str:
    return f"unix:///run/user/{os.geteuid() if euid is None else euid}/docker.sock"


def _fail(reason: str = "docker_contract_rejected") -> DockerContractError:
    return DockerContractError(reason)


def _socket_identity(path: str, uid: int) -> tuple[int, int]:
    try:
        link = os.lstat(path)
        info = os.stat(path, follow_symlinks=False)
    except (OSError, ValueError) as exc:
        raise _fail("docker_socket_rejected") from exc
    if stat.S_ISLNK(link.st_mode) or not stat.S_ISSOCK(info.st_mode):
        raise _fail("docker_socket_rejected")
    if info.st_uid != uid or info.st_mode & 0o600 != 0o600 or info.st_mode & 0o007:
        raise _fail("docker_socket_rejected")
    return info.st_dev, info.st_ino


def validate_environment(environment: Mapping[str, str] | None = None) -> str:
    env = os.environ if environment is None else environment
    expected = endpoint()
    for key, value in env.items():
        if key.startswith("DOCKER_") and (key != "DOCKER_HOST" or value != expected):
            raise _fail("docker_environment_rejected")
    return expected


def bind_environment(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a Docker-only environment without disturbing model credentials."""
    env = dict(os.environ if environment is None else environment)
    validate_environment(env)
    for key in tuple(env):
        if key.startswith("DOCKER_"):
            del env[key]
    env["DOCKER_HOST"] = endpoint()
    env["DOCKER_CONFIG"] = DOCKER_CONFIG
    return env


def install_environment(environment: MutableMapping[str, str] | None = None) -> dict[str, str]:
    target = os.environ if environment is None else environment
    bound = bind_environment(target)
    for key in tuple(target):
        if key.startswith("DOCKER_"):
            del target[key]
    target["DOCKER_HOST"] = bound["DOCKER_HOST"]
    target["DOCKER_CONFIG"] = bound["DOCKER_CONFIG"]
    return dict(target)


def _bound_environment(environment: Mapping[str, str]) -> dict[str, str]:
    docker = {key: value for key, value in environment.items() if key.startswith("DOCKER_")}
    if docker != {"DOCKER_HOST": endpoint(), "DOCKER_CONFIG": DOCKER_CONFIG}:
        raise _fail("docker_environment_rejected")
    return dict(environment)


def _executable_identity(executable: Path) -> tuple[str, int, int]:
    if not isinstance(executable, Path) or not executable.is_absolute() or executable != Path(os.path.normpath(str(executable))):
        raise _fail("docker_executable_rejected")
    try:
        link = os.lstat(executable)
        resolved = executable.resolve(strict=True)
        if (not str(executable).startswith(TRUSTED_STORE) or not str(resolved).startswith(TRUSTED_STORE)
                or link.st_uid != 0 or (not stat.S_ISLNK(link.st_mode) and link.st_mode & 0o022)):
            raise _fail("docker_executable_rejected")
        info = os.stat(resolved, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o222:
            raise _fail("docker_executable_rejected")
        digest = hashlib.sha256()
        with resolved.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except (OSError, ValueError) as exc:
        raise _fail("docker_executable_rejected") from exc
    if digest.hexdigest() != DOCKER_EXECUTABLE_SHA256:
        raise _fail("docker_executable_rejected")
    return digest.hexdigest(), info.st_dev, info.st_ino


def _json_output(value: str) -> Any:
    if not isinstance(value, str) or not value.isascii() or len(value.encode("ascii")) > OUTPUT_LIMIT:
        raise _fail("docker_output_rejected")
    try:
        return json.loads(value)
    except (ValueError, TypeError) as exc:
        raise _fail("docker_output_rejected") from exc


def _version(value: str) -> tuple[str, str, str, str]:
    try:
        cv, sv, context, operating_system, architecture = value.strip().split("|")
        platform = f"{operating_system}/{architecture}"
    except (AttributeError, ValueError):
        raise _fail("docker_version_rejected")
    if (cv, sv, context, platform) != (DOCKER_VERSION, DOCKER_VERSION, "default", "linux/amd64"):
        raise _fail("docker_version_rejected")
    return cv, sv, context, platform


def _info(value: str) -> tuple[str, str]:
    try:
        daemon, security_raw = value.strip().split("|", 1)
        security = _json_output(security_raw)
    except (AttributeError, ValueError):
        raise _fail("docker_daemon_rejected")
    if not isinstance(security, list) or not any(x == "name=rootless" for x in security):
        raise _fail("docker_daemon_rejected")
    try:
        daemon_hash = hashlib.sha256(daemon.encode("ascii")).hexdigest()
    except UnicodeEncodeError as exc:
        raise _fail("docker_daemon_rejected") from exc
    if daemon_hash != DAEMON_ID_SHA256:
        raise _fail("docker_daemon_rejected")
    return daemon, DAEMON_ID_SHA256


def _bounded_command(argv: list[str], env: Mapping[str, str], *, timeout: float = 30,
                     check: bool = True, text: bool = True) -> subprocess.CompletedProcess:
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=dict(env))
    except OSError as exc:
        raise _fail("docker_client_failure") from exc
    streams = selectors.DefaultSelector()
    output = {"stdout": bytearray(), "stderr": bytearray()}
    assert process.stdout is not None and process.stderr is not None
    streams.register(process.stdout, selectors.EVENT_READ, "stdout")
    streams.register(process.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + timeout
    try:
        while streams.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill(); process.wait()
                raise _fail("docker_client_failure")
            for key, _ in streams.select(min(remaining, 0.1)):
                block = os.read(key.fileobj.fileno(), 8192)
                if not block:
                    streams.unregister(key.fileobj)
                    continue
                target = output[key.data]
                target.extend(block)
                if len(target) > OUTPUT_LIMIT:
                    process.kill(); process.wait()
                    raise _fail("docker_output_rejected")
        returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except (OSError, subprocess.SubprocessError) as exc:
        process.kill(); process.wait()
        raise _fail("docker_client_failure") from exc
    finally:
        streams.close()
    stdout_bytes, stderr_bytes = bytes(output["stdout"]), bytes(output["stderr"])
    if text:
        try:
            stdout, stderr = stdout_bytes.decode("ascii"), stderr_bytes.decode("ascii")
        except UnicodeDecodeError as exc:
            raise _fail("docker_output_rejected") from exc
    else:
        stdout, stderr = stdout_bytes, stderr_bytes
    result = subprocess.CompletedProcess(argv, returncode, stdout, stderr)
    if check and returncode != 0:
        raise _fail("docker_client_failure")
    return result


def _run(executable: Path, args: list[str], env: Mapping[str, str]) -> str:
    try:
        result = _bounded_command([str(executable), *args], env, timeout=30, check=True)
    except DockerContractError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise _fail("docker_client_failure") from exc
    if not isinstance(result.stdout, str) or not result.stdout.isascii() or len(result.stdout.encode("ascii")) > OUTPUT_LIMIT:
        raise _fail("docker_output_rejected")
    return result.stdout


def inspect_docker_contract(executable: Path, environment: Mapping[str, str] | None = None,
                            previous: DockerIdentity | None = None,
                            require_clean: bool = True) -> DockerContractResult:
    uid = os.geteuid()
    supplied = dict(os.environ if environment is None else environment)
    env = _bound_environment(supplied) if "DOCKER_CONFIG" in supplied else bind_environment(supplied)
    socket = endpoint().removeprefix("unix://")
    socket_device, socket_inode = _socket_identity(socket, uid)
    executable_hash, executable_device, executable_inode = _executable_identity(executable)
    version = _run(executable, ["version", "--format",
                               "{{.Client.Version}}|{{.Server.Version}}|{{.Client.Context}}|{{.Server.Os}}|{{.Server.Arch}}"], env)
    cv, sv, context, platform = _version(version)
    daemon, daemon_hash = _info(_run(executable, ["info", "--format", "{{.ID}}|{{json .SecurityOptions}}"], env))
    ps_raw = _run(executable, ["ps", "-a", "--filter", "name=^/va-mc-", "--format", "{{.Names}}"], env)
    names = tuple(line for line in ps_raw.splitlines() if line)
    if len(names) > 128 or any(not name.isascii() or len(name) > 128 for name in names):
        raise _fail("docker_output_rejected")
    if any(not _NAME.fullmatch(name) for name in names):
        raise _fail("docker_output_rejected")
    managed_count = sum(name.startswith("va-mc-") for name in names)
    if require_clean and managed_count:
        raise _fail("docker_managed_present")
    if _executable_identity(executable) != (executable_hash, executable_device, executable_inode):
        raise _fail("docker_identity_drift")
    if _socket_identity(socket, uid) != (socket_device, socket_inode):
        raise _fail("docker_identity_drift")
    identity = DockerIdentity(daemon_hash, executable_hash, cv, sv, context, platform,
                              socket_device, socket_inode, executable_device, executable_inode)
    if previous is not None and identity != previous:
        raise _fail("docker_identity_drift")
    report = {"connection_category": "current_uid_rootless_unix_socket",
              "authorization_category": "current_uid_owner_read_write_no_world",
              "daemon_identity_category": "pinned_rootless_daemon",
              "executable_identity_category": "pinned_trusted_executable",
              "version_category": "client_server_pinned",
              "filtered_ps": "clean",
              "managed_container_count": managed_count,
              "same_daemon_identity": "baseline" if previous is None else "matched"}
    return DockerContractResult(identity, report)


def make_bound_runner(executable: Path, identity: DockerIdentity,
                      environment: Mapping[str, str] | None = None):
    env = _bound_environment(dict(os.environ if environment is None else environment))

    def run(argv, **kwargs):
        text = kwargs.get("text", True)
        if (not isinstance(argv, list) or not argv or argv[0] != "docker"
                or kwargs.get("env") is not None or kwargs.get("cwd") is not None
                or kwargs.get("capture_output", True) is not True or not isinstance(text, bool)):
            raise _fail("docker_executor_command_rejected")
        try:
            inspect_docker_contract(executable, env, previous=identity, require_clean=False)
            return _bounded_command([str(executable), *argv[1:]], env,
                                    timeout=float(kwargs.get("timeout", 30)),
                                    check=bool(kwargs.get("check", False)), text=text)
        except DockerContractError as exc:
            raise OSError("docker authorization rejected") from exc
    return run


def verify_executor_prerequisite(runtime_module: Any | None = None, runner=None) -> str:
    """Verify the loaded runtime image and return only a matched category."""
    if runtime_module is None:
        import benchmarks.minecraft.docker_runtime as runtime_module
    try:
        server = runtime_module.DockerServer(Path("/nonexistent/va-minecraft-admission"), runner=runner)
        result = server.verify_image()
    except Exception as exc:
        raise _fail("docker_executor_prerequisite_failed") from exc
    if result != getattr(runtime_module, "PINNED_IMAGE_DIGEST", object()):
        raise _fail("docker_executor_prerequisite_failed")
    return "pinned_runtime_image_matched"
