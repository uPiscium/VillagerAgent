"""Closed, response-free diagnostics for post-restart scoreboard markers."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


REASON_CATEGORIES = frozenset({
    "rcon_connect_failed", "rcon_command_failed", "rcon_response_invalid",
    "baseline_marker_missing", "baseline_marker_normalization_failed",
    "baseline_marker_mismatch", "source_marker_missing",
    "source_marker_normalization_failed", "source_marker_mismatch",
    "restart_not_ready", "unexpected_failure", "verification_passed",
})
_STAGE_VALUES = frozenset({"not_attempted", "success", "failed"})
_COMPARE_VALUES = frozenset({"not_attempted", "match", "mismatch"})


class RconBoundaryError(RuntimeError):
    def __init__(self, category: str):
        if category not in {"rcon_connect_failed", "rcon_command_failed"}:
            category = "unexpected_failure"
        super().__init__(category)
        self.category = category


class MarkerVerificationError(RuntimeError):
    def __init__(self, reason: str, evidence: dict):
        if reason not in REASON_CATEGORIES or reason == "verification_passed":
            reason = "unexpected_failure"
        super().__init__(reason)
        self.reason = reason
        self.evidence = evidence


@dataclass
class _MarkerState:
    read: str = "not_attempted"
    normalization: str = "not_attempted"
    compare: str = "not_attempted"

    def public(self):
        if self.read not in _STAGE_VALUES or self.normalization not in _STAGE_VALUES or self.compare not in _COMPARE_VALUES:
            raise RuntimeError("marker evidence state rejected")
        return {
            "read": self.read, "normalization": self.normalization,
            "compare": self.compare,
        }


def _public(reason, baseline, source, *, rcon, ready_attempts):
    if (
        reason not in REASON_CATEGORIES or rcon not in {
            "not_attempted", "connected", "connect_failed", "command_failed",
        }
        or type(ready_attempts) is not int or not 0 <= ready_attempts <= 3
    ):
        raise RuntimeError("marker evidence rejected")
    return {
        "schema_version": "minecraft_restart_marker_verification.v1",
        "reason": reason, "rcon": rcon, "ready_attempts": ready_attempts,
        "baseline_marker": baseline.public(), "source_marker": source.public(),
    }


def _classify_response(response, marker: str, marker_kind: str, state: _MarkerState):
    if not isinstance(response, str):
        state.read = "success"
        state.normalization = "failed"
        return "rcon_response_invalid"
    state.read = "success"
    normalized = response.strip(" \t\r\n")
    missing = f"{marker_kind}_marker_missing"
    normalization_failed = f"{marker_kind}_marker_normalization_failed"
    mismatch = f"{marker_kind}_marker_mismatch"
    if not normalized or normalized.startswith("No score for "):
        state.normalization = "success"
        state.compare = "mismatch"
        return missing
    expected = f"{marker} has 1 [va_baseline]"
    if normalized == expected:
        state.normalization = "success"
        state.compare = "match"
        return "verification_passed"
    if expected in normalized or normalized.strip("'\"") == expected:
        state.normalization = "failed"
        return normalization_failed
    if marker in normalized and "va_baseline" in normalized:
        state.normalization = "success"
        state.compare = "mismatch"
        return mismatch
    state.normalization = "failed"
    return "rcon_response_invalid"


def verify_restart_markers(
    rcon: Callable[[str], str], baseline_marker: str, source_marker: str, *,
    sleep: Callable[[float], None] = time.sleep, max_ready_attempts: int = 3,
) -> dict:
    """Verify exact score semantics while retaining categories, never responses."""
    if (
        not callable(rcon) or not callable(sleep) or type(max_ready_attempts) is not int
        or not 1 <= max_ready_attempts <= 3
        or not baseline_marker or not source_marker
    ):
        raise MarkerVerificationError(
            "unexpected_failure",
            _public("unexpected_failure", _MarkerState(), _MarkerState(), rcon="not_attempted", ready_attempts=0),
        )
    baseline = _MarkerState()
    source = _MarkerState()
    ready_attempts = 0
    for marker_kind, marker, state in (
        ("baseline", baseline_marker, baseline),
        ("source", source_marker, source),
    ):
        while True:
            if marker_kind == "baseline":
                ready_attempts += 1
            try:
                response = rcon(f"scoreboard players get {marker} va_baseline")
            except RconBoundaryError as exc:
                state.read = "failed"
                rcon_state = "connect_failed" if exc.category == "rcon_connect_failed" else "command_failed"
                if (
                    marker_kind == "baseline" and exc.category == "rcon_connect_failed"
                    and ready_attempts < max_ready_attempts
                ):
                    sleep(0.25)
                    continue
                reason = exc.category
                evidence = _public(
                    reason, baseline, source, rcon=rcon_state,
                    ready_attempts=ready_attempts,
                )
                raise MarkerVerificationError(reason, evidence) from None
            except Exception:
                state.read = "failed"
                evidence = _public(
                    "unexpected_failure", baseline, source, rcon="not_attempted",
                    ready_attempts=ready_attempts,
                )
                raise MarkerVerificationError("unexpected_failure", evidence) from None
            reason = _classify_response(response, marker, marker_kind, state)
            if reason != "verification_passed":
                evidence = _public(
                    reason, baseline, source, rcon="connected",
                    ready_attempts=ready_attempts,
                )
                raise MarkerVerificationError(reason, evidence)
            break
    return _public(
        "verification_passed", baseline, source, rcon="connected",
        ready_attempts=ready_attempts,
    )
