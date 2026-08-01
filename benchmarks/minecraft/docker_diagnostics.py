from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable


_SAFE_OUTPUT = re.compile(r"[\x20-\x7e]{1,300}\Z")
_CONTAINER_NAME = re.compile(r"va-mc-[0-9a-f]{32}\Z")
_SENSITIVE_OUTPUT = re.compile(
    r"(?:authorization|bearer|token|password|passwd|secret|api[ _-]?key|credential|cookie|session)",
    re.IGNORECASE,
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
    truncated: bool
    outcome: str

def output_bytes(value: str | bytes | None) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8", errors="replace")


def sanitize_output(
    stdout: bytes,
    stderr: bytes,
    *,
    strict: bool,
    stdout_bytes: int | None = None,
    stderr_bytes: int | None = None,
    pre_truncated: bool = False,
    safe_replacements: tuple[str, ...] = (),
) -> dict[str, Any]:
    raw_stdout_bytes = len(stdout) if stdout_bytes is None else stdout_bytes
    raw_stderr_bytes = len(stderr) if stderr_bytes is None else stderr_bytes
    selected = (
        b"\n".join(stream.rstrip(b"\r\n") for stream in (stdout, stderr) if stream)
        if strict else (stderr if stderr else stdout)
    )
    lines = selected.decode("utf-8", errors="replace").strip().splitlines()
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
        valid = bool(_SAFE_OUTPUT.fullmatch(line)) and not any(
            marker in line for marker in ("/", "\\", "=")
        )
        if strict and (
            "://" in line or _SENSITIVE_OUTPUT.search(line) or _HIGH_ENTROPY_OUTPUT.search(line)
        ):
            valid = False
        if not valid:
            redacted += 1
            continue
        if len(line) > _SAFE_LINE_CHARS:
            shortened = True
        safe_lines.append(line[:_SAFE_LINE_CHARS])
    retained = safe_lines[-_SAFE_LINE_LIMIT:]
    dropped = len(safe_lines) - len(retained)
    return {
        "safe_output": retained,
        "raw_stdout_bytes": raw_stdout_bytes,
        "raw_stderr_bytes": raw_stderr_bytes,
        "retained_safe_lines": len(retained),
        "redacted_line_count": redacted,
        "dropped_line_count": dropped,
        "truncated": pre_truncated or shortened or dropped > 0,
    }


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
    truncated = (
        outcome == "timeout"
        or any(thread.is_alive() for thread in threads)
        or any(totals[name] > len(buffers[name]) for name in ("stdout", "stderr"))
    )
    if outcome == "ok" and returncode != 0:
        outcome = "nonzero_exit"
    return DiagnosticCommandResult(
        returncode=returncode,
        stdout=bytes(buffers["stdout"]),
        stderr=bytes(buffers["stderr"]),
        stdout_bytes=totals["stdout"],
        stderr_bytes=totals["stderr"],
        truncated=truncated,
        outcome=outcome,
    )


class BoundedDiagnosticExecutor:
    """Bounded production executor, optionally backed by an injected test runner."""

    def __init__(self, runner: Callable[..., Any] | None = None):
        self.runner = runner

    def __call__(self, argv: list[str], *, timeout: float) -> DiagnosticCommandResult:
        if timeout <= 0:
            return DiagnosticCommandResult(None, b"", b"", 0, 0, True, "not_attempted")
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
            return DiagnosticCommandResult(None, stdout, stderr, len(stdout), len(stderr), True, "timeout")
        except (OSError, subprocess.SubprocessError):
            return DiagnosticCommandResult(None, b"", b"", 0, 0, False, "runner_error")
        stdout, stderr = output_bytes(result.stdout), output_bytes(result.stderr)
        return DiagnosticCommandResult(
            returncode=result.returncode,
            stdout=stdout,
            stderr=stderr,
            stdout_bytes=len(stdout),
            stderr_bytes=len(stderr),
            truncated=False,
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
            pre_truncated=result.truncated,
            safe_replacements=(container_name,),
        ),
    }


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
    return {
        "schema_version": 1,
        "collection_complete": False,
        "target_valid": target_valid,
        **{key: empty_diagnostic_record(outcome) for key in _RESTART_EVIDENCE_KEYS},
    }


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
                "status {{.State.Status}} running {{.State.Running}} paused {{.State.Paused}} restarting {{.State.Restarting}} oom_killed {{.State.OOMKilled}} dead {{.State.Dead}} exit_code {{.State.ExitCode}} restart_count {{.RestartCount}} health {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}{{println}}state_error {{.State.Error}}",
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
            evidence[key] = diagnostic_record(
                executor,
                argv,
                timeout=min(_DIAGNOSTIC_COMMAND_TIMEOUT_SECONDS, remaining),
                container_name=container_name,
            )
        if evidence[key]["outcome"] != "ok":
            evidence["collection_complete"] = False
    return evidence
