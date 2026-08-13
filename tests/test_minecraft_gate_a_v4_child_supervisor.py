import multiprocessing
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.minecraft import gate_a_v4_child_supervisor as supervisor


class _Lifecycle:
    def __init__(self, marker=None):
        self.registered = []
        self.reaped = []
        self.marker = marker

    def register(self, identity):
        if self.marker is not None:
            assert not self.marker.exists()
        self.registered.append(dict(identity))

    def mark_reaped(self, identity):
        self.reaped.append(dict(identity))

    def count(self):
        return len(self.registered) - len(self.reaped)


class _RejectRegistration(_Lifecycle):
    def register(self, identity):
        super().register(identity)
        raise RuntimeError("registration rejected")


def _runtime_target(marker):
    os.setsid()
    Path(marker).write_text("released", encoding="ascii")


def _runtime_target_with_short_descendant(marker):
    if os.fork() == 0:
        os.execl("/run/current-system/sw/bin/sleep", "sleep", "0.2")
    Path(marker).write_text("leader-exited", encoding="ascii")


def _runtime_target_with_stubborn_descendant(marker):
    if os.fork() == 0:
        import signal
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        os.execl("/run/current-system/sw/bin/sleep", "sleep", "30")
    Path(marker).write_text("leader-exited", encoding="ascii")


def test_docker_child_is_registered_before_fixed_executable_runs(tmp_path):
    marker = tmp_path / "docker-ran"
    executable = tmp_path / "docker"
    executable.write_text(f"#!/bin/sh\nprintf released > '{marker}'\nprintf ok\n", encoding="ascii")
    executable.chmod(0o700)
    bootstrap = tmp_path / "bootstrap.py"
    bootstrap.write_text(
        "import os,sys; os.setsid(); i=int(os.environ.pop('VA_GATE_A_IDENTITY_FD')); r=int(os.environ.pop('VA_GATE_A_RELEASE_FD')); e=os.environ.pop('VA_GATE_A_DOCKER_EXECUTABLE'); os.write(i, ('{\"pid\":%d,\"start_ticks\":1,\"pgid\":%d,\"session_id\":%d}' % (os.getpid(), os.getpgrp(), os.getsid(0))).encode()); os.close(i); os.read(r, 1); os.execve(e, sys.argv[2:], os.environ)\n",
        encoding="ascii",
    )
    lifecycle = _Lifecycle(marker)
    runner = supervisor.SupervisedDockerRunner(lifecycle, executable, dict(os.environ), _bootstrap=bootstrap)

    result = runner(["docker", "version"], capture_output=True, text=True, check=True)

    assert result.stdout == "ok"
    assert marker.read_text(encoding="ascii") == "released"
    assert lifecycle.registered == lifecycle.reaped
    assert lifecycle.registered[0]["pid"] > 0


def test_registration_failure_never_releases_docker_activity(tmp_path):
    marker = tmp_path / "docker-ran"
    executable = tmp_path / "docker"
    executable.write_text(f"#!/bin/sh\nprintf released > '{marker}'\n", encoding="ascii")
    executable.chmod(0o700)
    bootstrap = tmp_path / "bootstrap.py"
    bootstrap.write_text(
        "import json,os,sys\n"
        "os.setsid()\n"
        "fields=open(f'/proc/{os.getpid()}/stat').read().rsplit(') ',1)[1].split()\n"
        "identity={'pid':os.getpid(),'start_ticks':int(fields[19]),'pgid':int(fields[2]),'session_id':int(fields[3])}\n"
        "os.write(int(os.environ['VA_GATE_A_IDENTITY_FD']),json.dumps(identity).encode())\n"
        "os.read(int(os.environ['VA_GATE_A_RELEASE_FD']),1)\n",
        encoding="ascii",
    )
    lifecycle = _RejectRegistration(marker)
    runner = supervisor.SupervisedDockerRunner(
        lifecycle, executable, dict(os.environ), _bootstrap=bootstrap,
    )

    with pytest.raises(RuntimeError, match="registration rejected"):
        runner(["docker", "version"], capture_output=True, text=True, check=True)

    assert not marker.exists()


def test_runtime_target_is_registered_before_release_and_reaped(tmp_path):
    marker = tmp_path / "runtime-ran"
    lifecycle = _Lifecycle(marker)
    experiment = SimpleNamespace(multiprocessing=multiprocessing)
    owner = supervisor.OwnedChildSupervisor(
        lifecycle, tmp_path / "docker", dict(os.environ),
    )

    with owner.runtime_hook(experiment):
        process = experiment.multiprocessing.get_context("fork").Process(
            target=_runtime_target, args=(marker,),
        )
        process.start()
        process.join(10)
    owner.cleanup()

    assert process.exitcode == 0
    assert marker.read_text(encoding="ascii") == "released"
    assert lifecycle.registered == lifecycle.reaped


def test_supervised_docker_rejects_non_docker_command(tmp_path):
    runner = supervisor.SupervisedDockerRunner(
        _Lifecycle(), tmp_path / "docker", dict(os.environ),
    )
    try:
        runner(["sh", "-c", "true"])
    except supervisor.ChildSupervisionError as exc:
        assert str(exc) == "supervised Docker command rejected"
    else:
        raise AssertionError("non-Docker command was accepted")


def test_runtime_registration_is_not_reaped_while_descendant_group_remains(tmp_path):
    marker = tmp_path / "runtime-descendant"
    children = _Lifecycle()
    experiment = SimpleNamespace(multiprocessing=multiprocessing)
    owner = supervisor.OwnedChildSupervisor(children, tmp_path / "docker", dict(os.environ))
    with owner.runtime_hook(experiment):
        process = experiment.multiprocessing.get_context("fork").Process(
            target=_runtime_target_with_short_descendant, args=(marker,),
        )
        process.start()
        process.join(10)
    owner.cleanup()
    assert children.registered == children.reaped


def test_cleanup_kills_stubborn_descendant_after_leader_exit(tmp_path):
    marker = tmp_path / "runtime-stubborn-descendant"
    children = _Lifecycle()
    experiment = SimpleNamespace(multiprocessing=multiprocessing)
    owner = supervisor.OwnedChildSupervisor(children, tmp_path / "docker", dict(os.environ))
    with owner.runtime_hook(experiment):
        process = experiment.multiprocessing.get_context("fork").Process(
            target=_runtime_target_with_stubborn_descendant, args=(marker,),
        )
        process.start()
        process.join(10)
    owner.cleanup()
    assert children.registered == children.reaped
