from pipeline.dual_dag_task_store import DualDAGTaskStore
from type_define.graph import GraphState, Task


def test_store_fallback_connects_unspecified_tasks_sequentially():
    task_a = Task("A", {})
    task_b = Task("B", {})

    store = DualDAGTaskStore()
    store.load_tasks_from_decomposition([task_a, task_b])

    assert _edge_descriptions(store) == [("A", "B")]


def test_store_fallback_connects_after_previous_task_not_previous_predecessors():
    task_a = Task("A", {})
    task_b = Task("B", {})
    task_b._pre_idxs = [1]
    task_c = Task("C", {})

    store = DualDAGTaskStore()
    store.load_tasks_from_decomposition([task_a, task_b, task_c])

    assert _edge_descriptions(store) == [("A", "B"), ("B", "C")]


def test_store_preserves_explicit_parallel_dependencies():
    task_a = Task("A", {})
    task_b = Task("B", {})
    task_b._pre_idxs = [1]
    task_c = Task("C", {})
    task_c._pre_idxs = [1]

    store = DualDAGTaskStore()
    store.load_tasks_from_decomposition([task_a, task_b, task_c])

    assert _edge_descriptions(store) == [("A", "B"), ("A", "C")]


def test_store_open_tasks_distinguishes_direct_and_transitive_predecessors():
    task_a = Task("A", {})
    task_b = Task("B", {})
    task_c = Task("C", {})

    store = DualDAGTaskStore()
    store.load_tasks_from_decomposition([task_a, task_b, task_c])


    open_tasks = {task.description: task for task in store.query_open_tasks()}

    assert [task.description for task in open_tasks["C"]._direct_pre_task_list] == ["B"]
    assert [task.description for task in open_tasks["C"].predecessor_task_list] == ["B", "A"]


def test_store_query_runnable_tasks_uses_dual_dag_lifecycle_state():
    task_a = Task("A", {})
    task_a.candidate_list = ["Alice"]
    task_b = Task("B", {})
    task_b.candidate_list = ["Alice"]

    store = DualDAGTaskStore()
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


def test_store_snapshot_is_canonical_dual_dag_artifact():
    store, _ = _store_with_chain(1)

    snapshot = store.snapshot()

    assert snapshot["source_of_truth"] == "dual_dag"
    assert snapshot["summary"]["task_node_count"] == 1
    assert snapshot["nodes"][0]["node_type"] == "runtime_task"
    assert "runtime_task" in snapshot["schema"]["node_types"]
    assert "task_statuses" in snapshot["schema"]["lifecycle_fields"]


def _store_with_chain(count):
    tasks = [Task(chr(ord("A") + index), {}) for index in range(count)]
    store = DualDAGTaskStore()
    store.load_tasks_from_decomposition(tasks)
    return store, tasks


def _edge_descriptions(store):
    graph = store.to_task_graph_projection()
    return [(start.description, end.description) for start, end in graph.edge]
