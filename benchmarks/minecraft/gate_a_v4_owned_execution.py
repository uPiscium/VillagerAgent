"""The externally composed, one-shot Gate A v4 coordinator.

This module contains the coordinator only.  Minting an operator capability and
host composition are deliberately outside the repository.
"""
from __future__ import annotations

import threading
import signal
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

FIXED_REVISION = "25113661a6b09761ab47a05bd70bd8f0386e2b67"
FIXED_RUNTIME_DIGEST = "sha256:25441b6e08ce2eff2a71dd6330ff4ddfaa6e5c9f1aa89e508e2580a16b262e0f"
FIXED_CHILD_MANIFEST = "ce8c30e13ddef9251d64a3f833625e509dd9590b163229f52fe585444794ae5d"
FIXED_PREMANIFEST_BYTES = "222afe434cace4e7609cddaae578284ba1d2a1b1ed0dd927a4a6155ade71192f"
FIXED_PREMANIFEST_CANONICAL = "bffaedbe296d18b1c4d0cd7bc0d073d011566e3415133d464e65492bb9c13f3a"
FIXED_MODEL_DIGEST = "4eb23ef187e2c5462566d6a1d3bbbc2f1346d0b4327cbb66d58fffbcc9b2b05c"
FIXED_DOCKER_CONTRACT = "ebf181d73d28e24ec8d257d06f3107d1f25211bfb26dedfd999507870fb41d01"
FIXED_RUN_ID = "diagonal-s17-baseline_open"
EXECUTOR_IMPLEMENTATION_SHA256 = "9376d78badba0716ca02c5cae80eeca4fbd5e3be2c4bbd2502aa961351b7a1b7"
VALIDATION_IMPLEMENTATION_SHA256 = "6d5b1e089c69a365a90aa04531e06eae323b716a83faa5c1bea0b732293a4764"
VALIDATION_TIMEOUT_SECONDS = 120

_COUNTER_KEYS = ("admission", "ownership_acquire", "restore", "executor", "validation",
                 "ownership_release", "retry", "replacement", "resume", "second_run")


class SimulationOutcome(str, Enum):
    SUCCESS = "success"
    RESTORE_FAILURE = "restore_failure"
    EXECUTION_FAILURE = "execution_failure"
    VALIDATION_FAILURE = "validation_failure"
    CLEANUP_FAILURE = "cleanup_failure"


class OwnedExecutionError(RuntimeError):
    pass


class GateARunLaunchCapability:
    """Nominal, unmintable authorization to launch the owned run envelope."""
    def __new__(cls, *args, **kwargs):
        del args, kwargs
        raise TypeError("operator capability minting is unavailable")

    def consume(self):
        raise TypeError("operator capability minting is unavailable")


@dataclass(frozen=True)
class ExecutionBindings:
    admission: Callable[[], dict[str, Any]]
    parent_fd: int
    lifecycle: Any
    managed_docker: Any
    binding: dict[str, Any]
    docker_runner_factory: Callable[[Any, Any], Callable[..., Any]]
    docker_residue_probe: Callable[[], int]
    owned_data_root: Callable[[Any], Any]
    restore_baseline_open_once: Callable[[Any], Any]
    execute_diagonal_s17_once: Callable[[Any, Any, Callable[..., Any], Any], Any]
    validate_diagonal_s17_once: Callable[[Any, Any], bool]
    cleanup_children: Callable[[Any], None]
    invalidate_runtime_result: Callable[[Any], None]
    finalize_runtime_result: Callable[[Any, str], None]
    postflight: Callable[[Any, Any, Any], dict[str, Any]]
    execution_kind: str
    execution_revision: str = FIXED_REVISION
    runtime_digest: str = FIXED_RUNTIME_DIGEST
    child_manifest_sha256: str = FIXED_CHILD_MANIFEST
    premanifest_byte_sha256: str = FIXED_PREMANIFEST_BYTES
    premanifest_canonical: str = FIXED_PREMANIFEST_CANONICAL
    model_digest: str = FIXED_MODEL_DIGEST
    docker_contract_sha256: str = FIXED_DOCKER_CONTRACT
    executor_implementation_sha256: str = EXECUTOR_IMPLEMENTATION_SHA256
    validation_implementation_sha256: str = VALIDATION_IMPLEMENTATION_SHA256


class ChildRegistryCapability:
    def __init__(self, lifecycle, handle):
        self._lifecycle, self._handle, self._identities = lifecycle, handle, []
    def register(self, identity):
        self._lifecycle.register_owned_child(self._handle, identity); self._identities.append(dict(identity))
    def mark_reaped(self, identity):
        self._lifecycle.mark_owned_child_reaped(self._handle, identity); self._identities.remove(identity)
    def count(self): return self._lifecycle.owned_child_count(self._handle)


def _counters(): return {key: 0 for key in _COUNTER_KEYS}
def _flags(): return {"canary": False, "five_run": False, "matrix": False, "production": False}


def _result(status, phase, reason, counters, postflight, kind="fake"):
    if kind == "real" and status == "fake_success": status = "success"
    return {"status": status, "phase": phase, "reason_code": reason,
            "counters": dict(counters), "postflight": dict(postflight),
            "judged_attempts": counters["executor"] if kind == "real" else 0,
            "execution_flags": {**_flags(), "canary": kind == "real" and counters["executor"] == 1}}


def validate_execution_bindings(bindings: ExecutionBindings) -> None:
    if not isinstance(bindings, ExecutionBindings): raise OwnedExecutionError("implementation_identity_mismatch")
    values = (bindings.execution_revision, bindings.runtime_digest, bindings.child_manifest_sha256,
              bindings.premanifest_byte_sha256, bindings.premanifest_canonical, bindings.model_digest,
              bindings.docker_contract_sha256, bindings.executor_implementation_sha256,
              bindings.validation_implementation_sha256)
    expected = (FIXED_REVISION, FIXED_RUNTIME_DIGEST, FIXED_CHILD_MANIFEST, FIXED_PREMANIFEST_BYTES,
                FIXED_PREMANIFEST_CANONICAL, FIXED_MODEL_DIGEST, FIXED_DOCKER_CONTRACT,
                EXECUTOR_IMPLEMENTATION_SHA256, VALIDATION_IMPLEMENTATION_SHA256)
    callbacks = (bindings.admission, bindings.docker_runner_factory, bindings.docker_residue_probe,
                 bindings.owned_data_root, bindings.restore_baseline_open_once,
                 bindings.execute_diagonal_s17_once, bindings.validate_diagonal_s17_once,
                 bindings.cleanup_children, bindings.invalidate_runtime_result,
                 bindings.finalize_runtime_result, bindings.postflight)
    methods = ("acquire_owned_run", "transition_run", "register_owned_child", "mark_owned_child_reaped",
               "owned_child_count", "release_owned_run", "block_owned_run")
    if values != expected or bindings.execution_kind not in {"fake", "real"} or any(not callable(x) for x in callbacks):
        raise OwnedExecutionError("implementation_identity_mismatch")
    if any(not callable(getattr(bindings.lifecycle, x, None)) for x in methods) or not callable(getattr(bindings.managed_docker, "bind_managed_docker", None)):
        raise OwnedExecutionError("implementation_identity_mismatch")


def _validate_admission(value):
    required = {"status":"admission_passed", "phase_id":"admission_passed", "reason_code":"none",
      "runtime_identity":"match", "premanifest_identity":"match", "model_inventory":"match",
      "docker_identity":"match", "managed_containers":0, "run_owned_children":0, "namespace_state":"absent",
      "output_state":"absent", "work_state":"absent", "runtime_result_state":"absent", "lock_state":"absent",
      "lease_state":"absent", "canary":FIXED_RUN_ID, "final_recheck":"passed", "attempts":0}
    if not isinstance(value, dict) or any(value.get(k) != v for k, v in required.items()) or value.get("execution_flags") != _flags() or any(value.get("counters", {}).values()):
        raise OwnedExecutionError("admission_rejected")


def execute_once(authorization: GateARunLaunchCapability, bindings: ExecutionBindings):
    if type(authorization) is not GateARunLaunchCapability: raise TypeError("run launch authorization required")
    if not isinstance(bindings, ExecutionBindings) or bindings.execution_kind != "real":
        raise TypeError("real run bindings required")
    authorization.consume()
    return _execute_once_core(bindings)


def _execute_once_after_launch_authorization(bindings: ExecutionBindings):
    """Execute after host authorization; exposed only for effect-free fake wiring tests."""
    if not isinstance(bindings, ExecutionBindings) or bindings.execution_kind != "fake":
        raise TypeError("fake wiring only")
    return _execute_once_core(bindings)


def _bounded_validation(callback, handle, bundle):
    if threading.current_thread() is not threading.main_thread():
        raise OwnedExecutionError("validation_thread_rejected")
    previous = signal.getsignal(signal.SIGALRM)

    def expired(signum, frame):
        del signum, frame
        raise TimeoutError("validation timeout")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, VALIDATION_TIMEOUT_SECONDS)
    try:
        return callback(handle, bundle)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _execute_once_core(bindings: ExecutionBindings):
    try: validate_execution_bindings(bindings)
    except Exception: return _result("execution_rejected", "identity", "identity_mismatch", _counters(), {"managed_containers":0,"run_owned_children":0,"lease_state":"not_acquired","runtime_result_reusable":False})
    counters = _counters(); counters["admission"] = 1
    try: _validate_admission(bindings.admission())
    except Exception: return _result("execution_rejected", "admission", "admission_failed", counters, {"managed_containers":0,"run_owned_children":0,"lease_state":"not_acquired","runtime_result_reusable":False}, bindings.execution_kind)
    handle = None
    try:
        handle = bindings.lifecycle.acquire_owned_run(bindings.parent_fd, bindings.binding); counters["ownership_acquire"] = 1
        children = ChildRegistryCapability(bindings.lifecycle, handle)
        docker = bindings.managed_docker.bind_managed_docker(bindings.docker_runner_factory(handle, children), bindings.binding, bindings.docker_residue_probe, handle=handle)
    except Exception:
        if handle is not None:
            try: bindings.lifecycle.block_owned_run(handle)
            except Exception: pass
            try: handle.close()
            except Exception: pass
        return _result("blocked_cleanup", "cleanup", "unexpected_failure", counters, {"managed_containers":-1,"run_owned_children":0,"lease_state":"blocked","runtime_result_reusable":False}, bindings.execution_kind)
    state, phase, reason = "prepared", "completed", "none"
    try:
        bindings.lifecycle.transition_run(handle,state,"restore_started"); state="restore_started"; counters["restore"] = 1
        restored = bindings.restore_baseline_open_once(handle)
        bindings.lifecycle.transition_run(handle,state,"restore_finished"); state="restore_finished"
        bindings.lifecycle.transition_run(handle,state,"executor_started"); state="executor_started"; counters["executor"] = 1
        bundle = bindings.execute_diagonal_s17_once(handle,restored,docker.executor_runner,children)
        bindings.lifecycle.transition_run(handle,state,"executor_finished"); state="executor_finished"
        bindings.lifecycle.transition_run(handle,state,"validation_started"); state="validation_started"; counters["validation"] = 1
        if _bounded_validation(bindings.validate_diagonal_s17_once, handle, bundle) is not True: raise OwnedExecutionError("validation_failed")
        bindings.lifecycle.transition_run(handle,state,"validation_finished"); state="validation_finished"
    except TimeoutError:
        phase = "restore" if state.startswith("restore") else ("validation" if state.startswith("validation") else "executor")
        reason = "timeout"
    except OwnedExecutionError as exc: phase, reason = "validation", str(exc)
    except Exception: phase, reason = ("restore" if state.startswith("restore") else "executor"), "unexpected_failure"
    errors=[]
    try: bindings.lifecycle.transition_run(handle,state,"cleanup_started"); state="cleanup_started"
    except Exception: errors.append("lifecycle_cleanup_transition_failed")
    for name, callback in (("docker_cleanup_failed",docker.cleanup_owned),("child_cleanup_failed",lambda: bindings.cleanup_children(children)),("runtime_result_invalidation_failed",lambda: bindings.invalidate_runtime_result(handle))):
        try: callback()
        except Exception: errors.append(name)
    try: child_count=children.count(); assert child_count == 0
    except Exception: child_count=-1; errors.append("child_postflight_failed")
    try: proof=docker.prove_clean(); managed_count=proof.managed_containers
    except Exception: proof=None; managed_count=-1; errors.append("docker_postflight_failed")
    expected={"managed_containers":0,"run_owned_children":0,"runtime_result_reusable":False}
    try:
        observed=bindings.postflight(handle,proof,children)
        if observed != expected: raise OwnedExecutionError("postflight_mismatch")
    except Exception: errors.append("postflight_failed")
    released=False
    if not errors:
        try:
            bindings.finalize_runtime_result(handle,"success" if reason == "none" else "failed_quarantined")
            bindings.lifecycle.transition_run(handle,state,"postflight_verified")
            bindings.lifecycle.release_owned_run(handle,expected,"success" if reason == "none" else "failed"); counters["ownership_release"] = 1; released=True
        except Exception: errors.append("ownership_release_failed")
    if errors:
        try: bindings.invalidate_runtime_result(handle)
        except Exception: pass
        try: bindings.finalize_runtime_result(handle,"cleanup_blocked_quarantined")
        except Exception: pass
        try: bindings.lifecycle.block_owned_run(handle)
        except Exception: pass
    try: handle.close()
    except Exception: pass
    post={"managed_containers":managed_count,"run_owned_children":child_count,"lease_state":"released" if released else "blocked","runtime_result_reusable":False}
    if errors: return _result("blocked_cleanup","cleanup",errors[0],counters,post,bindings.execution_kind)
    return _result("execution_failed" if reason != "none" else "fake_success", phase if reason != "none" else "completed", reason,counters,post,bindings.execution_kind)


def simulate_fake_execution(outcome=SimulationOutcome.SUCCESS):
    if not isinstance(outcome, SimulationOutcome): raise ValueError("unknown simulation outcome")
    c=_counters(); c.update(admission=1,ownership_acquire=1,restore=1,executor=1,validation=1,ownership_release=1)
    if outcome is SimulationOutcome.CLEANUP_FAILURE: c["ownership_release"] = 0; return _result("blocked_cleanup","cleanup","cleanup_failed",c,{"managed_containers":0,"run_owned_children":0,"lease_state":"blocked","runtime_result_reusable":False})
    if outcome is SimulationOutcome.SUCCESS: return _result("fake_success","completed","none",c,{"managed_containers":0,"run_owned_children":0,"lease_state":"released","runtime_result_reusable":False})
    phase = "restore" if outcome is SimulationOutcome.RESTORE_FAILURE else ("executor" if outcome is SimulationOutcome.EXECUTION_FAILURE else "validation")
    c[phase]=1
    return _result("execution_failed",phase,outcome.value,c,{"managed_containers":0,"run_owned_children":0,"lease_state":"released","runtime_result_reusable":False})
