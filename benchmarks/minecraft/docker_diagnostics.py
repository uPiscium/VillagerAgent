from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


_SAFE_OUTPUT = re.compile(r"[\x20-\x7e]{1,300}\Z")
_CONTAINER_NAME = re.compile(r"va-mc-[0-9a-f]{32}\Z")
_SENSITIVE_OUTPUT = re.compile(
    r"(?:authorization|bearer|token|password|passwd|secret|api[ _-]?key|credential|cookie|session)",
    re.IGNORECASE,
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(?:token|password|passwd|secret|api[ _-]?key|credential|cookie|session)\s*[:=].*$"
)
_HIGH_ENTROPY_OUTPUT = re.compile(r"[A-Za-z0-9_-]{32,}")
_MINECRAFT_LOG_PREFIX = re.compile(
    r"^\[[^\]\r\n]{1,40}\]\s+\[[A-Za-z0-9 _.-]{1,40}/(INFO|WARN|ERROR|FATAL)\]:\s*(.*)$"
)
DIAGNOSTIC_STREAM_BYTES = 64 * 1024
_DIAGNOSTIC_COMMAND_TIMEOUT_SECONDS = 3.0
_DIAGNOSTIC_COLLECTION_TIMEOUT_SECONDS = 10.0
_DIAGNOSTIC_LOG_TAIL = 200
_SAFE_LINE_LIMIT = 5
_SAFE_LINE_CHARS = 160
_HEALTH_LOG_LIMIT = 5
_RESTART_EVIDENCE_KEYS = (
    "inspect_state",
    "logs_tail",
    "events_window",
    "ps_exact_name",
)


@dataclass(frozen=True)
class DiagnosticCommandResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    stdout_bytes: int
    stderr_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    outcome: str

    @property
    def truncated(self) -> bool:
        return self.stdout_truncated or self.stderr_truncated


def output_bytes(value: str | bytes | None) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8", errors="replace")


def _sanitize_stream(
    value: bytes,
    *,
    strict: bool,
    raw_bytes: int | None = None,
    pre_truncated: bool = False,
    safe_replacements: tuple[str, ...] = (),
) -> dict[str, Any]:
    total_bytes = len(value) if raw_bytes is None else raw_bytes
    lines = value.decode("utf-8", errors="replace").strip().splitlines()
    safe_lines: list[str] = []
    redacted = 0
    shortened = False
    for line in lines:
        for value in safe_replacements:
            line = line.replace(value, "<container>")
        if strict:
            minecraft_log = _MINECRAFT_LOG_PREFIX.fullmatch(line)
            if minecraft_log:
                line = f"minecraft {minecraft_log.group(1)}: {minecraft_log.group(2)}"
            line, sensitive_replacements = _SENSITIVE_ASSIGNMENT.subn(
                "[REDACTED]", line
            )
            if sensitive_replacements:
                redacted += 1
        valid = bool(_SAFE_OUTPUT.fullmatch(line))
        if strict and (
            "://" in line or _SENSITIVE_OUTPUT.search(line) or _HIGH_ENTROPY_OUTPUT.search(line)
        ):
            valid = False
        if not valid:
            redacted += 1
            continue
        partially_redacted = re.sub(r"\S*[/\\=]\S*", "[REDACTED]", line)
        if partially_redacted != line:
            line = partially_redacted
            redacted += 1
        if len(line) > _SAFE_LINE_CHARS:
            shortened = True
        safe_lines.append(line[:_SAFE_LINE_CHARS])
    retained = safe_lines[-_SAFE_LINE_LIMIT:]
    dropped = len(safe_lines) - len(retained)
    return {
        "safe_output": retained,
        "raw_bytes": total_bytes,
        "retained_safe_lines": len(retained),
        "redacted_line_count": redacted,
        "dropped_line_count": dropped,
        "truncated": pre_truncated or shortened or dropped > 0,
    }


def sanitize_output(
    stdout: bytes,
    stderr: bytes,
    *,
    strict: bool,
    stdout_bytes: int | None = None,
    stderr_bytes: int | None = None,
    pre_truncated: bool = False,
    stdout_truncated: bool | None = None,
    stderr_truncated: bool | None = None,
    safe_replacements: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Sanitize command streams independently so their provenance is retained."""
    return {
        "stdout": _sanitize_stream(
            stdout,
            strict=strict,
            raw_bytes=stdout_bytes,
            pre_truncated=(
                pre_truncated if stdout_truncated is None else stdout_truncated
            ),
            safe_replacements=safe_replacements,
        ),
        "stderr": _sanitize_stream(
            stderr,
            strict=strict,
            raw_bytes=stderr_bytes,
            pre_truncated=(
                pre_truncated if stderr_truncated is None else stderr_truncated
            ),
            safe_replacements=safe_replacements,
        ),
    }


def diagnostics_implementation_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _sanitized_text(value: Any) -> str:
    lines = _sanitize_stream(output_bytes(value), strict=True)["safe_output"]
    return lines[-1] if lines else ""


def run_bounded_command(
    argv: list[str], *, timeout: float, cleanup_reserve: float = 0.5
) -> DiagnosticCommandResult:
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    totals = {"stdout": 0, "stderr": 0}

    def drain(name: str, stream: Any) -> None:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            totals[name] += len(chunk)
            buffers[name].extend(chunk)
            overflow = len(buffers[name]) - DIAGNOSTIC_STREAM_BYTES
            if overflow > 0:
                del buffers[name][:overflow]

    threads = [
        threading.Thread(
            target=drain,
            args=(name, getattr(process, name)),
            daemon=True,
        )
        for name in ("stdout", "stderr")
    ]
    for thread in threads:
        thread.start()
    execution_deadline = time.monotonic() + timeout
    deadline = execution_deadline + cleanup_reserve
    outcome = "ok"
    try:
        returncode = process.wait(timeout=max(0.001, execution_deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        outcome = "timeout"
        process.kill()
        try:
            returncode = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            returncode = process.poll()
    for thread in threads:
        thread.join(timeout=max(0, deadline - time.monotonic()))
    for name in ("stdout", "stderr"):
        if totals[name] > len(buffers[name]):
            newline = buffers[name].find(b"\n")
            if newline < 0:
                buffers[name].clear()
            else:
                del buffers[name][:newline + 1]
    stream_truncated = {
        name: (
            outcome == "timeout"
            or threads[index].is_alive()
            or totals[name] > len(buffers[name])
        )
        for index, name in enumerate(("stdout", "stderr"))
    }
    if outcome == "ok" and returncode != 0:
        outcome = "nonzero_exit"
    return DiagnosticCommandResult(
        returncode=returncode,
        stdout=bytes(buffers["stdout"]),
        stderr=bytes(buffers["stderr"]),
        stdout_bytes=totals["stdout"],
        stderr_bytes=totals["stderr"],
        stdout_truncated=stream_truncated["stdout"],
        stderr_truncated=stream_truncated["stderr"],
        outcome=outcome,
    )


class BoundedDiagnosticExecutor:
    """Bounded production executor, optionally backed by an injected test runner."""

    def __init__(self, runner: Callable[..., Any] | None = None):
        self.runner = runner

    def __call__(self, argv: list[str], *, timeout: float) -> DiagnosticCommandResult:
        if timeout <= 0:
            return DiagnosticCommandResult(
                None, b"", b"", 0, 0, True, True, "not_attempted"
            )
        if self.runner is None:
            cleanup_reserve = min(0.5, timeout / 2)
            return run_bounded_command(
                argv,
                timeout=max(0.001, timeout - cleanup_reserve),
                cleanup_reserve=cleanup_reserve,
            )
        try:
            result = self.runner(argv, check=False, capture_output=True, text=False, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = output_bytes(exc.stdout), output_bytes(exc.stderr)
            return DiagnosticCommandResult(
                None, stdout, stderr, len(stdout), len(stderr), True, True, "timeout"
            )
        except (OSError, subprocess.SubprocessError):
            return DiagnosticCommandResult(
                None, b"", b"", 0, 0, False, False, "runner_error"
            )
        stdout, stderr = output_bytes(result.stdout), output_bytes(result.stderr)
        return DiagnosticCommandResult(
            returncode=result.returncode,
            stdout=stdout,
            stderr=stderr,
            stdout_bytes=len(stdout),
            stderr_bytes=len(stderr),
            stdout_truncated=False,
            stderr_truncated=False,
            outcome="ok" if result.returncode == 0 else "nonzero_exit",
        )


def diagnostic_record(
    executor: Callable[..., DiagnosticCommandResult],
    argv: list[str],
    *,
    timeout: float,
    container_name: str,
) -> dict[str, Any]:
    result = executor(argv, timeout=timeout)
    return {
        "outcome": result.outcome,
        "exit_code": result.returncode,
        **sanitize_output(
            result.stdout,
            result.stderr,
            strict=True,
            stdout_bytes=result.stdout_bytes,
            stderr_bytes=result.stderr_bytes,
            stdout_truncated=result.stdout_truncated,
            stderr_truncated=result.stderr_truncated,
            safe_replacements=(container_name,),
        ),
    }


def _inspect_state(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict) or not isinstance(value.get("State"), dict):
        raise ValueError("Docker inspect state is not an object")
    state = value["State"]
    health = state.get("Health")
    health_record = None
    if health is not None:
        if not isinstance(health, dict):
            raise ValueError("Docker health state is not an object")
        raw_log = health.get("Log", [])
        if not isinstance(raw_log, list):
            raise ValueError("Docker health log is not an array")
        log = []
        for item in raw_log[-_HEALTH_LOG_LIMIT:]:
            if not isinstance(item, dict):
                raise ValueError("Docker health log entry is not an object")
            output = output_bytes(item.get("Output"))
            log.append({
                "start": str(item.get("Start", "")),
                "end": str(item.get("End", "")),
                "exit_code": int(item.get("ExitCode", 0)),
                "output": _sanitize_stream(output, strict=True),
            })
        health_record = {
            "status": str(health.get("Status", "")),
            "failing_streak": int(health.get("FailingStreak", 0)),
            "log": log,
        }
    return {
        "status": str(state.get("Status", "")),
        "running": bool(state.get("Running", False)),
        "paused": bool(state.get("Paused", False)),
        "restarting": bool(state.get("Restarting", False)),
        "oom_killed": bool(state.get("OOMKilled", False)),
        "dead": bool(state.get("Dead", False)),
        "exit_code": int(state.get("ExitCode", 0)),
        "error": _sanitized_text(state.get("Error", "")),
        "started_at": str(state.get("StartedAt", "")),
        "finished_at": str(state.get("FinishedAt", "")),
        "restart_count": int(value.get("RestartCount", 0)),
        "health": health_record,
    }


def inspect_state_record(
    executor: Callable[..., DiagnosticCommandResult],
    argv: list[str],
    *,
    timeout: float,
    container_name: str,
) -> dict[str, Any]:
    result = executor(argv, timeout=timeout)
    record = {
        "outcome": result.outcome,
        "exit_code": result.returncode,
        **sanitize_output(
            b"",
            result.stderr,
            strict=True,
            stdout_bytes=result.stdout_bytes,
            stderr_bytes=result.stderr_bytes,
            stdout_truncated=result.stdout_truncated,
            stderr_truncated=result.stderr_truncated,
            safe_replacements=(container_name,),
        ),
        "state": None,
    }
    if result.outcome == "ok":
        try:
            record["state"] = _inspect_state(result.stdout)
        except (TypeError, ValueError, json.JSONDecodeError):
            record["outcome"] = "invalid_output"
    return record


def is_valid_container_name(container_name: str) -> bool:
    return bool(_CONTAINER_NAME.fullmatch(container_name))


def empty_diagnostic_record(outcome: str) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "exit_code": None,
        **sanitize_output(b"", b"", strict=True, pre_truncated=True),
    }


def empty_restart_failure_evidence(
    *, target_valid: bool, outcome: str
) -> dict[str, Any]:
    evidence = {
        "schema_version": 2,
        "diagnostics_implementation_sha256": diagnostics_implementation_sha256(),
        "collection_complete": False,
        "target_valid": target_valid,
        **{key: empty_diagnostic_record(outcome) for key in _RESTART_EVIDENCE_KEYS},
    }
    evidence["inspect_state"]["state"] = None
    return evidence


def collect_restart_failure_evidence(
    container_name: str,
    restart_started: float,
    executor: Callable[..., DiagnosticCommandResult],
) -> dict[str, Any]:
    valid = is_valid_container_name(container_name)
    evidence = empty_restart_failure_evidence(
        target_valid=valid, outcome="not_attempted"
    )
    evidence["collection_complete"] = valid
    commands = (
        (
            "inspect_state",
            [
                "docker",
                "inspect",
                "--type",
                "container",
                "--format",
                '{"State":{{json .State}},"RestartCount":{{.RestartCount}}}',
                container_name,
            ],
        ),
        (
            "logs_tail",
            ["docker", "logs", "--tail", str(_DIAGNOSTIC_LOG_TAIL), container_name],
        ),
        (
            "events_window",
            [
                "docker",
                "events",
                "--since",
                str(int(restart_started)),
                "--until",
                str(int(time.time()) + 1),
                "--filter",
                "type=container",
                "--filter",
                f"container={container_name}",
                "--format",
                "time {{.TimeNano}} type {{.Type}} action {{.Action}}",
            ],
        ),
        (
            "ps_exact_name",
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"name=^/{container_name}$",
                "--format",
                "id {{.ID}} name {{.Names}} state {{.State}} status {{.Status}}",
            ],
        ),
    )
    started = time.monotonic()
    for key, argv in commands:
        remaining = _DIAGNOSTIC_COLLECTION_TIMEOUT_SECONDS - (time.monotonic() - started)
        if not valid or remaining <= 0:
            evidence[key] = empty_diagnostic_record("not_attempted")
        else:
            record = inspect_state_record if key == "inspect_state" else diagnostic_record
            evidence[key] = record(
                executor, argv,
                timeout=min(_DIAGNOSTIC_COMMAND_TIMEOUT_SECONDS, remaining),
                container_name=container_name,
            )
        if evidence[key]["outcome"] != "ok":
            evidence["collection_complete"] = False
    return evidence
