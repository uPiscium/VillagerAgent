import pytest

from benchmarks.minecraft.restart_marker_verification import (
    MarkerVerificationError,
    RconBoundaryError,
    verify_restart_markers,
)


BASELINE = "baseline_open"
SOURCE = "src_8519378f5d71195a"


def _exact(command):
    marker = BASELINE if BASELINE in command else SOURCE
    return f"{marker} has 1 [va_baseline]"


def test_exact_baseline_and_source_markers_pass_with_closed_evidence():
    calls = []

    def rcon(command):
        calls.append(command)
        return _exact(command)

    result = verify_restart_markers(rcon, BASELINE, SOURCE)

    assert result == {
        "schema_version": "minecraft_restart_marker_verification.v1",
        "reason": "verification_passed", "rcon": "connected", "ready_attempts": 1,
        "baseline_marker": {"read": "success", "normalization": "success", "compare": "match"},
        "source_marker": {"read": "success", "normalization": "success", "compare": "match"},
    }
    assert calls == [
        f"scoreboard players get {BASELINE} va_baseline",
        f"scoreboard players get {SOURCE} va_baseline",
    ]


@pytest.mark.parametrize("response", [
    f"  {BASELINE} has 1 [va_baseline]  ",
    f"{BASELINE} has 1 [va_baseline]\r\n",
    f"\t{BASELINE} has 1 [va_baseline]\n",
])
def test_baseline_outer_whitespace_variation_is_normalized(response):
    values = iter((response, f"{SOURCE} has 1 [va_baseline]"))
    assert verify_restart_markers(lambda command: next(values), BASELINE, SOURCE)["reason"] == "verification_passed"


@pytest.mark.parametrize("response", [
    f'"{BASELINE} has 1 [va_baseline]"',
    f"prefix {BASELINE} has 1 [va_baseline]",
    f"{BASELINE} has 1 [va_baseline] suffix",
])
def test_baseline_quoting_prefix_or_suffix_is_normalization_failure(response):
    with pytest.raises(MarkerVerificationError) as captured:
        verify_restart_markers(lambda command: response, BASELINE, SOURCE)
    assert captured.value.reason == "baseline_marker_normalization_failed"
    assert captured.value.evidence["baseline_marker"] == {
        "read": "success", "normalization": "failed", "compare": "not_attempted",
    }


@pytest.mark.parametrize(("response", "reason"), [
    ("", "baseline_marker_missing"),
    ("No score for baseline_open is set", "baseline_marker_missing"),
    ("malformed response", "rcon_response_invalid"),
    (f"{BASELINE} has 2 [va_baseline]", "baseline_marker_mismatch"),
])
def test_baseline_empty_malformed_or_mismatch_has_exact_reason(response, reason):
    with pytest.raises(MarkerVerificationError) as captured:
        verify_restart_markers(lambda command: response, BASELINE, SOURCE)
    assert captured.value.reason == reason


@pytest.mark.parametrize(("source_response", "reason"), [
    (f'"{SOURCE} has 1 [va_baseline]"', "source_marker_normalization_failed"),
    (f"{SOURCE} has 2 [va_baseline]", "source_marker_mismatch"),
    ("", "source_marker_missing"),
])
def test_source_normalization_mismatch_and_missing_are_distinct(source_response, reason):
    responses = iter((f"{BASELINE} has 1 [va_baseline]", source_response))
    with pytest.raises(MarkerVerificationError) as captured:
        verify_restart_markers(lambda command: next(responses), BASELINE, SOURCE)
    assert captured.value.reason == reason
    assert captured.value.evidence["baseline_marker"]["compare"] == "match"


def test_source_outer_whitespace_variation_is_normalized():
    responses = iter((
        f"{BASELINE} has 1 [va_baseline]",
        f" \t{SOURCE} has 1 [va_baseline]\r\n",
    ))
    assert verify_restart_markers(lambda command: next(responses), BASELINE, SOURCE)["reason"] == "verification_passed"


def test_rcon_command_failure_is_not_transport_failure():
    with pytest.raises(MarkerVerificationError) as captured:
        verify_restart_markers(
            lambda command: (_ for _ in ()).throw(RconBoundaryError("rcon_command_failed")),
            BASELINE, SOURCE,
        )
    assert captured.value.reason == "rcon_command_failed"
    assert captured.value.evidence["rcon"] == "command_failed"


def test_source_rcon_transport_failure_is_exact_category():
    calls = 0

    def rcon(command):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RconBoundaryError("rcon_connect_failed")
        return _exact(command)

    with pytest.raises(MarkerVerificationError) as captured:
        verify_restart_markers(rcon, BASELINE, SOURCE)
    assert captured.value.reason == "rcon_connect_failed"


def test_restart_ready_delay_retries_only_baseline_connectivity():
    calls = 0
    sleeps = []

    def rcon(command):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RconBoundaryError("rcon_connect_failed")
        return _exact(command)

    result = verify_restart_markers(rcon, BASELINE, SOURCE, sleep=sleeps.append)
    assert result["reason"] == "verification_passed"
    assert result["ready_attempts"] == 2
    assert sleeps == [0.25]


def test_bounded_rcon_connect_failure_remains_exact_category():
    with pytest.raises(MarkerVerificationError) as captured:
        verify_restart_markers(
            lambda command: (_ for _ in ()).throw(RconBoundaryError("rcon_connect_failed")),
            BASELINE, SOURCE, sleep=lambda seconds: None,
        )
    assert captured.value.reason == "rcon_connect_failed"
    assert captured.value.evidence["ready_attempts"] == 3


def test_unexpected_response_type_is_invalid_without_value_retention():
    with pytest.raises(MarkerVerificationError) as captured:
        verify_restart_markers(lambda command: b"not text", BASELINE, SOURCE)
    assert captured.value.reason == "rcon_response_invalid"
    assert "response" not in captured.value.evidence


def test_unexpected_boundary_exception_is_closed_category():
    with pytest.raises(MarkerVerificationError) as captured:
        verify_restart_markers(
            lambda command: (_ for _ in ()).throw(RuntimeError("private")),
            BASELINE, SOURCE,
        )
    assert captured.value.reason == "unexpected_failure"
    assert "private" not in str(captured.value.evidence)
