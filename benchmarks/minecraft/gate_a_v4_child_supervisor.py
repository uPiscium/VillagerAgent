"""Pre-handoff child supervision for the fixed Gate A runtime adapter.

This module never chooses a command or workload.  It gates the two fixed child
creation seams: Docker CLI invocations already accepted by the managed Docker
capability, and the fixed experiment runtime process.
"""
from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path


_BOOTSTRAP = Path(__file__).with_name("gate_a_v4_child_supervisor_bootstrap.py")
_HANDSHAKE_TIMEOUT_SECONDS = 10


class ChildSupervisionError(RuntimeError):
    pass


def _read_identity(descriptor: int) -> dict:
    ready, _, _ = select.select([descriptor], [], [], _HANDSHAKE_TIMEOUT_SECONDS)
    if not ready:
        raise ChildSupervisionError("child identity handshake timed out")
    raw = os.read(descriptor, 512)
    try:
        identity = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ChildSupervisionError("child identity handshake rejected") from None
    if set(identity) != {"pid", "start_ticks", "pgid", "session_id"} or any(
        type(identity[name]) is not int or identity[name] <= 0 for name in identity
    ):
        raise ChildSupervisionError("child identity handshake rejected")
    return identity


def _proc_identity(pid: int) -> dict | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").rsplit(") ", 1)[1].split()
        return {
            "pid": pid, "start_ticks": int(fields[19]), "pgid": int(fields[2]),
            "session_id": int(fields[3]),
        }
    except (FileNotFoundError, OSError, ValueError, IndexError):
        return None


def _process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_exact(process, identity: dict) -> None:
    if process.poll() is None and _proc_identity(identity["pid"]) == identity:
        try:
            os.killpg(identity["pgid"], signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise ChildSupervisionError("owned child could not be reaped") from exc


class SupervisedDockerRunner:
    """A subprocess.run-compatible, Docker-only, register-before-exec runner."""

    def __init__(self, children, docker_executable: Path, docker_environment: dict, *, _bootstrap=None):
        self._children = children
        self._docker_executable = Path(docker_executable)
        self._docker_environment = dict(docker_environment)
        self._bootstrap = Path(_bootstrap) if _bootstrap is not None else _BOOTSTRAP

    def __call__(self, argv, **kwargs):
        if not isinstance(argv, list) or len(argv) < 2 or argv[0] != "docker":
            raise ChildSupervisionError("supervised Docker command rejected")
        if kwargs.get("shell"):
            raise ChildSupervisionError("supervised Docker shell rejected")
        capture_output = kwargs.pop("capture_output", False)
        check = kwargs.pop("check", False)
        timeout = kwargs.pop("timeout", None)
        input_value = kwargs.pop("input", None)
        if capture_output:
            if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
                raise ValueError("stdout and stderr arguments may not be used with capture_output")
            kwargs["stdout"] = subprocess.PIPE
            kwargs["stderr"] = subprocess.PIPE
        if input_value is not None and kwargs.get("stdin") is not None:
            raise ValueError("stdin and input arguments may not both be used")
        if input_value is not None:
            kwargs["stdin"] = subprocess.PIPE

        identity_read, identity_write = os.pipe()
        release_read, release_write = os.pipe()
        supplied_environment = kwargs.pop("env", self._docker_environment)
        if supplied_environment != self._docker_environment:
            raise ChildSupervisionError("supervised Docker environment rejected")
        env = dict(self._docker_environment)
        env["VA_GATE_A_IDENTITY_FD"] = str(identity_write)
        env["VA_GATE_A_RELEASE_FD"] = str(release_read)
        env["VA_GATE_A_DOCKER_EXECUTABLE"] = str(self._docker_executable)
        command = [sys.executable, "-I", str(self._bootstrap), "--", *argv]
        process = None
        identity = None
        registered = False
        try:
            process = subprocess.Popen(
                command, env=env, pass_fds=(identity_write, release_read), **kwargs,
            )
            os.close(identity_write)
            identity_write = -1
            os.close(release_read)
            release_read = -1
            identity = _read_identity(identity_read)
            if identity["pid"] != process.pid:
                raise ChildSupervisionError("child identity handshake rejected")
            self._children.register(identity)
            registered = True
            os.write(release_write, b"G")
            os.close(release_write)
            release_write = -1
            try:
                stdout, stderr = process.communicate(input=input_value, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                _terminate_exact(process, identity)
                stdout, stderr = process.communicate()
                exc.output = stdout
                exc.stdout = stdout
                exc.stderr = stderr
                raise
            if _process_group_exists(identity["pgid"]):
                raise ChildSupervisionError("owned Docker process group remains")
            result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
            if check:
                result.check_returncode()
            return result
        finally:
            for descriptor in (identity_read, identity_write, release_read, release_write):
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            if process is not None and process.poll() is None:
                if identity is not None:
                    _terminate_exact(process, identity)
                else:
                    process.kill()
                    process.wait()
            if registered:
                if not _process_group_exists(identity["pgid"]):
                    self._children.mark_reaped(identity)


def _runtime_entry(target, args, kwargs, identity_connection, release_connection):
    os.setsid()
    identity = _proc_identity(os.getpid())
    identity_connection.send(identity)
    identity_connection.close()
    if release_connection.recv_bytes(1) != b"G":
        raise ChildSupervisionError("runtime child release rejected")
    release_connection.close()
    original_setsid = os.setsid
    try:
        os.setsid = lambda: os.getsid(0)
        target(*args, **kwargs)
    finally:
        os.setsid = original_setsid


class _SupervisedProcess:
    def __init__(self, context, children, *args, **kwargs):
        target = kwargs.pop("target", None)
        target_args = kwargs.pop("args", ())
        target_kwargs = kwargs.pop("kwargs", {})
        if target is None or args or kwargs:
            raise ChildSupervisionError("runtime process shape rejected")
        parent_identity, child_identity = context.Pipe(duplex=False)
        child_release, parent_release = context.Pipe(duplex=False)
        self._identity_connection = parent_identity
        self._release_connection = parent_release
        self._children = children
        self._identity = None
        self._registered = False
        self._supervision_uncertain = False
        self._process = context.Process(
            target=_runtime_entry,
            args=(target, target_args, target_kwargs, child_identity, child_release),
        )

    def start(self):
        self._process.start()
        self._supervision_uncertain = True
        if not self._identity_connection.poll(_HANDSHAKE_TIMEOUT_SECONDS):
            self._process.kill()
            self._process.join(10)
            if self._process.is_alive():
                raise ChildSupervisionError("unregistered runtime child could not be reaped")
            raise ChildSupervisionError("runtime child identity handshake timed out")
        identity = self._identity_connection.recv()
        if identity is None or identity.get("pid") != self._process.pid:
            self._process.kill()
            self._process.join(10)
            if self._process.is_alive():
                raise ChildSupervisionError("unregistered runtime child could not be reaped")
            raise ChildSupervisionError("runtime child identity handshake rejected")
        try:
            self._children.register(identity)
        except BaseException:
            try:
                os.killpg(identity["pgid"], signal.SIGKILL)
            except ProcessLookupError:
                pass
            self._process.join(10)
            if self._process.is_alive():
                raise ChildSupervisionError("unregistered runtime child could not be reaped")
            raise
        self._identity = identity
        self._registered = True
        self._supervision_uncertain = False
        self._release_connection.send_bytes(b"G")
        self._release_connection.close()

    def join(self, timeout=None):
        return self._process.join(timeout)

    def terminate(self):
        if self._registered and _proc_identity(self._identity["pid"]) == self._identity:
            try:
                os.killpg(self._identity["pgid"], signal.SIGTERM)
            except ProcessLookupError:
                pass

    def kill(self):
        if self._registered and _proc_identity(self._identity["pid"]) == self._identity:
            try:
                os.killpg(self._identity["pgid"], signal.SIGKILL)
            except ProcessLookupError:
                pass

    def __getattr__(self, name):
        return getattr(self._process, name)


class _ContextProxy:
    def __init__(self, context, children, processes):
        self._context = context
        self._children = children
        self._processes = processes

    def Process(self, *args, **kwargs):
        process = _SupervisedProcess(self._context, self._children, *args, **kwargs)
        self._processes.append(process)
        return process

    def __getattr__(self, name):
        return getattr(self._context, name)


@contextmanager
def install_runtime_process_hook(experiment_module, children):
    """Scope the fixed experiment's get_context seam to an owned child proxy."""
    multiprocessing_module = experiment_module.multiprocessing
    original = multiprocessing_module.get_context
    processes = []

    def supervised_get_context(method=None):
        return _ContextProxy(original(method), children, processes)

    multiprocessing_module.get_context = supervised_get_context
    try:
        yield processes
    finally:
        multiprocessing_module.get_context = original


class OwnedChildSupervisor:
    """Own both fixed child seams for one retained lifecycle handle."""

    def __init__(self, children, docker_executable: Path, docker_environment: dict):
        self._children = children
        self._processes = []
        self.docker_runner = SupervisedDockerRunner(
            children, docker_executable, docker_environment,
        )

    @contextmanager
    def runtime_hook(self, experiment_module):
        with install_runtime_process_hook(
            experiment_module, self._children,
        ) as processes:
            try:
                yield
            finally:
                self._processes.extend(processes)

    def cleanup(self) -> None:
        for process in self._processes:
            if process._supervision_uncertain:
                if process._process.is_alive():
                    process._process.kill()
                    process._process.join(10)
                if process._process.is_alive():
                    raise ChildSupervisionError("unregistered runtime child remains")
                process._supervision_uncertain = False
            if process._registered:
                process.terminate()
                process.join(10)
            if process._registered and _process_group_exists(process._identity["pgid"]):
                try:
                    os.killpg(process._identity["pgid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.join(10)
                deadline = time.monotonic() + 10
                while _process_group_exists(process._identity["pgid"]) and time.monotonic() < deadline:
                    time.sleep(0.05)
            if process._registered and not process.is_alive():
                if _process_group_exists(process._identity["pgid"]):
                    raise ChildSupervisionError("owned runtime process group remains")
                self._children.mark_reaped(process._identity)
                process._registered = False
        if self._children.count() != 0:
            raise ChildSupervisionError("owned child cleanup incomplete")
