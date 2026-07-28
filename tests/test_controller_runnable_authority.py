import logging
import threading
from types import SimpleNamespace

import pytest

from pipeline.controller_tiny import GlobalController
from pipeline.task_manager import TaskManager
from type_define.graph import Task


def test_controller_final_available_check_does_not_recompute_store_authority():
    controller = object.__new__(GlobalController)
    runnable = Task("Runnable", {})
    runnable.available = True
    runnable.status = Task.unknown
    blocked = Task("Blocked", {})
    blocked.available = False
    blocked.status = Task.unknown
    running = Task("Running", {})
    running.available = True
    running.status = Task.running
    controller.task_list = [runnable, blocked, running]

    assert controller.check_task_list_available() == [runnable]


@pytest.mark.parametrize(
    ("candidates", "required", "expected_agents"),
    [
        (["Alice", "Bob"], 1, ["Alice"]),
        (["Alice", "Bob"], 2, ["Alice", "Bob"]),
        (["Alice"], 2, []),
    ],
)
def test_controller_assigns_exact_required_count(candidates, required, expected_agents):
    controller = _controller(["Alice", "Bob"])
    controller.task_list = [_task("A", candidates, required)]

    controller.assign_runnable_tasks()

    assert list(controller.assignment) == expected_agents
    assert controller.task_manager.running == (
        [("A", expected_agents)] if expected_agents else []
    )


def test_controller_assigns_free_agent_while_another_agent_is_busy():
    controller = _controller(["Alice", "Bob"])
    controller.assignment["Alice"] = "running-task"
    controller.task_list = [_task("B", ["Bob"], 1)]

    controller.assign_runnable_tasks()

    assert controller.assignment == {"Alice": "running-task", "Bob": controller.task_list[0].id}
    assert controller.task_manager.running == [("B", ["Bob"])]


def test_controller_assigns_multiple_independent_tasks_in_one_iteration():
    controller = _controller(["Alice", "Bob"])
    controller.task_list = [
        _task("A", ["Alice"], 1),
        _task("B", ["Bob"], 1),
    ]

    assigned_count = controller.assign_runnable_tasks()

    assert assigned_count == 2
    assert controller.task_manager.running == [("A", ["Alice"]), ("B", ["Bob"])]


def test_controller_does_not_assign_one_agent_to_multiple_tasks():
    controller = _controller(["Alice", "Bob"])
    controller.task_list = [
        _task("A", ["Alice"], 1),
        _task("B", ["Alice"], 1),
    ]

    controller.assign_runnable_tasks()

    assert controller.task_manager.running == [("A", ["Alice"])]


def test_controller_assigns_empty_candidate_task_from_store_projected_free_agents():
    manager = TaskManager(silent=True)
    task = _task("A", [], 1)
    manager.set_task_list_from_decomposition([task])
    controller = _controller(["Alice", "Bob"])
    controller.task_manager = manager
    controller.task_list = manager.query_runnable_subtasks(["Alice", "Bob"])

    controller.assign_runnable_tasks()

    assert controller.task_list[0].candidate_list == ["Alice", "Bob"]
    node = manager.runtime_task_store.nodes[manager.runtime_task_store.task_node_id(task)]
    assert node["lifecycle"]["active_agents"] == ["Alice"]


def test_controller_rejects_explicit_empty_candidate_task():
    controller = _controller(["Alice"])
    task = _task("A", [], 1)
    task._candidate_agents_explicit = True
    controller.task_list = [task]

    with pytest.raises(ValueError, match="explicit empty candidate"):
        controller.assign_runnable_tasks()

    assert controller.assignment == {}


def test_validate_assignments_rejects_incomplete_and_duplicate_agent_sets():
    controller = _controller(["Alice", "Bob"])
    controller.task_list = [_task("A", ["Alice", "Bob"], 2)]

    incomplete = controller.validate_assignments([{"task_id": 0, "agent": ["Alice"]}])
    duplicate = controller.validate_assignments([{"task_id": 0, "agent": ["Alice", "Alice"]}])

    assert incomplete == []
    assert duplicate == []


def test_validate_assignments_reserves_agents_across_batch():
    controller = _controller(["Alice"])
    controller.task_list = [
        _task("A", ["Alice"], 1),
        _task("B", ["Alice"], 1),
    ]

    validated = controller.validate_assignments([
        {"task_id": 0, "agent": ["Alice"]},
        {"task_id": 1, "agent": ["Alice"]},
    ])

    assert [assignment["task_instance"].description for assignment in validated] == ["A"]


class _TaskManagerStub:
    def __init__(self):
        self.running = []

    def mark_task_running(self, task, agent_names):
        self.running.append((task.description, list(agent_names)))


def _controller(agent_names):
    controller = object.__new__(GlobalController)
    controller.agent_list = [SimpleNamespace(name=name) for name in agent_names]
    controller.assignment = {}
    controller.task_list = []
    controller.task_queue = []
    controller.task_list_lock = threading.Lock()
    controller._execution_state_lock = threading.RLock()
    controller._judger_terminal_observed = False
    controller.shutdown_event = threading.Event()
    controller.task_manager = _TaskManagerStub()
    controller.logger = logging.getLogger("test-controller-runnable-authority")
    return controller


def _task(description, candidates, required):
    task = Task(description, {})
    task.candidate_list = list(candidates)
    task.number = required
    task.available = True
    task.status = Task.unknown
    return task
