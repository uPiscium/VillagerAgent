from benchmarks.minecraft import gate_a_v4_owned_execution as execution
import pytest
from dataclasses import replace


class _Handle:
    namespace_fd = 0

    def __init__(self, events):
        self.events = events

    def close(self):
        self.events.append("close")


class _Lifecycle:
    def __init__(self, events, reusable=True):
        self.events, self.reusable = events, reusable
        self.handle = None

    def acquire_owned_run(self, parent_fd, binding):
        self.events.append("acquire")
        if not self.reusable:
            raise RuntimeError("lease already used")
        self.reusable = False
        self.handle = _Handle(self.events)
        return self.handle

    def transition_run(self, handle, before, after):
        self.events.append(("transition", before, after))

    def register_owned_child(self, handle, identity):
        pass

    def mark_owned_child_reaped(self, handle, identity):
        pass

    def owned_child_count(self, handle):
        return 0

    def release_owned_run(self, handle, postflight, outcome):
        self.events.append(("release", outcome))

    def block_owned_run(self, handle):
        self.events.append("block")


class _Docker:
    def __init__(self, events, failure=None):
        self.events, self.failure = events, failure
        self.executor_runner = lambda *args: None

    def cleanup_owned(self):
        self.events.append("docker_cleanup")
        if self.failure == "docker":
            raise RuntimeError("docker cleanup")

    def prove_clean(self):
        if self.failure == "docker_proof":
            raise RuntimeError("docker proof")
        return type("Proof", (), {"managed_containers": 0})()


def _bindings(events, *, mode="success", cleanup_failure=None, reusable=True):
    lifecycle = _Lifecycle(events, reusable)
    docker = _Docker(events, cleanup_failure)
    binding = {
        "experiment_id": "minecraft-judged-production-v4", "gate": "A",
        "run_id": "diagonal-s17-baseline_open", "lease_id": "a" * 64,
    }
    admission = {
        "status": "admission_passed", "phase_id": "admission_passed", "reason_code": "none",
        "runtime_identity": "match", "premanifest_identity": "match", "model_inventory": "match",
        "docker_identity": "match", "managed_containers": 0, "run_owned_children": 0,
        "namespace_state": "absent", "output_state": "absent", "work_state": "absent",
        "runtime_result_state": "absent", "lock_state": "absent", "lease_state": "absent",
        "canary": execution.FIXED_RUN_ID, "final_recheck": "passed", "attempts": 0,
        "execution_flags": {"canary": False, "five_run": False, "matrix": False, "production": False},
        "counters": {key: 0 for key in ("admission", "ownership_acquire", "restore", "executor", "validation",
                                         "ownership_release", "retry", "replacement", "resume", "second_run")},
    }

    def restore(handle):
        events.append("restore")
        if mode == "restore":
            raise RuntimeError("restore")
        if mode == "restore_timeout":
            raise TimeoutError
        return "world"

    def execute(handle, restored, runner, children):
        events.append("execute")
        if mode == "executor":
            raise RuntimeError("executor")
        if mode == "executor_timeout":
            raise TimeoutError
        return "bundle"

    def validate(handle, bundle):
        events.append("validate")
        return mode != "validation"

    def cleanup_children(children):
        events.append("child_cleanup")
        if cleanup_failure == "child":
            raise RuntimeError("children")

    def invalidate(handle):
        events.append("invalidate")

    def finalize(handle, status):
        events.append(("finalize", status))

    def postflight(handle, proof, children):
        events.append("postflight")
        if cleanup_failure == "collision":
            raise RuntimeError("output/partial-result collision")
        return {"managed_containers": 0, "run_owned_children": 0, "runtime_result_reusable": False}

    return execution.ExecutionBindings(
        lambda: admission, 1, lifecycle, type("Managed", (), {"bind_managed_docker": lambda _, runner, b, p, handle: docker})(),
        binding, lambda handle, children: (events.append("docker_bind") or (lambda *args: None)),
        lambda: 0, lambda handle: "data", restore, execute, validate, cleanup_children,
        invalidate, finalize, postflight, "fake",
    ), lifecycle


def _assert_no_retries(result):
    assert all(result["counters"][key] == 0 for key in ("retry", "replacement", "resume", "second_run"))


def test_fake_wiring_success_has_exact_order_counters_and_postflight():
    events = []
    bindings, lifecycle = _bindings(events)
    result = execution._execute_once_after_launch_authorization(bindings)
    assert result["status"] == "fake_success"
    assert result["counters"] == {"admission": 1, "ownership_acquire": 1, "restore": 1,
                                   "executor": 1, "validation": 1, "ownership_release": 1,
                                   "retry": 0, "replacement": 0, "resume": 0, "second_run": 0}
    assert result["postflight"] == {"managed_containers": 0, "run_owned_children": 0,
                                    "lease_state": "released", "runtime_result_reusable": False}
    assert events == ["acquire", "docker_bind",
                      ("transition", "prepared", "restore_started"),
                      "restore",
                      ("transition", "restore_started", "restore_finished"),
                      ("transition", "restore_finished", "executor_started"),
                      "execute",
                      ("transition", "executor_started", "executor_finished"),
                      ("transition", "executor_finished", "validation_started"),
                      "validate",
                      ("transition", "validation_started", "validation_finished"),
                      ("transition", "validation_finished", "cleanup_started"),
                      "docker_cleanup", "child_cleanup", "invalidate", "postflight",
                      ("finalize", "success"), ("transition", "cleanup_started", "postflight_verified"),
                      ("release", "success"), "close"]
    assert lifecycle.handle is not None


@pytest.mark.parametrize("mode, phase, reason", [
    ("restore", "restore", "unexpected_failure"), ("restore_timeout", "restore", "timeout"),
    ("executor", "executor", "unexpected_failure"), ("executor_timeout", "executor", "timeout"),
    ("validation", "validation", "validation_failed"),
])
def test_fake_wiring_execution_failures_cleanup_and_release(mode, phase, reason):
    events = []
    bindings, _ = _bindings(events, mode=mode)
    result = execution._execute_once_after_launch_authorization(bindings)
    assert (result["status"], result["phase"], result["reason_code"]) == ("execution_failed", phase, reason)
    assert result["postflight"]["lease_state"] == "released"
    assert result["counters"]["ownership_release"] == 1
    _assert_no_retries(result)
    assert {"docker_cleanup", "child_cleanup", "invalidate", "postflight", "close"}.issubset(events)


@pytest.mark.parametrize("failure", ["docker", "child", "collision"])
def test_fake_wiring_cleanup_or_output_collision_blocks_without_release(failure):
    events = []
    bindings, _ = _bindings(events, cleanup_failure=failure)
    result = execution._execute_once_after_launch_authorization(bindings)
    assert result["status"] == "blocked_cleanup"
    assert result["postflight"]["lease_state"] == "blocked"
    assert result["counters"]["ownership_release"] == 0
    _assert_no_retries(result)
    assert {"docker_cleanup", "child_cleanup", "invalidate", "close"}.issubset(events)


def test_fake_wiring_second_invocation_is_rejected_without_reuse_or_release():
    events = []
    bindings, _ = _bindings(events)
    first = execution._execute_once_after_launch_authorization(bindings)
    second = execution._execute_once_after_launch_authorization(bindings)
    assert first["status"] == "fake_success"
    assert second["status"] == "blocked_cleanup"
    assert second["counters"]["ownership_acquire"] == 0
    assert second["counters"]["ownership_release"] == 0
    assert second["postflight"]["lease_state"] == "blocked"
    _assert_no_retries(second)


def test_fake_success_is_exact_and_effect_free():
    result = execution.simulate_fake_execution()
    assert result["status"] == "fake_success"
    assert result["counters"] == {
        "admission": 1, "ownership_acquire": 1, "restore": 1,
        "executor": 1, "validation": 1, "ownership_release": 1,
        "retry": 0, "replacement": 0, "resume": 0, "second_run": 0,
    }
    assert result["judged_attempts"] == 0


def test_failure_accounting_is_deterministic_and_quarantined():
    outcome = execution.SimulationOutcome.CLEANUP_FAILURE
    result = execution.simulate_fake_execution(outcome)
    assert result["status"] == "blocked_cleanup"
    assert result["postflight"]["lease_state"] == "blocked"
    assert execution.simulate_fake_execution(outcome) == result


def test_production_surface_has_no_callback_or_effect_chain():
    forbidden = {"ExecutionAuthorization", "OneShotExecutionCapability", "_execute"}
    assert not forbidden.intersection(vars(execution))


def test_run_launch_capability_cannot_be_minted_in_repository():
    with pytest.raises(TypeError, match="minting is unavailable"):
        execution.GateARunLaunchCapability()
    with pytest.raises(TypeError, match="run launch authorization required"):
        execution.execute_once(object(), object())
    assert not {"ExecutionPermit", "AdapterPermit"}.intersection(vars(execution))


def test_fake_wiring_seam_rejects_real_bindings_before_admission():
    events = []
    bindings, _ = _bindings(events)
    real = replace(bindings, execution_kind="real")
    with pytest.raises(TypeError, match="fake wiring only"):
        execution._execute_once_after_launch_authorization(real)
    assert events == []


def test_public_run_path_rejects_fake_bindings_before_authorization_consume():
    events = []
    bindings, _ = _bindings(events)
    launch = object.__new__(execution.GateARunLaunchCapability)
    launch.consume = lambda: events.append("consumed")
    with pytest.raises(TypeError, match="real run bindings required"):
        execution.execute_once(launch, bindings)
    assert events == []
