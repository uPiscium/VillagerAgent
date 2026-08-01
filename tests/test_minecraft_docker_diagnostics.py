import subprocess
import sys

from benchmarks.minecraft.docker_diagnostics import (
    DIAGNOSTIC_STREAM_BYTES,
    BoundedDiagnosticExecutor,
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


def test_strict_sanitizer_redacts_secrets_and_paths():
    result = sanitize_output(
        b"safe diagnostic\nAuthorization: Bearer hidden\ninternal /private/path\n",
        b"",
        strict=True,
    )
    assert result["safe_output"] == ["safe diagnostic"]
    assert result["redacted_line_count"] == 2


def test_strict_sanitizer_keeps_minecraft_errors_and_drops_high_entropy_values():
    result = sanitize_output(
        (
            b"[12:34:56] [Server thread/ERROR]: failed to restart cleanly\n"
            b"0123456789abcdef0123456789abcdef\n"
        ),
        b"",
        strict=True,
    )

    assert result["safe_output"] == ["minecraft ERROR: failed to restart cleanly"]
    assert result["retained_safe_lines"] == 1
    assert result["redacted_line_count"] == 1


def test_restart_evidence_schema_keeps_records_for_invalid_target():
    calls = []

    def executor(argv, *, timeout):
        calls.append(argv)
        raise AssertionError("invalid target must not execute Docker")

    evidence = collect_restart_failure_evidence("unsafe.*", 1, executor)
    assert set(evidence) == {
        "schema_version", "collection_complete", "target_valid",
        "inspect_state", "logs_tail", "events_window", "ps_exact_name",
    }
    assert all(evidence[key]["outcome"] == "not_attempted" for key in (
        "inspect_state", "logs_tail", "events_window", "ps_exact_name"
    ))
    assert calls == []


def test_custom_diagnostic_executor_is_the_only_runner_used():
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "safe\n", "")

    evidence = collect_restart_failure_evidence(
        "va-mc-" + "a" * 32, 100, BoundedDiagnosticExecutor(runner)
    )
    assert evidence["collection_complete"] is True
    assert len(calls) == 4
