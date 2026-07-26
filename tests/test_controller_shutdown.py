import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from pipeline.controller_tiny import GlobalController
from pipeline.runtime_events import InMemoryRuntimeEventRecorder


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


def _controller():
    controller = object.__new__(GlobalController)
    controller.logger = logging.getLogger("test-controller-shutdown")
    controller.shutdown_event = threading.Event()
    controller._failure_lock = threading.Lock()
    controller._first_failure = None
    controller._controller_threads = []
    controller.executor = ThreadPoolExecutor(max_workers=1)
    controller.executor.submit(lambda: None).result(timeout=2)
    checkpoints = []
    controller.task_manager = type("TaskManagerStub", (), {
        "checkpoint_runtime_state": lambda self: checkpoints.append("checkpoint"),
    })()
    sink = InMemoryRuntimeEventRecorder("controller-test")
    controller.event_sink = sink
    return controller, checkpoints, sink
