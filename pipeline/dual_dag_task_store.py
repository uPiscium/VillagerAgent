from __future__ import annotations

from copy import deepcopy

from benchmarks.craft.dual_dag.schema import DUAL_DAG_SCHEMA_VERSION, dual_dag_schema_registry
from type_define.graph import Graph, GraphState, Task


class TaskDependencyError(ValueError):
    """Raised when runtime task dependencies cannot form a valid DAG."""


class RuntimeTaskDAGStore:
    """Canonical store for runtime task dependency and lifecycle state.

    This store owns only the runtime task subgraph: task status, dependency,
    candidate, and assignment metadata. Epistemic and action-candidate DAG
    runtime state is outside this class.
    """

    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self._task_order: list[str] = []

    def load_tasks_from_decomposition(self, tasks: list[Task]) -> None:
        self.nodes = {}
        self.edges = []
        self._task_order = []
        for task in tasks:
            self.upsert_task(task)
        for task_index, task in enumerate(tasks):
            target_id = self.task_node_id(task)
            for predecessor_index in self._normalized_predecessor_indexes(task, task_index, len(tasks)):
                self.add_task_dependency(self.task_node_id(tasks[predecessor_index - 1]), target_id)
            if not getattr(task, "_pre_idxs", []) and task_index > 0:
                self.add_task_dependency(self.task_node_id(tasks[task_index - 1]), target_id)
        self._validate_acyclic()

    def load_tasks_from_graph(self, graph: Graph) -> None:
        self.nodes = {}
        self.edges = []
        self._task_order = []
        for task in graph.vertex:
            self.upsert_task(task)
        for predecessor, task in graph.edge:
            self.add_task_dependency(self.task_node_id(predecessor), self.task_node_id(task))
        self._validate_acyclic()

    def upsert_task(self, task: Task) -> str:
        node_id = self.task_node_id(task)
        existing = self.nodes.get(node_id, {})
        self.nodes[node_id] = {
            "node_id": node_id,
            "node_type": "runtime_task",
            "content": {
                "description": task.description,
                "metadata": deepcopy(task.content),
                "milestones": deepcopy(task.milestones),
                "reflect": deepcopy(task.reflect),
            },
            "lifecycle": {
                "status": getattr(task, "status", Task.unknown),
                "candidate_agents": list(getattr(task, "candidate_list", []) or []),
                "active_agents": list(getattr(task, "_agent", []) or []) if getattr(task, "status", Task.unknown) == Task.running else [],
                "last_assigned_agents": list(getattr(task, "_agent", []) or []),
                "required_agent_count": int(getattr(task, "number", 1) or 1),
            },
            "provenance": existing.get("provenance", {"source": "TaskManager.decomposition"}),
        }
        if node_id not in self._task_order:
            self._task_order.append(node_id)
        return node_id

    def add_task_dependency(self, predecessor_id: str, task_id: str) -> None:
        if predecessor_id == task_id:
            raise TaskDependencyError(f"task dependency self-loop detected: {predecessor_id}")
        missing = [node_id for node_id in (predecessor_id, task_id) if node_id not in self.nodes]
        if missing:
            raise TaskDependencyError(f"task dependency references unknown node(s): {', '.join(missing)}")
        edge = {
            "source_id": predecessor_id,
            "target_id": task_id,
            "edge_type": "precedes_task",
            "metadata": {"source": "runtime_task_dag_store"},
        }
        if edge not in self.edges:
            self.edges.append(edge)

    def query_open_tasks(self) -> list[Task]:
        tasks = []
        for node_id in self._task_order:
            if self._status(node_id) not in (Task.unknown, Task.running):
                continue
            task = self._project_task(node_id)
            task._direct_pre_task_list = [
                self._project_task(predecessor_id)
                for predecessor_id in self.direct_predecessor_ids(node_id)
                if self._status(predecessor_id) in (Task.unknown, Task.running)
            ]
            task.predecessor_task_list = [
                self._project_task(predecessor_id)
                for predecessor_id in self.all_predecessor_ids(node_id)
                if self._status(predecessor_id) in (Task.unknown, Task.running)
            ]
            tasks.append(task)
        return tasks

    def query_runnable_tasks(self, free_agent_names: list[str] | None = None) -> list[Task]:
        free_agents = set(free_agent_names or [])
        runnable = []
        for task in self.query_open_tasks():
            lifecycle = self.nodes[self.task_node_id(task)]["lifecycle"]
            task.available = True
            if task.status != Task.unknown or task.predecessor_task_list:
                task.available = False
            elif free_agent_names is not None:
                candidates = list(task.candidate_list or free_agent_names)
                eligible_free_agents = [agent for agent in free_agent_names if agent in set(candidates)]
                if not task.candidate_list:
                    task.candidate_list = eligible_free_agents
                if len(eligible_free_agents) < int(lifecycle.get("required_agent_count", 1) or 1):
                    task.available = False
            if task.available:
                runnable.append(task)
        return runnable

    def mark_task_running(self, task_id: str, assigned_agents: list[str] | None = None) -> None:
        self._set_task_lifecycle(task_id, status=Task.running, active_agents=assigned_agents)

    def mark_task_success(self, task_id: str, feedback=None) -> None:
        self._set_task_lifecycle(task_id, status=Task.success, reflect=feedback)

    def mark_task_failure(self, task_id: str, feedback=None) -> None:
        self._set_task_lifecycle(task_id, status=Task.failure, reflect=feedback)

    def mark_task_status(self, task_id: str, status: str, feedback=None, assigned_agents: list[str] | None = None) -> None:
        self._set_task_lifecycle(task_id, status=status, active_agents=assigned_agents, reflect=feedback)

    def terminal_state(self) -> GraphState:
        node_ids = list(self._task_order)
        if not node_ids:
            return GraphState.EMPTY
        if any(self._status(node_id) == Task.running for node_id in node_ids):
            return GraphState.RUNNING
        if all(self._status(node_id) == Task.success for node_id in node_ids):
            return GraphState.SUCCESS
        if any(self._status(node_id) == Task.failure for node_id in node_ids):
            return GraphState.FAILURE
        for node_id in node_ids:
            if self._status(node_id) == Task.unknown:
                predecessors = self.all_predecessor_ids(node_id)
                if not predecessors or all(self._status(predecessor_id) == Task.success for predecessor_id in predecessors):
                    return GraphState.RUNNING
        return GraphState.BLOCKED

    def to_task_graph_projection(self) -> Graph:
        graph = Graph()
        tasks_by_node_id = {node_id: self._project_task(node_id) for node_id in self._task_order}
        for node_id in self._task_order:
            graph.add_node(tasks_by_node_id[node_id])
        for edge in self.edges:
            if edge.get("edge_type") != "precedes_task":
                continue
            source_id = edge.get("source_id")
            target_id = edge.get("target_id")
            if source_id in tasks_by_node_id and target_id in tasks_by_node_id:
                graph.add_edge(tasks_by_node_id[source_id], tasks_by_node_id[target_id])
        return graph

    def snapshot(self) -> dict:
        return {
            "schema_version": DUAL_DAG_SCHEMA_VERSION,
            "runtime": "runtime_task_dag_store",
            "source_of_truth": "runtime_task_dag",
            "summary": {
                "task_node_count": len(self._task_order),
                "task_edge_count": sum(1 for edge in self.edges if edge.get("edge_type") == "precedes_task"),
                "terminal_state": self.terminal_state().value,
            },
            "nodes": [self._snapshot_node(node_id) for node_id in self._task_order],
            "edges": deepcopy(self.edges),
            "schema": dual_dag_schema_registry(),
        }

    @staticmethod
    def task_node_id(task: Task | str) -> str:
        if isinstance(task, Task):
            task_id = task.id
        else:
            task_id = task
        if str(task_id).startswith("runtime:task:"):
            return str(task_id)
        return f"runtime:task:{task_id}"

    def direct_predecessor_ids(self, node_id: str) -> list[str]:
        return [
            edge["source_id"]
            for edge in self.edges
            if edge.get("edge_type") == "precedes_task" and edge.get("target_id") == node_id
        ]

    def all_predecessor_ids(self, node_id: str, *, visited: set[str] | None = None) -> list[str]:
        visited = set() if visited is None else visited
        if node_id in visited:
            raise TaskDependencyError(f"task dependency cycle detected at {node_id}")
        visited.add(node_id)
        predecessor_ids = []
        for predecessor_id in self.direct_predecessor_ids(node_id):
            predecessor_ids.append(predecessor_id)
            predecessor_ids.extend(self.all_predecessor_ids(predecessor_id, visited=set(visited)))
        return predecessor_ids

    def _normalized_predecessor_indexes(self, task: Task, task_index: int, task_count: int) -> list[int]:
        indexes = []
        seen = set()
        for predecessor_index in getattr(task, "_pre_idxs", []) or []:
            try:
                normalized = int(predecessor_index)
            except (TypeError, ValueError) as exc:
                raise TaskDependencyError(
                    f'task "{task.description}" references non-integer predecessor index {predecessor_index!r}'
                ) from exc
            if normalized <= 0:
                raise TaskDependencyError(
                    f'task "{task.description}" references predecessor index {normalized}, but indexes are 1-based'
                )
            if normalized > task_count:
                raise TaskDependencyError(
                    f'task "{task.description}" references predecessor index {normalized}, but only {task_count} tasks exist'
                )
            if normalized == task_index + 1:
                raise TaskDependencyError(f'task dependency self-loop detected: "{task.description}"')
            if normalized not in seen:
                indexes.append(normalized)
                seen.add(normalized)
        return indexes

    def _validate_acyclic(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str, path: list[str]) -> None:
            if node_id in visiting:
                cycle_start = path.index(node_id) if node_id in path else 0
                cycle = path[cycle_start:]
                descriptions = [self.nodes[item]["content"].get("description", item) for item in cycle]
                raise TaskDependencyError(f"task dependency cycle detected: {' -> '.join(descriptions)}")
            if node_id in visited:
                return
            visiting.add(node_id)
            for edge in self.edges:
                if edge.get("edge_type") == "precedes_task" and edge.get("source_id") == node_id:
                    target_id = edge.get("target_id")
                    if target_id not in self.nodes:
                        raise TaskDependencyError(f"task dependency references unknown node: {target_id}")
                    visit(target_id, path + [target_id])
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in self._task_order:
            visit(node_id, [node_id])

    def _set_task_lifecycle(self, task_id: str, *, status: str, active_agents: list[str] | None = None, reflect=None) -> None:
        node_id = self.task_node_id(task_id)
        node = self.nodes[node_id]
        node["lifecycle"]["status"] = status
        if active_agents is not None:
            agents = list(active_agents)
            node["lifecycle"]["active_agents"] = agents
            if agents:
                node["lifecycle"]["last_assigned_agents"] = agents
        if status in (Task.success, Task.failure):
            node["lifecycle"]["active_agents"] = []
        if reflect is not None:
            node["content"]["reflect"] = deepcopy(reflect)

    def _snapshot_node(self, node_id: str) -> dict:
        node = deepcopy(self.nodes[node_id])
        predecessors = self.all_predecessor_ids(node_id)
        node["derived"] = {
            "dependency_ready": all(self._status(predecessor_id) == Task.success for predecessor_id in predecessors),
            "blocked_by_tasks": [
                predecessor_id
                for predecessor_id in predecessors
                if self._status(predecessor_id) in (Task.unknown, Task.running)
            ],
        }
        return node

    def _project_task(self, node_id: str) -> Task:
        node = self.nodes[node_id]
        content = node.get("content", {})
        lifecycle = node.get("lifecycle", {})
        task = Task(content.get("description", ""), deepcopy(content.get("metadata", {})))
        task.id = node_id.removeprefix("runtime:task:")
        task.milestones = deepcopy(content.get("milestones", []))
        task.reflect = deepcopy(content.get("reflect"))
        task.status = lifecycle.get("status", Task.unknown)
        task.candidate_list = list(lifecycle.get("candidate_agents", []) or [])
        active_agents = lifecycle.get("active_agents")
        if active_agents is None:
            active_agents = lifecycle.get("assigned_agents", [])
        task._agent = list(active_agents or [])
        task.number = int(lifecycle.get("required_agent_count", 1) or 1)
        task.available = self._is_dependency_ready(node_id) and task.status == Task.unknown
        return task

    def _is_dependency_ready(self, node_id: str) -> bool:
        return all(
            self._status(predecessor_id) == Task.success
            for predecessor_id in self.all_predecessor_ids(node_id)
        )

    def _status(self, node_id: str) -> str:
        return self.nodes[node_id].get("lifecycle", {}).get("status", Task.unknown)


# Deprecated compatibility alias. Use RuntimeTaskDAGStore.
DualDAGTaskStore = RuntimeTaskDAGStore
