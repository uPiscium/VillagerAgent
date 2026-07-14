from pipeline.task_manager import TaskManager
from type_define.graph import Graph, GraphState, Task


def test_query_graph_fallback_connects_unspecified_tasks_sequentially():
    task_a = Task("A", {})
    task_b = Task("B", {})

    graph = TaskManager(silent=True).query_graph([task_a, task_b])

    assert graph.edge == [(task_a, task_b)]


def test_query_graph_fallback_connects_after_previous_task_not_previous_predecessors():
    task_a = Task("A", {})
    task_b = Task("B", {})
    task_b._pre_idxs = [1]
    task_c = Task("C", {})

    graph = TaskManager(silent=True).query_graph([task_a, task_b, task_c])

    assert graph.edge == [(task_a, task_b), (task_b, task_c)]


def test_query_graph_preserves_explicit_parallel_dependencies():
    task_a = Task("A", {})
    task_b = Task("B", {})
    task_b._pre_idxs = [1]
    task_c = Task("C", {})
    task_c._pre_idxs = [1]

    graph = TaskManager(silent=True).query_graph([task_a, task_b, task_c])

    assert graph.edge == [(task_a, task_b), (task_a, task_c)]


def test_open_task_list_direct_predecessors_exclude_transitive_ancestors():
    task_a = Task("A", {})
    task_b = Task("B", {})
    task_c = Task("C", {})
    graph = Graph()
    for task in [task_a, task_b, task_c]:
        graph.add_node(task)
    graph.add_edge(task_a, task_b)
    graph.add_edge(task_b, task_c)

    graph.get_open_task_list()

    assert task_c._direct_pre_task_list == [task_b]
    assert task_c.predecessor_task_list == [task_b, task_a]


def test_graph_terminal_state_success():
    graph, tasks = _graph_with_chain(2)
    for task in tasks:
        task.status = Task.success

    assert graph.get_terminal_state() == GraphState.SUCCESS
    assert graph.check_graph_completion() is True


def test_graph_terminal_state_running_when_task_is_running():
    graph, tasks = _graph_with_chain(2)
    tasks[0].status = Task.running

    assert graph.get_terminal_state() == GraphState.RUNNING
    assert graph.check_graph_completion() is False


def test_graph_terminal_state_running_when_task_is_runnable():
    graph, _ = _graph_with_chain(1)

    assert graph.get_terminal_state() == GraphState.RUNNING
    assert graph.check_graph_completion() is False


def test_graph_terminal_state_failure_when_failed_task_blocks_success():
    graph, tasks = _graph_with_chain(2)
    tasks[0].status = Task.failure

    assert graph.get_terminal_state() == GraphState.FAILURE
    assert graph.check_graph_completion() is True


def test_graph_terminal_state_blocked_for_unreachable_unknown_task():
    graph, tasks = _graph_with_chain(2)
    tasks[0].status = "cancelled"


    assert graph.get_terminal_state() == GraphState.BLOCKED
    assert graph.check_graph_completion() is True


def test_graph_terminal_state_empty():
    graph = Graph()

    assert graph.get_terminal_state() == GraphState.EMPTY
    assert graph.check_graph_completion() is True


def _graph_with_chain(count):
    tasks = [Task(chr(ord("A") + index), {}) for index in range(count)]
    graph = Graph()
    for task in tasks:
        graph.add_node(task)
    for before, after in zip(tasks, tasks[1:]):
        graph.add_edge(before, after)
    return graph, tasks
