"""Authenticated zero-argument launcher for the single Issue #502 Gate A attempt."""
from __future__ import annotations

import sys
import signal as _early_signal

_EARLY_PREVIOUS_HANDLERS = None
if __name__ == "__main__":
    _early_signals = {_early_signal.SIGINT, _early_signal.SIGTERM}
    _early_signal.pthread_sigmask(_early_signal.SIG_BLOCK, _early_signals)
    try:
        _EARLY_PREVIOUS_HANDLERS = {
            signum: _early_signal.getsignal(signum) for signum in _early_signals
        }
        for _early_signum in _early_signals:
            _early_signal.signal(_early_signum, _early_signal.SIG_IGN)
    finally:
        _early_signal.pthread_sigmask(_early_signal.SIG_UNBLOCK, _early_signals)

if __name__ == "__main__" and (not sys.flags.isolated or not sys.dont_write_bytecode):
    sys.stdout.write('{"attempt_consumed":false,"execution_flags":{"canary":false,"five_run":false,"matrix":false,"production":false},"gate_a_status":"failed","judged_attempts":0,"reason_code":"authentication_failed","schema_version":"gate_a_v3_issue502_result.v1"}\n')
    raise SystemExit(3)

import dataclasses
import hashlib
import json
import os
import re
import signal
import stat
from pathlib import Path
from types import ModuleType


READINESS_LAUNCHER_SHA256 = "c5de18d3eca2eeeb539c87dfe2f3db2a8f1cb8cefbc325ef50c8fa2bfc89fde2"
RUN_ID = "diagonal-s17-baseline_open"
RESULT_SCHEMA = "gate_a_v3_issue502_result.v1"
_READINESS_MODULE_NAME = "authenticated_issue502_readiness"
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_AUTHENTICATED_EXECUTE_SHA256 = globals().get("ISSUE502_AUTHENTICATED_LAUNCHER_SHA256")


class ExecuteOnceLauncherError(RuntimeError):
    pass


class _ValidationCapture:
    """Narrow pass-through that retains the one strict validator return value."""

    def __init__(self, validation_module):
        self.__file__ = validation_module.__file__
        self._validate = validation_module.validate_matrix_run
        self.invocations = 0
        self.record = None

    def validate_matrix_run(self, *args, **kwargs):
        if self.invocations != 0:
            raise ExecuteOnceLauncherError("strict_validation_reinvocation_rejected")
        self.invocations = 1
        record = self._validate(*args, **kwargs)
        self.record = record
        return record


class _ExecutionCapture:
    """Record entry into the exact fixed executor callback independently of counters."""

    def __init__(self, callback):
        self._callback = callback
        self.invocations = 0

    def __call__(self, *args, **kwargs):
        if self.invocations != 0:
            raise ExecuteOnceLauncherError("executor_reinvocation_rejected")
        self.invocations = 1
        return self._callback(*args, **kwargs)


def _load_readiness(component_root: Path):
    path = component_root / "gate_a_v3_real_execution_launcher.py"
    try:
        source = path.read_bytes()
    except OSError:
        raise ExecuteOnceLauncherError("readiness_authentication_failed") from None
    if (
        hashlib.sha256(source).hexdigest() != READINESS_LAUNCHER_SHA256
        or _READINESS_MODULE_NAME in sys.modules
    ):
        raise ExecuteOnceLauncherError("readiness_authentication_failed")
    module = ModuleType(_READINESS_MODULE_NAME)
    module.__file__ = str(path)
    sys.modules[_READINESS_MODULE_NAME] = module
    try:
        exec(compile(source, str(path), "exec"), module.__dict__, module.__dict__)
    except Exception:
        sys.modules.pop(_READINESS_MODULE_NAME, None)
        raise ExecuteOnceLauncherError("readiness_authentication_failed") from None
    return module


def _identity(info):
    return info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)


def _bind_parent_continuity(bindings, private_parent: Path):
    accepted = _identity(os.fstat(bindings.parent_fd))
    original = bindings.admission

    def immediate_admission():
        record = original()
        descriptor = os.fstat(bindings.parent_fd)
        pathname = os.stat(private_parent, follow_symlinks=False)
        if (
            not stat.S_ISDIR(descriptor.st_mode) or not stat.S_ISDIR(pathname.st_mode)
            or _identity(descriptor) != accepted or _identity(pathname) != accepted
        ):
            raise ExecuteOnceLauncherError("admission_parent_identity_changed")
        return record

    return dataclasses.replace(bindings, admission=immediate_admission)


def _safe_scalar(value):
    if value is None or type(value) in {bool, int, float}:
        return value
    if isinstance(value, str) and len(value) <= 128 and value.isascii():
        return value
    return "unavailable"


def _safe_sha(value):
    return value if isinstance(value, str) and _SHA256.fullmatch(value) else None


def _strict_validation_evidence(capture: _ValidationCapture):
    record = capture.record
    if not isinstance(record, dict):
        return {
            "invoked": capture.invocations == 1, "passed": False,
            "failed_check_ids": [], "scanner_clean": False,
            "run_manifest_sha256": None, "experiment_manifest_sha256": None,
            "final_judging": {},
        }
    failures = []
    for item in record.get("errors", ()):
        check = item.get("check") if isinstance(item, dict) else None
        if isinstance(check, str) and _SAFE_ID.fullmatch(check) and check not in failures:
            failures.append(check)
        if len(failures) == 64:
            break
    scanner = record.get("scanner") if isinstance(record.get("scanner"), dict) else {}
    findings = scanner.get("findings")
    manifests = record.get("manifests") if isinstance(record.get("manifests"), dict) else {}
    run_manifest = manifests.get("run") if isinstance(manifests.get("run"), dict) else {}
    experiment_manifest = (
        manifests.get("experiment") if isinstance(manifests.get("experiment"), dict) else {}
    )
    observed = record.get("observed") if isinstance(record.get("observed"), dict) else {}
    run_name = record.get("run_name")
    attempt_id = record.get("attempt_id")
    return {
        "invoked": capture.invocations == 1,
        "passed": record.get("passed") is True,
        "run_name": run_name if isinstance(run_name, str) and _SAFE_ID.fullmatch(run_name) else None,
        "attempt_id": attempt_id if isinstance(attempt_id, str) and _SAFE_ID.fullmatch(attempt_id) else None,
        "failed_check_ids": failures,
        "scanner_clean": findings == [],
        "scanner_implementation_sha256": _safe_sha(scanner.get("implementation_sha256")),
        "run_manifest_sha256": _safe_sha(run_manifest.get("sha256")),
        "experiment_manifest_sha256": _safe_sha(experiment_manifest.get("sha256")),
        "final_judging": {
            "score": _safe_scalar(observed.get("score")),
            "progress": _safe_scalar(observed.get("progress")),
            "end_reason": _safe_scalar(observed.get("end_reason")),
            "action_count": _safe_scalar(observed.get("action_count")),
            "failed_action_count": _safe_scalar(observed.get("failed_action_count")),
            "agent_iteration": _safe_scalar(observed.get("agent_iteration")),
            "judger_iteration": _safe_scalar(observed.get("judger_iteration")),
            "position_convention": _safe_scalar(observed.get("position_convention")),
        },
    }


def _bounded_result(
    coordinator_result, capture, execution_capture, readiness, *,
    checkout_exact, attempt_consumed, evidence_reason="none",
):
    result = coordinator_result if isinstance(coordinator_result, dict) else {}
    counters = result.get("counters") if isinstance(result.get("counters"), dict) else {}
    postflight = result.get("postflight") if isinstance(result.get("postflight"), dict) else {}
    strict = _strict_validation_evidence(capture)
    exact_counters = {
        "admission": 1, "ownership_acquire": 1, "restore": 1, "executor": 1,
        "validation": 1, "ownership_release": 1, "retry": 0, "replacement": 0,
        "resume": 0, "second_run": 0,
    }
    passed = (
        result.get("status") == "success" and strict.get("passed") is True
        and strict.get("run_name") == RUN_ID and checkout_exact and attempt_consumed
        and evidence_reason == "none"
        and execution_capture.invocations == 1
        and all(
            type(counters.get(name)) is int and counters.get(name) == value
            for name, value in exact_counters.items()
        )
        and postflight == {
            "managed_containers": 0, "run_owned_children": 0,
            "lease_state": "released", "runtime_result_reusable": False,
        }
    )
    components = {"readiness_launcher": READINESS_LAUNCHER_SHA256, **readiness.COMPONENT_SHA256}
    launcher_sha = _AUTHENTICATED_EXECUTE_SHA256
    return {
        "schema_version": RESULT_SCHEMA,
        "gate_a_status": "passed" if passed else "failed",
        "run_id": RUN_ID,
        "phase": _safe_scalar(result.get("phase")),
        "reason_code": _safe_scalar(result.get("reason_code")),
        "judged_attempts": execution_capture.invocations,
        "counters": {
            name: counters.get(name, 0) if type(counters.get(name)) is int else 0
            for name in exact_counters
        },
        "strict_validation": strict,
        "output_identity": {
            "run_id": strict.get("run_name"),
            "attempt_id": strict.get("attempt_id"),
            "run_manifest_sha256": strict.get("run_manifest_sha256"),
            "experiment_manifest_sha256": strict.get("experiment_manifest_sha256"),
        },
        "diagnostic_summary": {
            "phase": _safe_scalar(result.get("phase")),
            "reason_code": _safe_scalar(result.get("reason_code")),
            "evidence_reason": evidence_reason,
            "failed_check_ids": strict.get("failed_check_ids", []),
        },
        "cleanup": {
            "managed_containers": postflight.get("managed_containers"),
            "run_owned_children": postflight.get("run_owned_children"),
            "lease_state": postflight.get("lease_state"),
            "runtime_result_reusable": postflight.get("runtime_result_reusable"),
        },
        "checkout": {
            "revision": "e7d670fb196e51d8afeb7af00503c58407df7f4b",
            "clean": checkout_exact, "detached": checkout_exact,
        },
        "component_sha256": components,
        "execute_launcher_sha256": (
            launcher_sha
            if isinstance(launcher_sha, str) and _SHA256.fullmatch(launcher_sha)
            else None
        ),
        "execution_flags": {
            "canary": passed, "five_run": False, "matrix": False, "production": False,
        },
        "attempt_consumed": attempt_consumed,
        "evidence_status": "complete" if evidence_reason == "none" else "partial",
    }


def _fallback_post_execution_result(
    coordinator_result, capture, execution_capture, readiness, checkout_exact, attempt_consumed,
):
    result = coordinator_result if isinstance(coordinator_result, dict) else {}
    counters = result.get("counters") if isinstance(result.get("counters"), dict) else {}
    postflight = result.get("postflight") if isinstance(result.get("postflight"), dict) else {}
    return {
        "schema_version": RESULT_SCHEMA, "gate_a_status": "failed", "run_id": RUN_ID,
        "phase": _safe_scalar(result.get("phase")),
        "reason_code": _safe_scalar(result.get("reason_code")),
        "judged_attempts": execution_capture.invocations,
        "counters": {
            name: counters.get(name, 0) if type(counters.get(name)) is int else 0
            for name in (
                "admission", "ownership_acquire", "restore", "executor", "validation",
                "ownership_release", "retry", "replacement", "resume", "second_run",
            )
        },
        "cleanup": {
            "managed_containers": postflight.get("managed_containers"),
            "run_owned_children": postflight.get("run_owned_children"),
            "lease_state": postflight.get("lease_state"),
            "runtime_result_reusable": postflight.get("runtime_result_reusable"),
        },
        "component_sha256": {
            "readiness_launcher": READINESS_LAUNCHER_SHA256, **readiness.COMPONENT_SHA256,
        },
        "strict_validation": _safe_strict_validation_evidence(capture),
        "checkout": {
            "revision": "e7d670fb196e51d8afeb7af00503c58407df7f4b",
            "clean": checkout_exact, "detached": checkout_exact,
        },
        "diagnostic_summary": {"evidence_reason": "post_execution_evidence_failed"},
        "execution_flags": {
            "canary": False, "five_run": False, "matrix": False, "production": False,
        },
        "attempt_consumed": attempt_consumed,
        "evidence_status": "partial",
    }


def _safe_strict_validation_evidence(capture):
    try:
        return _strict_validation_evidence(capture)
    except BaseException:
        return {
            "invoked": getattr(capture, "invocations", 0) == 1, "passed": False,
            "failed_check_ids": [], "scanner_clean": False,
            "run_manifest_sha256": None, "experiment_manifest_sha256": None,
            "final_judging": {},
        }


def _restore_interrupt_handlers(previous):
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def _suppress_interrupts_for_evidence(previous):
    signals = set(previous)
    signal.pthread_sigmask(signal.SIG_BLOCK, signals)
    try:
        for signum in signals:
            signal.signal(signum, signal.SIG_IGN)
    finally:
        signal.pthread_sigmask(signal.SIG_UNBLOCK, signals)


def _namespace_consumed(parent_fd, lifecycle):
    try:
        info = os.stat(lifecycle.NAMESPACE_NAME, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _execute_fixed_once():
    if len(sys.argv) != 1:
        raise ExecuteOnceLauncherError("arguments_rejected")
    if not isinstance(_AUTHENTICATED_EXECUTE_SHA256, str) or not _SHA256.fullmatch(
        _AUTHENTICATED_EXECUTE_SHA256
    ):
        raise ExecuteOnceLauncherError("launcher_authentication_failed")
    raise ExecuteOnceLauncherError("consumed_v3_execution_disabled")
    component_root = Path(__file__).resolve(strict=True).parent
    readiness = _load_readiness(component_root)
    components = readiness._load_components(component_root)
    fixed = readiness._load_fixed_runtime()
    capture = _ValidationCapture(fixed["benchmarks.minecraft.matrix_validation"])
    adapter_module = components["real_adapter"]
    runtime_modules = adapter_module.RuntimeModules(
        runtime_execution=fixed["env.runtime_execution"],
        docker_runtime=fixed["benchmarks.minecraft.docker_runtime"],
        experiment=fixed["benchmarks.minecraft.experiment"],
        matrix_spec=fixed["benchmarks.minecraft.matrix_spec"],
        matrix_validation=capture,
        world_snapshot=fixed["benchmarks.minecraft.world_snapshot"],
    )
    fixed_adapter = adapter_module.bind_fixed_adapter(
        runtime_modules, components["lifecycle"], components["child_supervisor"],
        components["docker_contract"], _authority=adapter_module._BIND_AUTHORITY,
    )
    admission = components["admission"]
    host_bindings = admission._host_bindings(
        readiness.EXECUTION_ROOT, readiness.PREMANIFEST,
        component_root / readiness.COMPONENT_FILES["docker_contract"],
        readiness.DOCKER_EXECUTABLE,
    )
    bindings, parent_fd = readiness._compose_bindings(components, fixed_adapter, host_bindings)
    coordinator_result = None
    close_failed = False
    attempt_consumed = False
    try:
        bindings = _bind_parent_continuity(bindings, readiness.PRIVATE_PARENT)
        execution_capture = _ExecutionCapture(bindings.execute_diagonal_s17_once)
        bindings = dataclasses.replace(
            bindings, execute_diagonal_s17_once=execution_capture,
        )
        execution = components["coordinator"]
        execution.validate_execution_bindings(bindings)
        capability = execution.OneShotExecutionCapability(
            execution.ExecutionAuthorization(), bindings,
        )
        try:
            coordinator_result = capability.execute()
        except BaseException:
            coordinator_result = {
                "status": "blocked_cleanup", "phase": "cleanup",
                "reason_code": "unexpected_failure",
                "counters": {
                    "admission": 0, "ownership_acquire": 0, "restore": 0,
                    "executor": execution_capture.invocations, "validation": capture.invocations,
                    "ownership_release": 0, "retry": 0, "replacement": 0,
                    "resume": 0, "second_run": 0,
                },
                "postflight": {
                    "managed_containers": -1, "run_owned_children": -1,
                    "lease_state": "blocked", "runtime_result_reusable": False,
                },
            }
    finally:
        attempt_consumed = _namespace_consumed(parent_fd, components["lifecycle"])
        try:
            os.close(parent_fd)
        except Exception:
            close_failed = True
    if coordinator_result is None:
        raise ExecuteOnceLauncherError("execution_result_unavailable")
    checkout_exact = True
    try:
        readiness._authenticate_fixed_checkout()
    except Exception:
        checkout_exact = False
    evidence_reason = "none"
    if not checkout_exact:
        evidence_reason = "post_execution_checkout_drift"
    elif close_failed:
        evidence_reason = "parent_descriptor_close_failed"
    elif capture.record is None:
        evidence_reason = "strict_validation_record_unavailable"
    try:
        return _bounded_result(
            coordinator_result, capture, execution_capture, readiness,
            checkout_exact=checkout_exact, attempt_consumed=attempt_consumed,
            evidence_reason=evidence_reason,
        )
    except Exception:
        return _fallback_post_execution_result(
            coordinator_result, capture, execution_capture, readiness, checkout_exact,
            attempt_consumed,
        )


def main() -> int:
    previous_handlers = _EARLY_PREVIOUS_HANDLERS
    if previous_handlers is None:
        previous_handlers = {
            signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)
        }
        _suppress_interrupts_for_evidence(previous_handlers)
    try:
        result = _execute_fixed_once()
    except Exception as exc:
        reason = exc.args[0] if isinstance(exc, ExecuteOnceLauncherError) and exc.args else "unexpected_failure"
        result = {
            "schema_version": RESULT_SCHEMA, "gate_a_status": "failed",
            "run_id": RUN_ID, "reason_code": reason, "judged_attempts": 0,
            "counters": {
                "admission": 0, "ownership_acquire": 0, "restore": 0, "executor": 0,
                "validation": 0, "ownership_release": 0, "retry": 0,
                "replacement": 0, "resume": 0, "second_run": 0,
            },
            "execution_flags": {
                "canary": False, "five_run": False, "matrix": False, "production": False,
            },
            "attempt_consumed": False,
            "evidence_status": "partial",
        }
    try:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
        return 0 if result.get("gate_a_status") == "passed" else 3
    finally:
        _restore_interrupt_handlers(previous_handlers)


if __name__ == "__main__":
    raise SystemExit(main())
