import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from pipeline.controller_tiny import ControllerShutdownError, GlobalController, TaskExecutionGroup
from pipeline.runtime_events import InMemoryRuntimeEventRecorder
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
    controller.result_queue.append(TaskExecutionGroup(
        task=task,
        agents=[agent],
        futures={"Alice": future},
        started_at=time.time(),
    ))
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
    })]
    assert checkpoints == ["checkpoint"]
    assert sink.events[0]["payload"]["shutdown_complete"] is False


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
    })]
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

    def mark_task_status(self, task_id, status, feedback):
        self.status_updates.append((task_id, status, feedback))

    def checkpoint_runtime_state(self):
        self.checkpoints.append("checkpoint")
