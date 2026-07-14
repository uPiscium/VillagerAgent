from types import SimpleNamespace

import pytest

import env.env as env_module
from env.env import VillagerBench
from pipeline.task_manager import TaskManager
from pipeline.utils import dict2document
from type_define.graph import Graph, Task


def test_graph_status_rejects_unsupported_task_status():
    task = Task("invalid task", {})
    task.status = "cancelled"
    graph = Graph()
    graph.add_node(task)

    with pytest.raises(ValueError, match="unsupported status 'cancelled'"):
        graph.get_graph_status_with_id()


def test_graph_common_parents_requires_parents_on_both_nodes():
    task_a = Task("A", {})
    task_b = Task("B", {})

    with pytest.raises(ValueError, match="both nodes must have at least one parent"):
        Graph.get_co_parent_list(task_a, task_b)


def test_task_manager_honors_method_argument():
    manager = TaskManager(silent=True, method="merge")

    assert manager.method == "merge"
    assert manager.manage_method == "merge"


def test_task_manager_rejects_unsupported_method():
    with pytest.raises(ValueError, match="Unsupported task manager method 'replace'"):
        TaskManager(silent=True, method="replace")


def test_environment_initial_state_requires_running_environment():
    environment = object.__new__(VillagerBench)
    environment.running = False
    environment._virtual_debug = False

    with pytest.raises(RuntimeError, match=r"call '\.launch\(\)' first"):
        environment.get_init_state()


def test_environment_reset_requires_running_environment():
    environment = object.__new__(VillagerBench)
    environment.running = False
    environment._virtual_debug = False
    environment.logger = SimpleNamespace(info=lambda *_: None)
    environment.agent_pool = []

    with pytest.raises(RuntimeError, match=r"call '\.launch\(\)' before '\.reset\(\)'"):
        environment.reset()


def test_environment_reset_rejects_unsupported_type(monkeypatch):
    environment = object.__new__(VillagerBench)
    environment.running = True
    environment._virtual_debug = False
    environment.logger = SimpleNamespace(info=lambda *_: None)
    environment.agent_pool = []
    environment.env_type = 999
    monkeypatch.setattr(env_module.os.path, "exists", lambda *_: False)

    with pytest.raises(ValueError, match="Unsupported environment type: 999"):
        environment.reset()


def test_dict2document_rejects_unsupported_database():
    with pytest.raises(ValueError, match="Unsupported database name 'unknown'"):
        dict2document({}, "unknown")
