from __future__ import annotations

from copy import deepcopy

from benchmarks.craft.dual_dag.schema import DUAL_DAG_SCHEMA_VERSION, dual_dag_schema_registry
from type_define.graph import Graph, GraphState, Task


class DualDAGTaskStore:
    """Canonical Dual-DAG store for runtime task lifecycle state.

    `Task` and `Graph` remain compatibility projections. The canonical task
    status, dependency, candidate, and assignment state lives in `nodes` and
    `edges`.
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
            for predecessor_index in getattr(task, "_pre_idxs", []) or []:
                if 0 < predecessor_index <= len(tasks):
                    self.add_task_dependency(self.task_node_id(tasks[predecessor_index - 1]), target_id)
            if not getattr(task, "_pre_idxs", []) and task_index > 0:
                self.add_task_dependency(self.task_node_id(tasks[task_index - 1]), target_id)

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
                "assigned_agents": list(getattr(task, "_agent", []) or []),
                "required_agent_count": int(getattr(task, "number", 1) or 1),
                "available": bool(getattr(task, "available", True)),
            },
            "provenance": existing.get("provenance", {"source": "TaskManager.decomposition"}),
        }
        if node_id not in self._task_order:
            self._task_order.append(node_id)
        return node_id

    def add_task_dependency(self, predecessor_id: str, task_id: str) -> None:
        edge = {
            "source_id": predecessor_id,
            "target_id": task_id,
            "edge_type": "precedes_task",
            "metadata": {"source": "dual_dag_task_store"},
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
            elif free_agents:
                candidates = set(task.candidate_list or free_agents)
                if len(candidates & free_agents) < int(lifecycle.get("required_agent_count", 1) or 1):
                    task.available = False
            if task.available:
                runnable.append(task)
        return runnable

    def mark_task_running(self, task_id: str, assigned_agents: list[str] | None = None) -> None:
        self._set_task_lifecycle(task_id, status=Task.running, assigned_agents=assigned_agents)

    def mark_task_success(self, task_id: str, feedback=None) -> None:
        self._set_task_lifecycle(task_id, status=Task.success, reflect=feedback)

    def mark_task_failure(self, task_id: str, feedback=None) -> None:
        self._set_task_lifecycle(task_id, status=Task.failure, reflect=feedback)

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
            "runtime": "dual_dag_task_store",
            "source_of_truth": "dual_dag",
            "summary": {
                "task_node_count": len(self._task_order),
                "task_edge_count": sum(1 for edge in self.edges if edge.get("edge_type") == "precedes_task"),
                "terminal_state": self.terminal_state().value,
            },
            "nodes": [deepcopy(self.nodes[node_id]) for node_id in self._task_order],
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

    def all_predecessor_ids(self, node_id: str) -> list[str]:
        predecessor_ids = []
        for predecessor_id in self.direct_predecessor_ids(node_id):
            predecessor_ids.append(predecessor_id)
            predecessor_ids.extend(self.all_predecessor_ids(predecessor_id))
        return predecessor_ids

    def _set_task_lifecycle(self, task_id: str, *, status: str, assigned_agents: list[str] | None = None, reflect=None) -> None:
        node_id = self.task_node_id(task_id)
        node = self.nodes[node_id]
        node["lifecycle"]["status"] = status
        if assigned_agents is not None:
            node["lifecycle"]["assigned_agents"] = list(assigned_agents)
        if reflect is not None:
            node["content"]["reflect"] = deepcopy(reflect)

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
        task._agent = list(lifecycle.get("assigned_agents", []) or [])
        task.number = int(lifecycle.get("required_agent_count", 1) or 1)
        task.available = bool(lifecycle.get("available", True))
        return task

    def _status(self, node_id: str) -> str:
        return self.nodes[node_id].get("lifecycle", {}).get("status", Task.unknown)
