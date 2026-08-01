import json
import subprocess
import sys

from benchmarks.minecraft.docker_diagnostics import (
    DIAGNOSTIC_STREAM_BYTES,
    BoundedDiagnosticExecutor,
    DiagnosticCommandResult,
    collect_restart_failure_evidence,
    run_bounded_command,
    sanitize_output,
)


def test_bounded_capture_accounts_for_output_without_retaining_it():
    result = run_bounded_command(
        [sys.executable, "-c", f"import sys; sys.stdout.buffer.write(b'x' * {DIAGNOSTIC_STREAM_BYTES * 2})"],
        timeout=3,
    )
    assert result.stdout == b""
    assert result.stdout_bytes == DIAGNOSTIC_STREAM_BYTES * 2
    assert result.truncated is True
    assert result.stdout_truncated is True
    assert result.stderr_truncated is False


def test_strict_sanitizer_redacts_secrets_and_paths():
    result = sanitize_output(
        b"safe diagnostic\nAuthorization: Bearer hidden\ninternal /private/path\n",
        b"",
        strict=True,
    )
    assert result["stdout"]["safe_output"] == [
        "safe diagnostic",
        "[REDACTED]",
        "internal [REDACTED]",
    ]
    assert result["stdout"]["redacted_line_count"] == 2
    assert result["stderr"]["safe_output"] == []


def test_strict_sanitizer_keeps_minecraft_errors_and_drops_high_entropy_values():
    result = sanitize_output(
        (
            b"[12:34:56] [Server thread/ERROR]: failed to restart cleanly\n"
            b"0123456789abcdef0123456789abcdef\n"
        ),
        b"",
        strict=True,
    )

    assert result["stdout"]["safe_output"] == [
        "minecraft ERROR: failed to restart cleanly"
    ]
    assert result["stdout"]["retained_safe_lines"] == 1
    assert result["stdout"]["redacted_line_count"] == 1


def test_strict_sanitizer_partially_redacts_sensitive_assignments():
    result = sanitize_output(
        b"request failed while token=hidden was refreshed\n",
        b"",
        strict=True,
    )

    assert result["stdout"]["safe_output"] == [
        "request failed while [REDACTED]"
    ]
    assert result["stdout"]["redacted_line_count"] == 1

    quoted = sanitize_output(
        b'login failed: password="correct horse battery staple"\n',
        b"",
        strict=True,
    )
    assert quoted["stdout"]["safe_output"] == ["login failed: [REDACTED]"]

    authorization = sanitize_output(
        b"restart failed: Authorization: Bearer hidden-value\n",
        b"",
        strict=True,
    )
    assert authorization["stdout"]["safe_output"] == [
        "restart failed: [REDACTED]"
    ]


def test_restart_evidence_schema_keeps_records_for_invalid_target():
    calls = []

    def executor(argv, *, timeout):
        calls.append(argv)
        raise AssertionError("invalid target must not execute Docker")

    evidence = collect_restart_failure_evidence("unsafe.*", 1, executor)
    assert set(evidence) == {
        "schema_version", "collection_complete", "target_valid",
        "diagnostics_implementation_sha256",
        "inspect_state", "logs_tail", "events_window", "ps_exact_name",
    }
    assert all(evidence[key]["outcome"] == "not_attempted" for key in (
        "inspect_state", "logs_tail", "events_window", "ps_exact_name"
    ))
    assert all(
        evidence[key][stream]["truncated"] is False
        for key in ("inspect_state", "logs_tail", "events_window", "ps_exact_name")
        for stream in ("stdout", "stderr")
    )
    assert calls == []


def test_custom_diagnostic_executor_is_the_only_runner_used():
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        if argv[:4] == ["docker", "inspect", "--type", "container"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                '{"State":{"Status":"exited"},"RestartCount":0}\n',
                "",
            )
        return subprocess.CompletedProcess(argv, 0, "safe\n", "")

    evidence = collect_restart_failure_evidence(
        "va-mc-" + "a" * 32, 100, BoundedDiagnosticExecutor(runner)
    )
    assert evidence["collection_complete"] is True
    assert len(calls) == 4


def test_injected_diagnostic_runner_enforces_per_stream_bounds():
    stdout = b"prefix\n" + b"x" * (DIAGNOSTIC_STREAM_BYTES * 2)
    stderr = b"safe stderr\n"

    def runner(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout, stderr)

    result = BoundedDiagnosticExecutor(runner)(["docker", "restart"], timeout=1)

    assert result.stdout == b""
    assert result.stdout_bytes == len(stdout)
    assert result.stdout_truncated is True
    assert result.stderr == stderr
    assert result.stderr_bytes == len(stderr)
    assert result.stderr_truncated is False


def test_nonpositive_budget_is_not_attempted_without_fake_truncation():
    result = BoundedDiagnosticExecutor(lambda *_args, **_kwargs: None)(
        ["docker", "restart"], timeout=0
    )

    assert result.outcome == "not_attempted"
    assert result.stdout_truncated is False
    assert result.stderr_truncated is False


def test_inspect_state_is_structured_and_health_output_is_partially_redacted():
    container = "va-mc-" + "a" * 32
    state = {
        "State": {
            "Status": "exited", "Running": False, "Paused": False,
            "Restarting": False, "OOMKilled": False, "Dead": False,
            "ExitCode": 1, "Error": "mount failed at /data/world",
            "StartedAt": "2026-08-01T01:00:00Z",
            "FinishedAt": "2026-08-01T01:01:00Z",
            "Health": {
                "Status": "unhealthy", "FailingStreak": 3,
                "Log": [{
                    "Start": "2026-08-01T01:00:30Z",
                    "End": "2026-08-01T01:00:31Z",
                    "ExitCode": 1,
                    "Output": "cannot open /data/server.properties\n",
                }],
            },
        },
        "RestartCount": 2,
    }

    def executor(argv, *, timeout):
        del timeout
        output = json.dumps(state).encode() if argv[:2] == ["docker", "inspect"] else b""
        return DiagnosticCommandResult(
            0, output, b"", len(output), 0, False, False, "ok"
        )

    evidence = collect_restart_failure_evidence(container, 1, executor)
    observed = evidence["inspect_state"]["state"]
    assert observed["started_at"] == "2026-08-01T01:00:00Z"
    assert observed["finished_at"] == "2026-08-01T01:01:00Z"
    assert observed["restart_count"] == 2
    assert observed["error"] == "mount failed at [REDACTED]"
    assert observed["health"]["failing_streak"] == 3
    assert observed["health"]["log"][0]["output"]["safe_output"] == [
        "cannot open [REDACTED]"
    ]
