from copy import deepcopy

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


def test_store_preserves_explicit_tasks_without_predecessors_as_parallel():
    task_a = Task("A", {})
    task_a._pre_idxs_explicit = True
    task_b = Task("B", {})
    task_b._pre_idxs_explicit = True

    store = RuntimeTaskDAGStore()
    store.load_tasks_from_decomposition([task_a, task_b])

    assert _edge_descriptions(store) == []


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


def test_store_query_runnable_tasks_requires_enough_free_candidate_agents():
    task = Task("A", {})
    task.candidate_list = ["Alice"]
    task.number = 1
    store = RuntimeTaskDAGStore()
    store.load_tasks_from_decomposition([task])

    assert store.query_runnable_tasks(["Bob"]) == []
    assert [task.description for task in store.query_runnable_tasks(["Alice"])] == ["A"]


def test_store_query_runnable_tasks_treats_empty_candidates_as_all_free_agents():
    task = Task("A", {})
    task.candidate_list = []
    task.number = 2
    store = RuntimeTaskDAGStore()
    store.load_tasks_from_decomposition([task])

    assert store.query_runnable_tasks(["Alice"]) == []
    runnable = store.query_runnable_tasks(["Alice", "Bob"])

    assert [task.description for task in runnable] == ["A"]
    assert runnable[0].candidate_list == ["Alice", "Bob"]


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
        "dependency_blockers": [],
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


@pytest.mark.parametrize("invalid_index", [True, 1.0, 1.5, "1", None])
def test_store_rejects_non_integer_predecessor_types(invalid_index):
    task_a = Task("A", {})
    task_b = Task("B", {})
    task_b._pre_idxs = [invalid_index]
    store = RuntimeTaskDAGStore()

    with pytest.raises(TaskDependencyError, match="non-integer predecessor index"):
        store.load_tasks_from_decomposition([task_a, task_b])


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


@pytest.mark.parametrize("status", [Task.unknown, Task.running, Task.failure])
def test_store_snapshot_reports_non_success_dependency_blocker_status(status):
    store, tasks = _store_with_chain(2)
    if status != Task.unknown:
        store.mark_task_status(tasks[0].id, status)

    blocked = store.snapshot()["nodes"][1]["derived"]

    assert blocked["dependency_ready"] is False
    assert blocked["blocked_by_tasks"] == [store.task_node_id(tasks[0])]
    assert blocked["dependency_blockers"] == [{
        "task_id": store.task_node_id(tasks[0]),
        "description": "A",
        "status": status,
        "relation": "direct",
    }]


def test_store_snapshot_distinguishes_direct_and_transitive_blockers():
    store, tasks = _store_with_chain(3)
    store.mark_task_running(tasks[1].id, assigned_agents=["Alice"])

    blocked = store.snapshot()["nodes"][2]["derived"]

    assert blocked["dependency_blockers"] == [
        {
            "task_id": store.task_node_id(tasks[1]),
            "description": "B",
            "status": Task.running,
            "relation": "direct",
        },
        {
            "task_id": store.task_node_id(tasks[0]),
            "description": "A",
            "status": Task.unknown,
            "relation": "transitive",
        },
    ]


def test_store_snapshot_has_no_blockers_when_all_dependencies_succeed():
    store, tasks = _store_with_chain(3)
    store.mark_task_success(tasks[0].id)
    store.mark_task_success(tasks[1].id)

    derived = store.snapshot()["nodes"][2]["derived"]

    assert derived == {
        "dependency_ready": True,
        "blocked_by_tasks": [],
        "dependency_blockers": [],
    }


def test_store_replace_preserves_identity_assignment_history_and_reflect():
    store, tasks = _store_with_chain(1)
    original_id = tasks[0].id
    store.mark_task_running(original_id, assigned_agents=["Alice"])
    store.mark_task_failure(original_id, feedback="blocked")
    replacement = Task("Replanned A", {"revision_data": True})
    replacement.milestones = ["new milestone"]

    store.replace_task(original_id, replacement)

    node = store.snapshot()["nodes"][0]
    assert node["node_id"] == store.task_node_id(original_id)
    assert node["content"]["description"] == "Replanned A"
    assert node["content"]["reflect"] == "blocked"
    assert node["lifecycle"]["status"] == Task.unknown
    assert node["lifecycle"]["last_assigned_agents"] == ["Alice"]
    assert node["provenance"]["source"] == "TaskManager.replan"
    assert node["provenance"]["previous_status"] == Task.failure


def test_store_move_preserves_node_lifecycle_and_identity():
    store, tasks = _store_with_chain(3)
    store.mark_task_running(tasks[1].id, assigned_agents=["Bob"])
    before = deepcopy(store.nodes[store.task_node_id(tasks[1])]["lifecycle"])

    store.move_task_after(tasks[1].id, tasks[2].id)

    assert store.nodes[store.task_node_id(tasks[1])]["lifecycle"] == before
    assert [task.id for task in store.to_task_graph_projection().vertex] == [
        tasks[0].id,
        tasks[2].id,
        tasks[1].id,
    ]
    assert _edge_descriptions(store) == [("A", "C"), ("C", "B")]


def test_store_insert_reroutes_successors_and_uses_new_identity():
    store, tasks = _store_with_chain(2)
    inserted = Task("Inserted", {})

    inserted_node_id = store.insert_task_after(tasks[0].id, inserted)

    assert inserted_node_id not in {store.task_node_id(task) for task in tasks}
    assert _edge_descriptions(store) == [("A", "Inserted"), ("Inserted", "B")]
    assert store.nodes[inserted_node_id]["provenance"]["source"] == "TaskManager.insert"


def test_store_delete_merges_dependencies_without_dangling_edges():
    store, tasks = _store_with_chain(3)

    store.remove_task(tasks[1].id)

    assert store.task_node_id(tasks[1]) not in store.nodes
    assert _edge_descriptions(store) == [("A", "C")]
    assert all(
        edge["source_id"] in store.nodes and edge["target_id"] in store.nodes
        for edge in store.edges
    )
    assert store.snapshot()["mutation_history"][-1] == {
        "revision": 1,
        "operation": "delete",
        "task_ids": [store.task_node_id(tasks[1])],
        "source": "TaskManager.delete",
    }


def test_store_decompose_replaces_parent_with_validated_subgraph():
    store, tasks = _store_with_chain(3)
    store.mark_task_running(tasks[1].id, assigned_agents=["Alice"])
    store.mark_task_failure(tasks[1].id, feedback="needs decomposition")
    first = Task("B1", {})
    second = Task("B2", {})

    subtask_ids = store.replace_task_with_subgraph(tasks[1].id, [first, second])

    assert store.task_node_id(tasks[1]) not in store.nodes
    assert subtask_ids == [store.task_node_id(first), store.task_node_id(second)]
    assert _edge_descriptions(store) == [("B1", "B2"), ("A", "B1"), ("B2", "C")]
    provenance = store.nodes[subtask_ids[0]]["provenance"]
    assert provenance["parent_task_id"] == store.task_node_id(tasks[1])
    assert provenance["parent_last_assigned_agents"] == ["Alice"]
    assert provenance["parent_reflect"] == "needs decomposition"


def test_store_rejects_cyclic_edit_and_rolls_back():
    store, tasks = _store_with_chain(2)
    before = store.snapshot()

    with pytest.raises(TaskDependencyError, match="self-loop"):
        store.move_task_after(tasks[0].id, tasks[0].id)
    assert store.snapshot() == before

    cyclic_a = Task("Cycle A", {})
    cyclic_a._pre_idxs = [2]
    cyclic_b = Task("Cycle B", {})
    cyclic_b._pre_idxs = [1]
    with pytest.raises(TaskDependencyError, match="cycle"):
        store.replace_task_with_subgraph(tasks[0].id, [cyclic_a, cyclic_b])
    assert store.snapshot() == before


@pytest.mark.parametrize("invalid_dependencies", [[[], [3]], [[2], [1]]])
def test_store_invalid_load_rolls_back_snapshot_and_projection(invalid_dependencies):
    store, _ = _store_with_chain(3)
    before_snapshot = store.snapshot()
    before_projection = _edge_descriptions(store)
    invalid_tasks = [Task("Invalid A", {}), Task("Invalid B", {})]
    for task, predecessors in zip(invalid_tasks, invalid_dependencies):
        task._pre_idxs = predecessors

    with pytest.raises(TaskDependencyError):
        store.load_tasks_from_decomposition(invalid_tasks)

    assert store.snapshot() == before_snapshot
    assert _edge_descriptions(store) == before_projection


def _store_with_chain(count):
    tasks = [Task(chr(ord("A") + index), {}) for index in range(count)]
    store = RuntimeTaskDAGStore()
    store.load_tasks_from_decomposition(tasks)
    return store, tasks


def _edge_descriptions(store):
    graph = store.to_task_graph_projection()
    return [(start.description, end.description) for start, end in graph.edge]
