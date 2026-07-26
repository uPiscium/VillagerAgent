from copy import deepcopy
from types import SimpleNamespace

import pytest

import pipeline.task_manager as task_manager_module
from pipeline.dual_dag_task_store import TaskDependencyError
from pipeline.task_manager import TaskManager
from type_define.graph import Graph, GraphState, Task


def test_task_manager_uses_dual_dag_store_as_task_source_of_truth():
    task_a = Task("A", {})
    task_b = Task("B", {})
    manager = TaskManager(silent=True)

    manager.set_task_list_from_decomposition([task_a, task_b])

    assert manager.runtime_task_store.snapshot()["source_of_truth"] == "runtime_task_dag"
    assert [(start.description, end.description) for start, end in manager.graph.edge] == [("A", "B")]

    manager.mark_task_running(task_a, ["Alice"])

    node = manager.dual_dag_store.nodes[manager.dual_dag_store.task_node_id(task_a)]
    assert node["lifecycle"]["status"] == Task.running
    assert node["lifecycle"]["active_agents"] == ["Alice"]
    assert node["lifecycle"]["last_assigned_agents"] == ["Alice"]
    assert manager.graph.vertex[0].status == Task.running
    assert manager.graph.vertex[0]._agent == ["Alice"]


def test_task_manager_query_subtasks_reads_dual_dag_state():
    task_a = Task("A", {})
    task_b = Task("B", {})
    manager = TaskManager(silent=True)
    manager.set_task_list_from_decomposition([task_a, task_b])

    open_tasks = {task.description: task for task in manager.query_subtask_list()}
    assert [task.description for task in open_tasks["B"]._direct_pre_task_list] == ["A"]

    manager.mark_task_status(task_a.id, Task.success, {"ok": True})
    open_tasks = {task.description: task for task in manager.query_subtask_list()}

    assert "A" not in open_tasks
    assert open_tasks["B"].predecessor_task_list == []
    assert manager.dual_dag_store.terminal_state() == GraphState.RUNNING


def test_task_manager_query_runnable_subtasks_delegates_to_runtime_task_store():
    task_a = Task("A", {})
    task_a.candidate_list = ["Alice"]
    task_b = Task("B", {})
    task_b.candidate_list = ["Bob"]
    manager = TaskManager(silent=True)
    manager.set_task_list_from_decomposition([task_a, task_b])

    assert [task.description for task in manager.query_runnable_subtasks(["Bob"])] == []
    assert [task.description for task in manager.query_runnable_subtasks(["Alice"])] == ["A"]

    manager.mark_task_status(task_a.id, Task.success, {"ok": True})

    assert [task.description for task in manager.query_runnable_subtasks(["Bob"])] == ["B"]


def test_task_manager_status_updates_write_dual_dag_before_projection():
    task = Task("A", {})
    manager = TaskManager(silent=True)
    manager.set_task_list_from_decomposition([task])

    manager.mark_task_status(task.id, Task.failure, "failed")

    node = manager.dual_dag_store.nodes[manager.dual_dag_store.task_node_id(task)]
    assert node["lifecycle"]["status"] == Task.failure
    assert node["content"]["reflect"] == "failed"
    assert manager.graph.vertex[0].status == Task.failure
    assert manager.graph.vertex[0].reflect == "failed"
    assert manager.dual_dag_store.terminal_state() == GraphState.FAILURE


def test_task_manager_checkpoints_decomposition_and_lifecycle_transitions():
    task = Task("A", {})
    manager = TaskManager(silent=True)
    snapshots = []
    manager.runtime_checkpoint = lambda: snapshots.append(manager.runtime_task_store.snapshot())

    manager.set_task_list_from_decomposition([task])
    manager.mark_task_running(task, ["Alice"])
    manager.mark_task_status(task.id, Task.success, {"ok": True})

    assert [snapshot["nodes"][0]["lifecycle"]["status"] for snapshot in snapshots] == [
        Task.unknown,
        Task.running,
        Task.success,
    ]


def test_task_manager_replan_edits_store_first_and_preserves_history(monkeypatch):
    task = Task("A", {"old": True})
    manager = TaskManager(silent=True, method="merge")
    manager.set_task_list_from_decomposition([task])
    manager.mark_task_running(task, ["Alice"])
    manager.mark_task_status(task.id, Task.failure, "failed detail")
    manager.get_graph_strategy = lambda _task: {
        "strategy": "replan",
        "origin-id": 1,
        "description": "Replanned A",
        "milestones": ["retry"],
    }
    manager.sync_dual_dag_from_graph = lambda: (_ for _ in ()).throw(
        AssertionError("Graph must not be reloaded into the canonical store")
    )
    monkeypatch.setattr(Graph, "write_graph_to_md", lambda *args, **kwargs: None)
    monkeypatch.setattr(Graph, "write_graph_to_json", lambda *args, **kwargs: None)

    manager.merge_task(manager.graph.vertex[0])

    node = manager.runtime_task_store.snapshot()["nodes"][0]
    assert node["content"]["description"] == "Replanned A"
    assert node["content"]["reflect"] == "failed detail"
    assert node["lifecycle"]["last_assigned_agents"] == ["Alice"]
    assert node["lifecycle"]["status"] == Task.unknown
    assert manager.graph.vertex[0].id == task.id
    assert manager.graph.vertex[0].description == "Replanned A"


def test_initial_decomposition_preserves_complete_multi_agent_assignment(monkeypatch):
    result = [_decomposition_task("A", ["Alice", "Bob"], [])]
    manager = _decomposition_manager(monkeypatch, result)

    manager.init_task("parent", {})

    node = manager.runtime_task_store.snapshot()["nodes"][0]
    assert len(manager.graph.vertex) == 1
    assert node["lifecycle"]["candidate_agents"] == ["Alice", "Bob"]
    assert node["lifecycle"]["required_agent_count"] == 2
    assert manager.graph.vertex[0].candidate_list == ["Alice", "Bob"]
    assert manager.graph.vertex[0].number == 2


def test_initial_decomposition_recovers_idle_status_after_dependency_error(monkeypatch):
    result = [
        _decomposition_task("A", ["Alice"], [2]),
        _decomposition_task("B", ["Bob"], [1]),
    ]
    manager = _decomposition_manager(monkeypatch, result)

    with pytest.raises(TaskDependencyError, match="cycle"):
        manager.init_task("parent", {})

    assert manager.status == TaskManager.idle


def test_fill_agents_deduplicates_in_order_and_rejects_unknown_names():
    manager = TaskManager(silent=True)
    agents = [SimpleNamespace(name="Alice"), SimpleNamespace(name="Bob")]
    result = [_decomposition_task("A", ["Bob", "Alice", "Bob"], [])]

    assert manager.fill_agents(result, agents)[0]["assigned agents"] == ["Bob", "Alice"]

    with pytest.raises(ValueError, match="Unknown assigned agent 'Ghost'.*task 'B'"):
        manager.fill_agents([_decomposition_task("B", ["Ghost"], [])], agents)


def test_redecomposition_preserves_chains_branches_parallel_roots_and_projections(monkeypatch):
    result = [
        _decomposition_task("A", ["Alice"], []),
        _decomposition_task("B", ["Bob"], [1]),
        _decomposition_task("C", ["Alice"], [2]),
        _decomposition_task("D", ["Bob"], [1]),
        _decomposition_task("E", ["Alice"], []),
    ]
    manager = _decomposition_manager(monkeypatch, result)
    manager.task_description = "parent"
    manager.task_document = {}

    manager.update_task(Task("failed", {}))

    expected_edges = [("A", "B"), ("B", "C"), ("A", "D")]
    store_edges = [
        (start.description, end.description)
        for start, end in manager.runtime_task_store.to_task_graph_projection().edge
    ]
    compatibility_edges = [(start.description, end.description) for start, end in manager.graph.edge]
    assert store_edges == expected_edges
    assert compatibility_edges == expected_edges
    assert [task.description for task in manager.graph.get_entry_node()] == ["A", "E"]


@pytest.mark.parametrize(
    ("required_subtasks", "message"),
    [
        ([[2], [1]], "cycle"),
        ([[], [3]], "only 2 tasks exist"),
        ([[], [2]], "self-loop"),
    ],
)
def test_redecomposition_fails_explicitly_for_invalid_dependencies(
    monkeypatch, required_subtasks, message
):
    result = [
        _decomposition_task("A", ["Alice"], required_subtasks[0]),
        _decomposition_task("B", ["Bob"], required_subtasks[1]),
    ]
    manager = _decomposition_manager(monkeypatch, result)
    manager.task_description = "parent"
    manager.task_document = {}

    with pytest.raises(TaskDependencyError, match=message):
        manager.update_task(Task("failed", {}))
    assert manager.status == TaskManager.idle


def _decomposition_task(description, assigned_agents, required_subtasks):
    return {
        "description": description,
        "milestones": [],
        "assigned agents": assigned_agents,
        "required subtasks": required_subtasks,
        "retrieval paths": [],
    }


def _decomposition_manager(monkeypatch, result):
    manager = TaskManager(silent=True)
    manager.agent_list = [SimpleNamespace(name="Alice"), SimpleNamespace(name="Bob")]
    manager.dm = SimpleNamespace(
        query_env_with_task=lambda *_args, **_kwargs: "environment",
        query_history=lambda *_args, **_kwargs: [],
    )
    manager.llm = SimpleNamespace(few_shot_generate_thoughts=lambda *_args, **_kwargs: "response")
    manager.update_history = lambda *_args, **_kwargs: None
    monkeypatch.setattr(task_manager_module, "extract_info", lambda *_args, **_kwargs: deepcopy(result))
    monkeypatch.setattr(Graph, "write_graph_to_md", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Graph, "write_graph_to_json", lambda *_args, **_kwargs: None)
    return manager
