import pytest

from pipeline.dual_dag_task_store import DualDAGTaskStore, RuntimeTaskDAGStore, TaskDependencyError
from type_define.graph import GraphState, Task


def test_store_fallback_connects_unspecified_tasks_sequentially():
    task_a = Task("A", {})
    task_b = Task("B", {})

    store = RuntimeTaskDAGStore()
    store.load_tasks_from_decomposition([task_a, task_b])

    assert _edge_descriptions(store) == [("A", "B")]


def test_store_fallback_connects_after_previous_task_not_previous_predecessors():
    task_a = Task("A", {})
    task_b = Task("B", {})
    task_b._pre_idxs = [1]
    task_c = Task("C", {})

    store = RuntimeTaskDAGStore()
    store.load_tasks_from_decomposition([task_a, task_b, task_c])

    assert _edge_descriptions(store) == [("A", "B"), ("B", "C")]


def test_store_preserves_explicit_parallel_dependencies():
    task_a = Task("A", {})
    task_b = Task("B", {})
    task_b._pre_idxs = [1]
    task_c = Task("C", {})
    task_c._pre_idxs = [1]

    store = RuntimeTaskDAGStore()
    store.load_tasks_from_decomposition([task_a, task_b, task_c])

    assert _edge_descriptions(store) == [("A", "B"), ("A", "C")]


def test_store_open_tasks_distinguishes_direct_and_transitive_predecessors():
    task_a = Task("A", {})
    task_b = Task("B", {})
    task_c = Task("C", {})

    store = RuntimeTaskDAGStore()
    store.load_tasks_from_decomposition([task_a, task_b, task_c])


    open_tasks = {task.description: task for task in store.query_open_tasks()}

    assert [task.description for task in open_tasks["C"]._direct_pre_task_list] == ["B"]
    assert [task.description for task in open_tasks["C"].predecessor_task_list] == ["B", "A"]


def test_store_query_runnable_tasks_uses_dual_dag_lifecycle_state():
    task_a = Task("A", {})
    task_a.candidate_list = ["Alice"]
    task_b = Task("B", {})
    task_b.candidate_list = ["Alice"]

    store = RuntimeTaskDAGStore()
    store.load_tasks_from_decomposition([task_a, task_b])

    assert [task.description for task in store.query_runnable_tasks(["Alice"])] == ["A"]

    store.mark_task_success(task_a.id, feedback={"ok": True})

    assert [task.description for task in store.query_runnable_tasks(["Alice"])] == ["B"]


def test_store_terminal_state_matches_graph_semantics():
    store, tasks = _store_with_chain(2)

    assert store.terminal_state() == GraphState.RUNNING

    store.mark_task_running(tasks[0].id, assigned_agents=["Alice"])
    assert store.terminal_state() == GraphState.RUNNING

    store.mark_task_success(tasks[0].id)
    store.mark_task_success(tasks[1].id)
    assert store.terminal_state() == GraphState.SUCCESS


def test_store_terminal_state_failure_and_blocked():
    store, tasks = _store_with_chain(2)
    store.mark_task_failure(tasks[0].id, feedback="failed")
    assert store.terminal_state() == GraphState.FAILURE

    blocked_store, blocked_tasks = _store_with_chain(2)
    blocked_store.nodes[blocked_store.task_node_id(blocked_tasks[0])]["lifecycle"]["status"] = "cancelled"
    assert blocked_store.terminal_state() == GraphState.BLOCKED


def test_store_task_graph_projection_reflects_canonical_dual_dag_state():
    store, tasks = _store_with_chain(2)
    store.mark_task_running(tasks[0].id, assigned_agents=["Alice"])

    graph = store.to_task_graph_projection()

    assert [task.description for task in graph.vertex] == ["A", "B"]
    assert [(start.description, end.description) for start, end in graph.edge] == [("A", "B")]
    assert graph.vertex[0].status == Task.running
    assert graph.vertex[0]._agent == ["Alice"]


def test_store_lifecycle_tracks_active_and_last_assigned_agents():
    store, tasks = _store_with_chain(1)
    node_id = store.task_node_id(tasks[0])

    assert store.nodes[node_id]["lifecycle"]["active_agents"] == []
    assert store.nodes[node_id]["lifecycle"]["last_assigned_agents"] == []

    store.mark_task_running(tasks[0].id, assigned_agents=["Alice"])
    assert store.nodes[node_id]["lifecycle"]["active_agents"] == ["Alice"]
    assert store.nodes[node_id]["lifecycle"]["last_assigned_agents"] == ["Alice"]

    store.mark_task_success(tasks[0].id, feedback={"ok": True})
    assert store.nodes[node_id]["lifecycle"]["active_agents"] == []
    assert store.nodes[node_id]["lifecycle"]["last_assigned_agents"] == ["Alice"]
    assert store.nodes[node_id]["content"]["reflect"] == {"ok": True}


def test_store_lifecycle_clears_active_agents_on_failure():
    store, tasks = _store_with_chain(1)
    node_id = store.task_node_id(tasks[0])

    store.mark_task_running(tasks[0].id, assigned_agents=["Alice"])
    store.mark_task_failure(tasks[0].id, feedback="failed")

    assert store.nodes[node_id]["lifecycle"]["active_agents"] == []
    assert store.nodes[node_id]["lifecycle"]["last_assigned_agents"] == ["Alice"]
    assert store.nodes[node_id]["content"]["reflect"] == "failed"


def test_store_snapshot_is_canonical_dual_dag_artifact():
    store, _ = _store_with_chain(1)

    snapshot = store.snapshot()

    assert snapshot["runtime"] == "runtime_task_dag_store"
    assert snapshot["source_of_truth"] == "runtime_task_dag"
    assert snapshot["summary"]["task_node_count"] == 1
    assert snapshot["nodes"][0]["node_type"] == "runtime_task"
    assert "available" not in snapshot["nodes"][0]["lifecycle"]
    assert snapshot["nodes"][0]["derived"] == {
        "dependency_ready": True,
        "blocked_by_tasks": [],
    }
    assert "runtime_task" in snapshot["schema"]["node_types"]
    assert "task_statuses" in snapshot["schema"]["lifecycle_fields"]


def test_deprecated_dual_dag_task_store_alias_remains_available():
    assert DualDAGTaskStore is RuntimeTaskDAGStore


def test_store_rejects_self_loop_dependency():
    task = Task("A", {})
    task._pre_idxs = [1]
    store = RuntimeTaskDAGStore()

    with pytest.raises(TaskDependencyError, match="self-loop"):
        store.load_tasks_from_decomposition([task])


def test_store_rejects_two_node_cycle():
    task_a = Task("A", {})
    task_a._pre_idxs = [2]
    task_b = Task("B", {})
    task_b._pre_idxs = [1]
    store = RuntimeTaskDAGStore()

    with pytest.raises(TaskDependencyError, match="A -> B -> A"):
        store.load_tasks_from_decomposition([task_a, task_b])


def test_store_rejects_three_node_cycle():
    task_a = Task("A", {})
    task_a._pre_idxs = [3]
    task_b = Task("B", {})
    task_b._pre_idxs = [1]
    task_c = Task("C", {})
    task_c._pre_idxs = [2]
    store = RuntimeTaskDAGStore()

    with pytest.raises(TaskDependencyError, match="A -> B -> C -> A"):
        store.load_tasks_from_decomposition([task_a, task_b, task_c])


def test_store_rejects_out_of_range_predecessor_index():
    task = Task("A", {})
    task._pre_idxs = [2]
    store = RuntimeTaskDAGStore()

    with pytest.raises(TaskDependencyError, match="only 1 tasks exist"):
        store.load_tasks_from_decomposition([task])


@pytest.mark.parametrize("invalid_index", [0, -1])
def test_store_rejects_non_positive_predecessor_index(invalid_index):
    task = Task("A", {})
    task._pre_idxs = [invalid_index]
    store = RuntimeTaskDAGStore()

    with pytest.raises(TaskDependencyError, match="indexes are 1-based"):
        store.load_tasks_from_decomposition([task])


def test_store_normalizes_duplicate_dependencies():
    task_a = Task("A", {})
    task_b = Task("B", {})
    task_b._pre_idxs = [1, 1]
    store = RuntimeTaskDAGStore()

    store.load_tasks_from_decomposition([task_a, task_b])

    assert _edge_descriptions(store) == [("A", "B")]


def test_store_rejects_unknown_node_edge():
    task = Task("A", {})
    store = RuntimeTaskDAGStore()
    store.upsert_task(task)

    with pytest.raises(TaskDependencyError, match="unknown node"):
        store.add_task_dependency(store.task_node_id(task), "runtime:task:missing")


def _store_with_chain(count):
    tasks = [Task(chr(ord("A") + index), {}) for index in range(count)]
    store = RuntimeTaskDAGStore()
    store.load_tasks_from_decomposition(tasks)
    return store, tasks


def _edge_descriptions(store):
    graph = store.to_task_graph_projection()
    return [(start.description, end.description) for start, end in graph.edge]
