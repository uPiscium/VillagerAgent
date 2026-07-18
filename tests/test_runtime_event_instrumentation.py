import json
import logging
import threading
from pathlib import Path
from types import SimpleNamespace

from benchmarks.minecraft.experiment import run_minecraft_experiment
from pipeline.controller_tiny import GlobalController
from pipeline.runtime_events import InMemoryRuntimeEventRecorder
from pipeline.task_manager import TaskManager
from type_define.graph import Task


class TaskManagerStub:
    def __init__(self):
        self.running = []

    def mark_task_running(self, task, agents):
        self.running.append((task.id, list(agents)))


def controller_with_sink(sink):
    controller = object.__new__(GlobalController)
    controller.agent_list = [SimpleNamespace(name="Alice")]
    controller.assignment = {}
    controller.task_list = []
    controller.task_queue = []
    controller.task_list_lock = threading.Lock()
    controller.task_manager = TaskManagerStub()
    controller.logger = logging.getLogger("event-instrumentation-test")
    controller.event_sink = sink
    controller.minecraft_dual_dag_config = {"task_selection_policy": "dual-dag"}
    return controller


def task(description="Collect wood"):
    value = Task(description, {})
    value.candidate_list = ["Alice"]
    value.number = 1
    value.available = True
    value.status = Task.unknown
    return value


def test_task_manager_emits_graph_and_sanitized_authoritative_status_events() -> None:
    sink = InMemoryRuntimeEventRecorder("run")
    manager = TaskManager(silent=True, event_sink=sink)
    selected = task()

    manager.set_task_list_from_decomposition([selected])
    manager.mark_task_running(selected, ["Alice"])
    manager.mark_task_status(selected.id, Task.failure, feedback={"token": "secret", "summary": "blocked"})

    assert [event["event_type"] for event in sink.events] == ["task_graph_snapshot", "task_status_changed", "task_status_changed"]
    assert sink.events[1]["payload"]["status"] == Task.running
    assert sink.events[2]["payload"]["feedback"] == {"token": "[REDACTED]", "summary": "blocked"}


def test_controller_records_only_validated_selection_and_complete_assignment_group() -> None:
    sink = InMemoryRuntimeEventRecorder("run")
    controller = controller_with_sink(sink)
    selected = task()
    controller.task_list = [selected]

    assert controller.assign_runnable_tasks() == 1

    assert [event["event_type"] for event in sink.events] == ["task_selected", "task_assigned"]
    assert sink.events[0]["entity_id"] == selected.id
    assert sink.events[1]["payload"]["agents"] == ["Alice"]
    assert controller.task_manager.running == [(selected.id, ["Alice"])]


def test_sink_exception_does_not_change_assignment_result() -> None:
    class FailingSink:
        def emit(self, *args, **kwargs):
            raise RuntimeError("event storage failed")

    controller = controller_with_sink(FailingSink())
    controller.task_list = [task()]

    assert controller.assign_runnable_tasks() == 1
    assert list(controller.assignment) == ["Alice"]


def test_experiment_emits_run_lifecycle_without_extra_runtime_work(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "task_type": "meta",
        "task_idx": 0,
        "agent_num": 1,
        "task_goal": "Find the bell",
        "host": "127.0.0.1",
        "port": 25565,
        "task_name": "event-test",
    }), encoding="utf-8")
    sink = InMemoryRuntimeEventRecorder("event-test")

    summary = run_minecraft_experiment(config_path=config, output_root=tmp_path / "result", event_sink=sink)

    assert summary["error"] is None
    assert [event["event_type"] for event in sink.events] == ["run_started", "run_completed"]
