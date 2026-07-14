from pipeline.task_manager import TaskManager
from type_define.graph import GraphState, Task


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
