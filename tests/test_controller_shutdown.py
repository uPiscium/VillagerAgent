import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from pipeline.controller_tiny import ControllerShutdownError, GlobalController, TaskExecutionGroup
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


def _controller():
    controller = object.__new__(GlobalController)
    controller.logger = logging.getLogger("test-controller-shutdown")
    controller.shutdown_event = threading.Event()
    controller._failure_lock = threading.Lock()
    controller._first_failure = None
    controller._controller_threads = []
    controller._run_started = False
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
