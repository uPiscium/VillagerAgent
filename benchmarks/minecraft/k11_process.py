"""Bounded subprocess supervision for Minecraft benchmark runs."""

from __future__ import annotations

import math
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Sequence


def supervise_process(
    command: Sequence[str],
    *,
    timeout_seconds: float,
    artifact_ready_path: str | os.PathLike[str] | None = None,
    completion_grace_seconds: float = 0.5,
    termination_grace_seconds: float = 0.5,
    kill_grace_seconds: float = 0.5,
    cwd: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, int | float | bool | None]:
    """Run *command* in an isolated session and clean up its whole process group."""
    if os.name != "posix":
        raise OSError("the K11 process supervisor requires POSIX process groups")
    timeout_seconds = _positive_finite(timeout_seconds, "timeout_seconds")
    completion_grace_seconds = _nonnegative_finite(completion_grace_seconds, "completion_grace_seconds")
    termination_grace_seconds = _nonnegative_finite(
        termination_grace_seconds, "termination_grace_seconds"
    )
    kill_grace_seconds = _nonnegative_finite(kill_grace_seconds, "kill_grace_seconds")
    artifact_path = Path(artifact_ready_path) if artifact_ready_path is not None else None

    started = time.monotonic()
    process = subprocess.Popen(
        list(command), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    process_group_id = process.pid
    term_sent = False
    kill_sent = False
    timed_out = False
    post_artifact_linger = False
    post_parent_group_linger = False

    try:
        deadline = started + timeout_seconds
        artifact_seen_at = None
        while process.poll() is None:
            now = time.monotonic()
            if _artifact_exists(artifact_path):
                artifact_seen_at = artifact_seen_at or now
                if now - artifact_seen_at >= completion_grace_seconds:
                    post_artifact_linger = True
                    break
            if now >= deadline:
                timed_out = True
                break
            time.sleep(min(0.02, max(0.0, deadline - now)))

        if process.poll() is None:
            term_sent = _signal_group(process_group_id, signal.SIGTERM)
            _wait_for_group_exit(process_group_id, termination_grace_seconds)
            if _group_exists(process_group_id):
                kill_sent = _signal_group(process_group_id, signal.SIGKILL)
                _wait_for_group_exit(process_group_id, kill_grace_seconds)
        try:
            process.wait(timeout=kill_grace_seconds)
        except subprocess.TimeoutExpired:
            pass
    finally:
        if _group_exists(process_group_id):
            post_parent_group_linger = process.poll() is not None
            if not term_sent:
                term_sent = _signal_group(process_group_id, signal.SIGTERM)
                _wait_for_group_exit(process_group_id, termination_grace_seconds)
            if _group_exists(process_group_id) and not kill_sent:
                kill_sent = _signal_group(process_group_id, signal.SIGKILL)
                _wait_for_group_exit(process_group_id, kill_grace_seconds)

    return {
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "artifact_ready": _artifact_exists(artifact_path),
        "post_artifact_linger": post_artifact_linger,
        "post_parent_group_linger": post_parent_group_linger,
        "term_sent": term_sent,
        "kill_sent": kill_sent,
        "duration": time.monotonic() - started,
        "process_group_alive_after_cleanup": _group_exists(process_group_id),
    }


run_bounded_process = supervise_process


def _artifact_exists(path: Path | None) -> bool:
    return path is not None and path.exists()


def _group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _signal_group(process_group_id: int, sent_signal: int) -> bool:
    try:
        os.killpg(process_group_id, sent_signal)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _wait_for_group_exit(process_group_id: int, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while _group_exists(process_group_id) and time.monotonic() < deadline:
        time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))


def _positive_finite(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def _nonnegative_finite(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return value
