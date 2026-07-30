import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from pipeline.controller_tiny import (
    ControllerShutdownError,
    GlobalController,
    JudgedEvidenceConsistencyError,
    JudgedTaskFailure,
    TaskExecutionGroup,
)
from env.minecraft_client import MinecraftActionLogError, MinecraftToolTimeoutError
from env.minecraft_client import ToolActionBlockedError
from pipeline.runtime_events import InMemoryRuntimeEventRecorder
from pipeline.task_manager import TaskManager
from type_define.graph import Task


@pytest.mark.parametrize("failing_entrypoint", [
    "execute_tasks",
    "worker",
    "process_completed_tasks",
])
def test_thread_failure_stops_controller_and_preserves_first_exception(failing_entrypoint):
    controller, checkpoints, sink = _controller()
    failure = RuntimeError(f"{failing_entrypoint} failed")

    def fail():
        raise failure

    def wait_for_shutdown():
        assert controller.shutdown_event.wait(2)

    for entrypoint in ("execute_tasks", "worker", "process_completed_tasks"):
        setattr(controller, entrypoint, fail if entrypoint == failing_entrypoint else wait_for_shutdown)

    with pytest.raises(RuntimeError) as raised:
        controller.run()

    assert raised.value is failure
    assert all(not thread.is_alive() for thread in controller._controller_threads)
    assert all(not thread.is_alive() for thread in controller.executor._threads)
    assert checkpoints == ["checkpoint"]
    assert [event["event_type"] for event in sink.events] == ["run_failed"]
    assert sink.events[0]["payload"]["thread"] == failing_entrypoint
    assert "raise failure" in sink.events[0]["payload"]["traceback"]


def test_normal_completion_stops_all_threads_executor_and_checkpoints():
    controller, checkpoints, sink = _controller()

    def complete():
        controller._request_shutdown()

    def wait_for_shutdown():
        assert controller.shutdown_event.wait(2)

    controller.execute_tasks = complete
    controller.worker = wait_for_shutdown
    controller.process_completed_tasks = wait_for_shutdown

    controller.run()

    assert all(not thread.is_alive() for thread in controller._controller_threads)
    assert all(not thread.is_alive() for thread in controller.executor._threads)
    assert checkpoints == ["checkpoint"]
    assert [event["event_type"] for event in sink.events] == ["run_completed"]


def test_should_shutdown_is_a_side_effect_free_query():
    controller, _, _ = _controller()
    controller.env = SimpleNamespace(
        is_task_complete=lambda: True,
        stop=lambda: (_ for _ in ()).throw(AssertionError("must not stop environment")),
    )

    assert controller.should_shutdown() is False
    assert controller._judger_terminal_observed is False


def test_judged_completion_persists_canonical_success_before_shutdown():
    controller, _, sink = _controller()
    task = Task("Judged task", {})
    task.candidate_list = ["Alice"]
    task.number = 1
    manager = TaskManager(silent=True, event_sink=sink)
    manager.set_task_list_from_decomposition([task])
    projected_task = manager.query_runnable_subtasks(["Alice"])[0]
    manager.mark_task_running(projected_task, ["Alice"])
    sink.events.clear()
    snapshots = []
    manager.runtime_checkpoint = lambda: snapshots.append(manager.runtime_task_store.snapshot())
    controller.task_manager = manager
    agent = SimpleNamespace(name="Alice")
    future = controller.executor.submit(lambda: ("done", "detail"))
    future.result(timeout=1)
    group = TaskExecutionGroup(
        task=projected_task,
        agents=[agent],
        futures={"Alice": future},
        started_at=time.time(),
        submission_complete=True,
    )
    controller.result_queue.append(group)
    controller.assignment["Alice"] = projected_task.id
    stopped = []
    controller.env = SimpleNamespace(
        attempt_id="attempt-a",
        task_name="runtime-task-a",
        is_task_complete=lambda: True,
        get_score=lambda: {
            "attempt_id": "attempt-a",
            "task_name": "runtime-task-a",
            "status": "success",
            "score": 100,
            "progress": 100,
        },
        stop=lambda: stopped.append(True),
        agents_ping=lambda: {"status": True},
    )
    controller.query_interval = 0
    controller.execute_tasks = controller.shutdown_event.wait
    controller.worker = controller.shutdown_event.wait

    controller.run()

    assert group.completed is True
    assert group.terminal_state_persisted is True
    assert controller.assignment == {}
    final_snapshot = snapshots[-1]
    assert final_snapshot["summary"]["terminal_state"] == "success"
    assert final_snapshot["nodes"][0]["lifecycle"]["status"] == Task.success
    assert final_snapshot["nodes"][0]["lifecycle"]["active_agents"] == []
    assert final_snapshot["nodes"][0]["content"]["reflect"]["terminal_source"] == "external_judger"
    assert controller.shutdown_complete is True
    assert stopped == [True]
    assert [event["event_type"] for event in sink.events] == [
        "task_status_changed",
        "run_completed",
    ]


def test_judger_failure_persists_canonical_failure_once():
    controller, manager, sink, _ = _judged_reconciliation_controller(status="failure")

    assert controller.observe_judger_terminal() is True
    assert controller.reconcile_judger_terminal() is True
    assert controller.reconcile_judger_terminal() is True

    snapshot = manager.runtime_task_store.snapshot()
    assert snapshot["summary"]["terminal_state"] == "failure"
    assert snapshot["nodes"][0]["lifecycle"]["status"] == Task.failure
    status_events = [event for event in sink.events if event["event_type"] == "task_status_changed"]
    assert len(status_events) == 1
    assert controller.shutdown_event.is_set() is True
    assert controller._first_failure[2]["thread"] == "external_judger"
    error = controller._first_failure[0]
    assert isinstance(error, JudgedTaskFailure)
    assert "status=failure" in str(error)
    assert "diagnostics=judged_terminal_diagnostics.json" in str(error)


@pytest.mark.parametrize(
    "error",
    [
        MinecraftActionLogError("action evidence unavailable", agent="Alice"),
        MinecraftToolTimeoutError("tool response timed out", agent="Alice"),
    ],
)
def test_judger_success_rejects_retry_unsafe_future_exception(error):
    controller, manager, _, task = _judged_reconciliation_controller()
    future = Future()
    future.set_exception(error)
    group = TaskExecutionGroup(
        task=task,
        agents=[SimpleNamespace(name="Alice")],
        futures={"Alice": future},
        submission_complete=True,
    )
    controller.result_queue.append(group)
    controller.assignment["Alice"] = task.id
    controller.observe_judger_terminal()

    with pytest.raises(JudgedEvidenceConsistencyError) as raised:
        controller.reconcile_judger_terminal()

    snapshot = manager.runtime_task_store.snapshot()
    assert snapshot["summary"]["terminal_state"] == "failure"
    assert snapshot["nodes"][0]["lifecycle"]["status"] == Task.failure
    feedback = snapshot["nodes"][0]["content"]["reflect"]
    assert feedback["judger_status"] == "success"
    assert feedback["evidence_consistency"] == "failed"
    assert feedback["agent_failures"]["Alice"]["retry_safe"] is False
    assert raised.value.agent_failures == feedback["agent_failures"]


@pytest.mark.parametrize("future_state", ["cancelled", "malformed"])
def test_judger_success_rejects_invalid_completed_future(future_state):
    controller, manager, _, task = _judged_reconciliation_controller()
    future = Future()
    if future_state == "cancelled":
        future.cancel()
    else:
        future.set_result("not a step result")
    group = TaskExecutionGroup(
        task=task,
        agents=[SimpleNamespace(name="Alice")],
        futures={"Alice": future},
        submission_complete=True,
    )
    controller.result_queue.append(group)
    controller.assignment["Alice"] = task.id
    controller.observe_judger_terminal()

    with pytest.raises(JudgedEvidenceConsistencyError):
        controller.reconcile_judger_terminal()

    assert manager.runtime_task_store.snapshot()["summary"]["terminal_state"] == "failure"


def test_judger_success_accepts_valid_completed_future():
    controller, manager, _, task = _judged_reconciliation_controller()
    future = Future()
    future.set_result(("done", {"action_list": [{"action": "navigateTo"}]}))
    group = TaskExecutionGroup(
        task=task,
        agents=[SimpleNamespace(name="Alice")],
        futures={"Alice": future},
        submission_complete=True,
    )
    controller.result_queue.append(group)
    controller.assignment["Alice"] = task.id
    controller.observe_judger_terminal()

    assert controller.reconcile_judger_terminal() is True

    snapshot = manager.runtime_task_store.snapshot()
    assert snapshot["summary"]["terminal_state"] == "success"
    feedback = snapshot["nodes"][0]["content"]["reflect"]
    assert feedback["agent_execution"]["valid"] is True


def test_judger_success_accepts_tool_blocked_by_terminal_barrier():
    controller, manager, _, task = _judged_reconciliation_controller()
    future = Future()
    future.set_exception(
        ToolActionBlockedError(
            "Cannot start Minecraft tool action after judger terminal detection"
        )
    )
    group = TaskExecutionGroup(
        task=task,
        agents=[SimpleNamespace(name="Alice")],
        futures={"Alice": future},
        submission_complete=True,
    )
    controller.result_queue.append(group)
    controller.assignment["Alice"] = task.id
    controller.observe_judger_terminal()

    assert controller.reconcile_judger_terminal() is True

    snapshot = manager.runtime_task_store.snapshot()
    assert snapshot["summary"]["terminal_state"] == "success"
    feedback = snapshot["nodes"][0]["content"]["reflect"]
    assert feedback["agent_execution"]["valid"] is True
    assert feedback["agent_execution"]["agent_results"]["Alice"]["status"] == "terminal_blocked"


def test_judger_payload_attempt_mismatch_is_rejected():
    controller, manager, _, _ = _judged_reconciliation_controller()
    controller.env.get_score = lambda: {
        "attempt_id": "stale-attempt",
        "task_name": "runtime-task-a",
        "status": "success",
        "score": 100,
    }

    with pytest.raises(ControllerShutdownError, match="attempt mismatch"):
        controller.observe_judger_terminal()

    assert manager.runtime_task_store.snapshot()["summary"]["terminal_state"] == "running"


def test_worker_does_not_pop_queue_after_terminal_observation():
    controller, _, _ = _controller()
    task = Task("Queued after judged completion", {})
    group = TaskExecutionGroup(task=task, agents=[SimpleNamespace(name="Alice")])
    controller.task_queue.append(group)
    controller.query_interval = 0.01
    controller.env = SimpleNamespace(
        attempt_id="attempt-a",
        task_name="runtime-task-a",
        is_task_complete=lambda: True,
        get_score=lambda: {
            "attempt_id": "attempt-a",
            "task_name": "runtime-task-a",
            "status": "success",
            "score": 100,
        },
        agents_ping=lambda: (_ for _ in ()).throw(
            AssertionError("worker must not continue after terminal observation")
        ),
    )
    worker = threading.Thread(target=controller.worker)

    worker.start()
    deadline = time.monotonic() + 1
    while not controller._judger_terminal_pending and time.monotonic() < deadline:
        time.sleep(0.01)
    controller._request_shutdown()
    worker.join(1)
    controller.executor.shutdown(wait=True)

    assert not worker.is_alive()
    assert controller.task_queue == [group]
    assert controller.result_queue == []


def test_terminal_detection_closes_tool_barrier_before_waiting_for_active_action():
    controller, _, _, _ = _judged_reconciliation_controller()
    controller._begin_tool_action()
    observation_result = []
    observation = threading.Thread(
        target=lambda: observation_result.append(controller.observe_judger_terminal())
    )

    observation.start()
    deadline = time.monotonic() + 1
    while not controller._judger_terminal_pending and time.monotonic() < deadline:
        time.sleep(0.01)

    assert controller._judger_terminal_pending is True
    assert controller._judger_terminal_observed is False
    observation.join(1)
    assert not observation.is_alive()
    assert observation_result == [True]
    with pytest.raises(ToolActionBlockedError, match="after judger terminal"):
        controller._begin_tool_action()

    controller._end_tool_action()
    assert controller.reconcile_judger_terminal() is True
    assert controller._judger_terminal_observed is True


def test_terminal_pending_blocks_assignment_queue_handoff_and_submission():
    controller, _, _, task = _judged_reconciliation_controller()
    agent = SimpleNamespace(name="Alice", supports_cooperative_cancellation=lambda: False)
    queued = TaskExecutionGroup(task=task, agents=[agent])
    controller.task_queue.append(queued)
    controller.observe_judger_terminal()

    assert controller.execute_assignments([{
        "task_instance": task,
        "agent_instances": [agent],
    }]) == 0
    assert controller._take_and_start_next_execution_group() is False
    with pytest.raises(ControllerShutdownError, match="after judger terminal"):
        controller.start_execution_group(queued)

    assert controller.task_queue == [queued]
    assert controller.result_queue == []
    assert queued.futures == {}


def test_active_tool_drain_timeout_does_not_publish_judged_success():
    controller, manager, _, task = _judged_reconciliation_controller()
    controller.assignment["Alice"] = task.id
    controller.judger_tool_drain_grace_period = 0
    controller._begin_tool_action()
    controller.observe_judger_terminal()

    try:
        with pytest.raises(ControllerShutdownError, match="tool action.*remained active"):
            controller.reconcile_judger_terminal()
    finally:
        controller._end_tool_action()

    snapshot = manager.runtime_task_store.snapshot()
    assert snapshot["summary"]["terminal_state"] == "running"
    assert snapshot["nodes"][0]["lifecycle"]["active_agents"] == ["Alice"]
    assert controller.assignment == {"Alice": task.id}
    assert controller._terminal_barrier_context() == {
        "pending": True,
        "observed": False,
        "detected_at": controller._judger_terminal_detected_at,
        "active_tool_actions": 0,
        "tool_drain_timed_out": True,
    }


@pytest.mark.parametrize("task_count", [0, 2])
def test_judger_terminal_requires_exactly_one_running_task(task_count):
    controller, _, _, _ = _judged_reconciliation_controller(task_count=task_count)
    controller.observe_judger_terminal()

    with pytest.raises(ControllerShutdownError, match=f"found {task_count}"):
        controller.reconcile_judger_terminal()


def test_judger_success_does_not_publish_while_future_can_mutate_environment():
    release = threading.Event()
    running = threading.Event()

    def active_step():
        running.set()
        release.wait()
        return "done", "detail"

    controller, manager, _, task = _judged_reconciliation_controller()
    future = controller.executor.submit(active_step)
    assert running.wait(1)
    group = TaskExecutionGroup(
        task=task,
        agents=[SimpleNamespace(name="Alice")],
        futures={"Alice": future},
        started_at=time.time(),
        submission_complete=True,
    )
    controller.result_queue.append(group)
    controller.assignment["Alice"] = task.id
    controller.shutdown_grace_period = 0
    controller.cancellation_grace_period = 0
    controller.observe_judger_terminal()

    try:
        with pytest.raises(ControllerShutdownError, match="remained active"):
            controller.reconcile_judger_terminal()
    finally:
        release.set()
        future.result(timeout=1)
        controller.executor.shutdown(wait=True)

    snapshot = manager.runtime_task_store.snapshot()
    assert snapshot["nodes"][0]["lifecycle"]["status"] == Task.running
    assert snapshot["nodes"][0]["lifecycle"]["active_agents"] == ["Alice"]
    assert controller.shutdown_event.is_set() is False


def test_judger_terminal_uses_dedicated_natural_drain_grace():
    release = threading.Event()
    running = threading.Event()

    def active_step():
        running.set()
        release.wait()
        return "done", "detail"

    controller, _, _, task = _judged_reconciliation_controller()
    future = controller.executor.submit(active_step)
    assert running.wait(1)
    group = TaskExecutionGroup(
        task=task,
        agents=[SimpleNamespace(name="Alice")],
        futures={"Alice": future},
        started_at=time.time(),
        submission_complete=True,
    )
    controller.result_queue.append(group)
    controller.assignment["Alice"] = task.id
    controller.shutdown_grace_period = 0
    controller.judger_drain_grace_period = 1
    controller.observe_judger_terminal()

    try:
        assert controller.reconcile_judger_terminal() is False
        assert future.running() is True
    finally:
        release.set()
        future.result(timeout=1)
        controller.executor.shutdown(wait=True)


def test_non_cooperative_controller_thread_has_bounded_incomplete_shutdown():
    controller, _, sink = _controller()
    controller.shutdown_grace_period = 0.05
    release = threading.Event()
    failure = RuntimeError("ranking failed")

    def fail():
        raise failure

    controller.execute_tasks = fail
    controller.worker = release.wait
    controller.process_completed_tasks = controller.shutdown_event.wait

    started_at = time.monotonic()
    try:
        with pytest.raises(RuntimeError) as raised:
            controller.run()
    finally:
        release.set()
        for thread in controller._controller_threads:
            thread.join(1)

    assert raised.value is failure
    assert time.monotonic() - started_at < 1
    assert failure.controller_shutdown_context["shutdown_complete"] is False
    assert sink.events[0]["payload"]["shutdown_complete"] is False
    assert "controller-worker" in sink.events[0]["payload"]["live_threads"]


def test_active_future_is_interrupted_without_releasing_agent_for_reuse():
    controller, checkpoints, sink = _controller()
    controller.shutdown_grace_period = 0.05
    release = threading.Event()
    running = threading.Event()
    task = Task("Active task", {})
    task.status = Task.running
    agent = SimpleNamespace(name="Alice")

    def active_step():
        running.set()
        release.wait()

    future = controller.executor.submit(active_step)
    assert running.wait(1)
    group = TaskExecutionGroup(
        task=task,
        agents=[agent],
        futures={"Alice": future},
        started_at=time.time(),
        submission_complete=True,
    )
    controller.result_queue.append(group)
    controller.assignment["Alice"] = task.id
    failure = RuntimeError("controller failed")
    controller.execute_tasks = lambda: (_ for _ in ()).throw(failure)
    controller.worker = controller.shutdown_event.wait
    controller.process_completed_tasks = controller.shutdown_event.wait

    try:
        with pytest.raises(RuntimeError) as raised:
            controller.run()
    finally:
        release.set()
        for thread in controller.executor._threads:
            thread.join(1)

    assert raised.value is failure
    assert controller.assignment == {"Alice": task.id}
    assert controller.task_manager.status_updates == [(task.id, Task.failure, {
        "reason": "controller_shutdown",
        "execution_may_still_be_active": True,
        "assigned_agents": ["Alice"],
        "submitted_agents": ["Alice"],
        "active_agents": ["Alice"],
        "unsubmitted_agents": [],
        "submission_complete": True,
        "agent_reuse_blocked": True,
        "requires_agent_reconciliation": True,
    })]
    next_task = Task("Must not be reassigned", {})
    next_task.candidate_list = ["Alice"]
    controller.agent_list = [agent]
    controller.task_list = [next_task]
    assert controller.assign_runnable_tasks() == 0
    assert controller.result_queue == [group]
    assert checkpoints == ["checkpoint"]
    assert sink.events[0]["payload"]["shutdown_complete"] is False
    assert sink.events[0]["payload"]["active_task_ids"] == [task.id]


def test_queued_group_is_checkpointed_as_interrupted():
    controller, checkpoints, _ = _controller()
    task = Task("Queued task", {})
    task.status = Task.running
    controller.task_queue.append(TaskExecutionGroup(
        task=task,
        agents=[SimpleNamespace(name="Alice")],
    ))
    failure = RuntimeError("task ranking failed")
    controller.execute_tasks = lambda: (_ for _ in ()).throw(failure)
    controller.worker = controller.shutdown_event.wait
    controller.process_completed_tasks = controller.shutdown_event.wait

    with pytest.raises(RuntimeError) as raised:
        controller.run()

    assert raised.value is failure
    assert controller.task_manager.status_updates == [(task.id, Task.failure, {
        "reason": "controller_shutdown",
        "execution_may_still_be_active": False,
        "assigned_agents": ["Alice"],
        "submitted_agents": [],
        "active_agents": [],
        "unsubmitted_agents": ["Alice"],
        "submission_complete": False,
        "agent_reuse_blocked": True,
        "requires_agent_reconciliation": True,
    })]
    assert len(controller.task_queue) == 1
    assert checkpoints == ["checkpoint"]


def test_offline_agent_is_reported_as_run_failure():
    controller, _, sink = _controller()
    controller.env = SimpleNamespace(agents_ping=lambda: {"status": False})
    controller.execute_tasks = controller.shutdown_event.wait
    controller.process_completed_tasks = controller.shutdown_event.wait

    with pytest.raises(ControllerShutdownError, match="Some agents are offline"):
        controller.run()

    assert [event["event_type"] for event in sink.events] == ["run_failed"]
    assert sink.events[0]["payload"]["error"] == "Some agents are offline"


def test_result_processor_preserves_unprocessed_groups_on_shutdown():
    controller, _, _ = _controller()
    controller.env = SimpleNamespace(agents_ping=lambda: {"status": True})
    controller.query_interval = 0
    groups = [
        TaskExecutionGroup(Task(f"Task {index}", {}), [])
        for index in range(3)
    ]
    controller.result_queue = list(groups)

    def finalize(group):
        assert group is groups[0]
        controller._request_shutdown()
        return True

    controller.finalize_execution_group = finalize

    controller.process_completed_tasks()

    assert controller.result_queue == groups[1:]
    controller.executor.shutdown(wait=True)


def test_shutdown_finalization_never_reflects_completed_future():
    controller, _, _ = _controller()
    task = Task("Completed but unprocessed", {})
    future = controller.executor.submit(lambda: ("done", "detail"))
    future.result(timeout=1)
    reflected = []
    agent = SimpleNamespace(
        name="Alice",
        reflect=lambda *_args: reflected.append(True),
    )
    controller.result_queue.append(TaskExecutionGroup(
        task=task,
        agents=[agent],
        futures={"Alice": future},
        started_at=time.time(),
    ))
    failure = RuntimeError("controller failed")
    controller.execute_tasks = lambda: (_ for _ in ()).throw(failure)
    controller.worker = controller.shutdown_event.wait
    controller.process_completed_tasks = controller.shutdown_event.wait

    with pytest.raises(RuntimeError):
        controller.run()

    assert reflected == []
    assert controller.task_manager.status_updates[0][2]["reason"] == "controller_shutdown"


def test_group_remains_queued_when_interrupted_marking_fails():
    controller, _, sink = _controller()
    task = Task("Preserve me", {})
    group = TaskExecutionGroup(task, [SimpleNamespace(name="Alice")])
    controller.task_queue.append(group)
    controller.task_manager.mark_error = RuntimeError("task store unavailable")
    failure = RuntimeError("controller failed")
    controller.execute_tasks = lambda: (_ for _ in ()).throw(failure)
    controller.worker = controller.shutdown_event.wait
    controller.process_completed_tasks = controller.shutdown_event.wait

    with pytest.raises(RuntimeError) as raised:
        controller.run()

    assert raised.value is failure
    assert controller.task_queue == [group]
    assert group.completed is False
    assert sink.events[0]["payload"]["undrained_queues"] == ["task_queue"]


def test_group_remains_queued_when_final_checkpoint_fails():
    controller, _, sink = _controller()
    task = Task("Checkpoint me", {})
    group = TaskExecutionGroup(task, [SimpleNamespace(name="Alice")])
    controller.task_queue.append(group)
    controller.task_manager.checkpoint_error = RuntimeError("checkpoint unavailable")
    failure = RuntimeError("controller failed")
    controller.execute_tasks = lambda: (_ for _ in ()).throw(failure)
    controller.worker = controller.shutdown_event.wait
    controller.process_completed_tasks = controller.shutdown_event.wait

    with pytest.raises(RuntimeError) as raised:
        controller.run()

    assert raised.value is failure
    assert controller.task_queue == [group]
    assert sink.events[0]["payload"]["checkpoint_error"] == {
        "error": "checkpoint unavailable",
        "error_type": "RuntimeError",
    }


def test_worker_retains_group_when_shutdown_interrupts_second_submit():
    controller, _, sink = _controller()
    task = Task("Shared task", {})
    task.status = Task.running
    agents = [SimpleNamespace(name=name, step=lambda _task: None) for name in ("Alice", "Bob")]
    group = TaskExecutionGroup(task, agents)
    controller.task_queue.append(group)
    controller.assignment = {agent.name: task.id for agent in agents}
    controller.agent_list = agents
    controller.env = SimpleNamespace(agents_ping=lambda: {"status": True})
    controller.executor.shutdown(wait=True)
    controller.executor = _ShutdownDuringSecondSubmitExecutor(controller)
    controller.execute_tasks = controller.shutdown_event.wait
    controller.process_completed_tasks = controller.shutdown_event.wait

    with pytest.raises(RuntimeError, match="second submit failed"):
        controller.run()

    assert controller.task_queue == []
    assert controller.result_queue == [group]
    assert list(group.futures) == ["Alice"]
    assert group.submission_complete is False
    assert controller.assignment == {"Alice": task.id, "Bob": task.id}
    assert controller.task_manager.status_updates[0][2] == {
        "reason": "controller_shutdown",
        "execution_may_still_be_active": True,
        "assigned_agents": ["Alice", "Bob"],
        "submitted_agents": ["Alice"],
        "active_agents": ["Alice"],
        "unsubmitted_agents": ["Bob"],
        "submission_complete": False,
        "agent_reuse_blocked": True,
        "requires_agent_reconciliation": True,
    }
    assert sink.events[0]["payload"]["active_task_ids"] == [task.id]
    assert sink.events[0]["payload"]["active_agent_ids"] == ["Alice"]
    assert sink.events[0]["payload"]["incomplete_submission_task_ids"] == [task.id]

    next_task = Task("Do not reuse agents", {})
    next_task.candidate_list = ["Alice", "Bob"]
    next_task.number = 1
    controller.task_list = [next_task]
    assert controller.assign_runnable_tasks() == 0


def test_terminal_checkpoint_failure_surfaces_through_real_task_manager():
    controller, _, sink = _controller()
    manager = TaskManager(silent=True, event_sink=sink)
    checkpoint_error = RuntimeError("terminal checkpoint failed")
    manager.runtime_checkpoint = lambda: (_ for _ in ()).throw(checkpoint_error)
    controller.task_manager = manager
    controller.execute_tasks = controller._request_shutdown
    controller.worker = controller.shutdown_event.wait
    controller.process_completed_tasks = controller.shutdown_event.wait

    manager.checkpoint_runtime_state()
    with pytest.raises(RuntimeError) as raised:
        controller.run()

    assert raised.value is checkpoint_error
    assert [event["event_type"] for event in sink.events] == ["run_failed"]
    assert sink.events[0]["payload"]["thread"] == "run.checkpoint"


def test_non_cooperative_timeout_run_checkpoints_running_lifecycle():
    controller, _, sink = _controller()
    controller.shutdown_grace_period = 0.05
    controller.cancellation_grace_period = 0
    controller.max_task_time = 0
    controller.query_interval = 0
    controller.env = SimpleNamespace(agents_ping=lambda: {"status": True})
    release = threading.Event()
    running = threading.Event()
    task = Task("Non-cooperative timeout", {})
    task.candidate_list = ["Alice"]
    task.number = 1
    manager = TaskManager(silent=True, event_sink=sink)
    manager.set_task_list_from_decomposition([task])
    projected_task = manager.query_runnable_subtasks(["Alice"])[0]
    manager.mark_task_running(projected_task, ["Alice"])
    checkpoints = []
    manager.runtime_checkpoint = lambda: checkpoints.append(
        manager.runtime_task_store.snapshot()
    )
    controller.task_manager = manager
    agent = SimpleNamespace(name="Alice")

    def active_step():
        running.set()
        release.wait()
        return "done", "detail"

    future = controller.executor.submit(active_step)
    assert running.wait(1)
    group = TaskExecutionGroup(
        task=projected_task,
        agents=[agent],
        futures={"Alice": future},
        started_at=time.time() - 1,
        submission_complete=True,
    )
    controller.result_queue.append(group)
    controller.assignment["Alice"] = projected_task.id
    controller.execute_tasks = controller.shutdown_event.wait
    controller.worker = controller.shutdown_event.wait

    try:
        with pytest.raises(ControllerShutdownError, match="remained active after timeout"):
            controller.run()
    finally:
        release.set()
        for thread in controller.executor._threads:
            thread.join(1)

    node = checkpoints[-1]["nodes"][0]
    assert node["lifecycle"]["status"] == Task.running
    assert node["lifecycle"]["active_agents"] == ["Alice"]
    assert node["lifecycle"]["last_assigned_agents"] == ["Alice"]
    assert node["content"]["reflect"] == {
        "reason": "task_timeout_shutdown_escalation",
        "execution_may_still_be_active": True,
        "assigned_agents": ["Alice"],
        "submitted_agents": ["Alice"],
        "active_agents": ["Alice"],
        "unsubmitted_agents": [],
        "submission_complete": True,
        "agent_reuse_blocked": True,
        "requires_agent_reconciliation": True,
        "timeout_detected": ["Alice"],
        "shutdown_escalated": ["Alice"],
        "cancellation_requested": [],
        "cancellation_acknowledged": [],
        "cancellation_forced": [],
        "timeout_details": {
            "Alice": {
                "status": "timeout",
                "error": f"Task {projected_task.description} timeout for agent Alice",
                "cooperative_cancellation": False,
                "timeout_detected": True,
                "shutdown_escalated": True,
                "cancellation_requested": False,
                "cancellation_acknowledged": False,
                "cancellation_forced": False,
            },
        },
    }
    assert controller.assignment == {"Alice": projected_task.id}
    assert group.terminal_state_persisted is False
    assert group.completed is False
    assert sink.events[-1]["event_type"] == "run_failed"


def _judged_reconciliation_controller(status="success", task_count=1):
    controller, _, sink = _controller()
    manager = TaskManager(silent=True, event_sink=sink)
    tasks = []
    for index in range(task_count):
        task = Task(f"Judged task {index}", {})
        task.candidate_list = ["Alice"]
        task.number = 1
        tasks.append(task)
    manager.set_task_list_from_decomposition(tasks)
    projected_tasks = list(manager.graph.vertex)
    for task in projected_tasks:
        manager.mark_task_running(task, ["Alice"])
    sink.events.clear()
    controller.task_manager = manager
    controller.env = SimpleNamespace(
        attempt_id="attempt-a",
        task_name="runtime-task-a",
        is_task_complete=lambda: True,
        get_score=lambda: {
            "attempt_id": "attempt-a",
            "task_name": "runtime-task-a",
            "status": status,
            "score": 100 if status == "success" else 0,
            "progress": 100 if status == "success" else 0,
        },
        stop=lambda: None,
    )
    return controller, manager, sink, projected_tasks[0] if projected_tasks else None


def _controller():
    controller = object.__new__(GlobalController)
    controller.logger = logging.getLogger("test-controller-shutdown")
    controller.shutdown_event = threading.Event()
    controller._failure_lock = threading.Lock()
    controller._first_failure = None
    controller._controller_threads = []
    controller._run_started = False
    controller._execution_state_lock = threading.RLock()
    controller._tool_action_condition = threading.Condition(controller._execution_state_lock)
    controller._active_tool_actions = 0
    controller._judger_terminal_pending = False
    controller._judger_terminal_observed = False
    controller._judger_terminal_payload = None
    controller._judger_terminal_detected_at = None
    controller._judger_terminal_observed_at = None
    controller._tool_drain_timed_out = False
    controller.judger_tool_drain_grace_period = 0.2
    controller._judger_terminal_reconciled = False
    controller.controller_state = GlobalController.STATE_RUNNING
    controller.shutdown_complete = False
    controller.shutdown_context = None
    controller.shutdown_grace_period = 0.2
    controller.executor = ThreadPoolExecutor(max_workers=1)
    controller.executor.submit(lambda: None).result(timeout=2)
    controller.task_queue = []
    controller.result_queue = []
    controller.task_list_lock = threading.Lock()
    controller.result_list_lock = threading.Lock()
    controller.assignment = {}
    checkpoints = []
    controller.task_manager = _TaskManagerStub(checkpoints)
    sink = InMemoryRuntimeEventRecorder("controller-test")
    controller.event_sink = sink
    controller.emit_terminal_events = True
    return controller, checkpoints, sink


class _TaskManagerStub:
    def __init__(self, checkpoints):
        self.checkpoints = checkpoints
        self.status_updates = []
        self.mark_error = None
        self.checkpoint_error = None

    def mark_task_status(self, task_id, status, feedback):
        if self.mark_error is not None:
            raise self.mark_error
        self.status_updates.append((task_id, status, feedback))

    def checkpoint_runtime_state(self, *, raise_on_error=False):
        if self.checkpoint_error is not None:
            if raise_on_error:
                raise self.checkpoint_error
            return
        self.checkpoints.append("checkpoint")


class _ShutdownDuringSecondSubmitExecutor:
    def __init__(self, controller):
        self.controller = controller
        self.submit_count = 0
        self._threads = set()

    def submit(self, _fn, _task):
        self.submit_count += 1
        if self.submit_count == 2:
            self.controller._request_shutdown()
            raise RuntimeError("second submit failed")
        future = Future()
        future.set_running_or_notify_cancel()
        return future

    def shutdown(self, wait=True, cancel_futures=False):
        return None
