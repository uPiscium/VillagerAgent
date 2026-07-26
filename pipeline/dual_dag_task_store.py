from __future__ import annotations

from contextlib import contextmanager
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
        self.mutation_history: list[dict] = []

    def load_tasks_from_decomposition(self, tasks: list[Task]) -> None:
        with self._edit_transaction():
            self.nodes = {}
            self.edges = []
            self._task_order = []
            self.mutation_history = []
            for task in tasks:
                self.upsert_task(task)
            for task_index, task in enumerate(tasks):
                target_id = self.task_node_id(task)
                for predecessor_index in self._normalized_predecessor_indexes(task, task_index, len(tasks)):
                    self.add_task_dependency(self.task_node_id(tasks[predecessor_index - 1]), target_id)
                if (
                    not getattr(task, "_pre_idxs", [])
                    and task_index > 0
                    and not getattr(task, "_pre_idxs_explicit", False)
                ):
                    self.add_task_dependency(self.task_node_id(tasks[task_index - 1]), target_id)

    def load_tasks_from_graph(self, graph: Graph) -> None:
        with self._edit_transaction():
            self.nodes = {}
            self.edges = []
            self._task_order = []
            self.mutation_history = []
            for task in graph.vertex:
                self.upsert_task(task)
            for predecessor, task in graph.edge:
                self.add_task_dependency(self.task_node_id(predecessor), self.task_node_id(task))

    def upsert_task(self, task: Task) -> str:
        node_id = self.task_node_id(task)
        existing = self.nodes.get(node_id, {})
        required_agent_count = getattr(task, "number", None)
        if (
            isinstance(required_agent_count, bool)
            or not isinstance(required_agent_count, int)
            or required_agent_count <= 0
        ):
            raise TaskDependencyError(
                f'task "{task.description}" required agent count must be a positive integer'
            )
        candidate_agents = getattr(task, "candidate_list", None)
        if not isinstance(candidate_agents, list):
            raise TaskDependencyError(
                f'task "{task.description}" candidate agents must be a list'
            )
        if any(not isinstance(agent, str) or not agent for agent in candidate_agents):
            raise TaskDependencyError(
                f'task "{task.description}" candidate agents must be non-empty names'
            )
        if len({agent.casefold() for agent in candidate_agents}) != len(candidate_agents):
            raise TaskDependencyError(
                f'task "{task.description}" candidate agents must be unique'
            )
        candidates_explicit = bool(getattr(task, "_candidate_agents_explicit", False))
        exact_count = bool(getattr(task, "_candidate_agent_count_exact", False))
        if candidates_explicit and not candidate_agents:
            raise TaskDependencyError(
                f'task "{task.description}" has an explicit empty candidate list'
            )
        if candidate_agents and required_agent_count > len(candidate_agents):
            raise TaskDependencyError(
                f'task "{task.description}" requires {required_agent_count} agent(s), '
                f'but only {len(candidate_agents)} candidate(s) are available'
            )
        if exact_count and required_agent_count != len(candidate_agents):
            raise TaskDependencyError(
                f'task "{task.description}" requires assignment count to match candidates'
            )
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
                "candidate_agents": list(candidate_agents),
                "candidate_agents_explicit": candidates_explicit,
                "candidate_agent_count_exact": exact_count,
                "active_agents": list(getattr(task, "_agent", []) or []) if getattr(task, "status", Task.unknown) == Task.running else [],
                "last_assigned_agents": list(getattr(task, "_agent", []) or []),
                "required_agent_count": required_agent_count,
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

    def task_id_at(self, index: int) -> str:
        if index < 0:
            raise TaskDependencyError(f"runtime task index out of range: {index}")
        try:
            return self._task_order[index]
        except IndexError as exc:
            raise TaskDependencyError(f"runtime task index out of range: {index}") from exc

    def get_task(self, task_id: str) -> Task:
        node_id = self.task_node_id(task_id)
        if node_id not in self.nodes:
            raise TaskDependencyError(f"runtime task does not exist: {node_id}")
        return self._project_task(node_id)

    def replace_task(self, task_id: str, replacement: Task, *, source: str = "TaskManager.replan") -> str:
        node_id = self.task_node_id(task_id)
        with self._edit_transaction():
            node = self._require_node(node_id)
            previous_status = node["lifecycle"].get("status", Task.unknown)
            node["content"]["description"] = replacement.description
            node["content"]["metadata"] = deepcopy(replacement.content)
            node["content"]["milestones"] = deepcopy(replacement.milestones)
            node["lifecycle"]["status"] = Task.unknown
            node["lifecycle"]["active_agents"] = []
            self._record_revision(node, source=source, previous_status=previous_status)
            self._record_mutation("replace", [node_id], source=source)
        return node_id

    def insert_task_after(self, predecessor_id: str, task: Task, *, source: str = "TaskManager.insert") -> str:
        predecessor_node_id = self.task_node_id(predecessor_id)
        new_node_id = self.task_node_id(task)
        with self._edit_transaction():
            self._require_node(predecessor_node_id)
            if new_node_id in self.nodes:
                raise TaskDependencyError(f"runtime task already exists: {new_node_id}")
            successors = self._direct_successor_ids(predecessor_node_id)
            self.upsert_task(task)
            self.nodes[new_node_id]["provenance"] = {
                "source": source,
                "parent_task_id": predecessor_node_id,
                "revision": 1,
            }
            self._task_order.remove(new_node_id)
            predecessor_index = self._task_order.index(predecessor_node_id)
            self._task_order.insert(predecessor_index + 1, new_node_id)
            self._remove_edges(source_id=predecessor_node_id)
            self.add_task_dependency(predecessor_node_id, new_node_id)
            for successor_id in successors:
                self.add_task_dependency(new_node_id, successor_id)
            self._record_mutation("insert", [new_node_id], source=source)
        return new_node_id

    def remove_task(self, task_id: str, *, source: str = "TaskManager.delete") -> None:
        node_id = self.task_node_id(task_id)
        with self._edit_transaction():
            self._require_node(node_id)
            predecessors = self.direct_predecessor_ids(node_id)
            successors = self._direct_successor_ids(node_id)
            self._remove_edges(source_id=node_id)
            self._remove_edges(target_id=node_id)
            del self.nodes[node_id]
            self._task_order.remove(node_id)
            for predecessor_id in predecessors:
                for successor_id in successors:
                    if predecessor_id != successor_id:
                        self.add_task_dependency(predecessor_id, successor_id)
            self._record_mutation("delete", [node_id], source=source)

    def move_task_after(self, task_id: str, predecessor_id: str, *, source: str = "TaskManager.move") -> None:
        node_id = self.task_node_id(task_id)
        predecessor_node_id = self.task_node_id(predecessor_id)
        if node_id == predecessor_node_id:
            raise TaskDependencyError(f"task dependency self-loop detected: {node_id}")
        with self._edit_transaction():
            node = self._require_node(node_id)
            self._require_node(predecessor_node_id)
            old_predecessors = self.direct_predecessor_ids(node_id)
            old_successors = self._direct_successor_ids(node_id)
            self._remove_edges(source_id=node_id)
            self._remove_edges(target_id=node_id)
            for old_predecessor_id in old_predecessors:
                for old_successor_id in old_successors:
                    if old_predecessor_id != old_successor_id:
                        self.add_task_dependency(old_predecessor_id, old_successor_id)
            new_successors = self._direct_successor_ids(predecessor_node_id)
            self._remove_edges(source_id=predecessor_node_id)
            self.add_task_dependency(predecessor_node_id, node_id)
            for successor_id in new_successors:
                if successor_id != node_id:
                    self.add_task_dependency(node_id, successor_id)
            self._task_order.remove(node_id)
            predecessor_index = self._task_order.index(predecessor_node_id)
            self._task_order.insert(predecessor_index + 1, node_id)
            self._record_revision(node, source=source)
            self._record_mutation("move", [node_id], source=source)

    def replace_task_with_subgraph(
        self,
        task_id: str,
        subtasks: list[Task],
        *,
        source: str = "TaskManager.decompose",
    ) -> list[str]:
        parent_node_id = self.task_node_id(task_id)
        if not subtasks:
            raise TaskDependencyError("replacement subgraph must contain at least one task")
        with self._edit_transaction():
            parent_node = deepcopy(self._require_node(parent_node_id))
            predecessors = self.direct_predecessor_ids(parent_node_id)
            successors = self._direct_successor_ids(parent_node_id)
            subgraph_store = RuntimeTaskDAGStore()
            subgraph_store.load_tasks_from_decomposition(subtasks)
            self._remove_edges(source_id=parent_node_id)
            self._remove_edges(target_id=parent_node_id)
            del self.nodes[parent_node_id]
            parent_index = self._task_order.index(parent_node_id)
            self._task_order.remove(parent_node_id)
            subtask_node_ids = list(subgraph_store._task_order)
            for offset, subtask_node_id in enumerate(subtask_node_ids):
                if subtask_node_id in self.nodes:
                    raise TaskDependencyError(f"runtime task already exists: {subtask_node_id}")
                node = deepcopy(subgraph_store.nodes[subtask_node_id])
                node["provenance"] = {
                    "source": source,
                    "parent_task_id": parent_node_id,
                    "parent_status": parent_node["lifecycle"].get("status", Task.unknown),
                    "parent_last_assigned_agents": deepcopy(
                        parent_node["lifecycle"].get("last_assigned_agents", [])
                    ),
                    "parent_reflect": deepcopy(parent_node["content"].get("reflect")),
                    "revision": 1,
                }
                self.nodes[subtask_node_id] = node
                self._task_order.insert(parent_index + offset, subtask_node_id)
            self.edges.extend(deepcopy(subgraph_store.edges))
            entry_ids = [
                node_id for node_id in subtask_node_ids
                if not subgraph_store.direct_predecessor_ids(node_id)
            ]
            exit_ids = [
                node_id for node_id in subtask_node_ids
                if not subgraph_store._direct_successor_ids(node_id)
            ]
            for predecessor_id in predecessors:
                for entry_id in entry_ids:
                    self.add_task_dependency(predecessor_id, entry_id)
            for exit_id in exit_ids:
                for successor_id in successors:
                    self.add_task_dependency(exit_id, successor_id)
            self._record_mutation("decompose", [parent_node_id, *subtask_node_ids], source=source)
        return subtask_node_ids

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
                candidates_explicit = bool(lifecycle.get("candidate_agents_explicit", False))
                if candidates_explicit and not task.candidate_list:
                    raise TaskDependencyError(
                        f'task "{task.description}" has an explicit empty candidate list'
                    )
                candidates = list(
                    task.candidate_list
                    if task.candidate_list
                    else free_agent_names
                )
                eligible_free_agents = [agent for agent in free_agent_names if agent in set(candidates)]
                if not task.candidate_list and not candidates_explicit:
                    task.candidate_list = eligible_free_agents
                required_agent_count = lifecycle.get("required_agent_count")
                if len(eligible_free_agents) < required_agent_count:
                    task.available = False
            if task.available:
                runnable.append(task)
        return runnable

    def mark_task_running(self, task_id: str, assigned_agents: list[str] | None = None) -> None:
        node = self._require_node(self.task_node_id(task_id))
        assigned_agents = list(assigned_agents or [])
        required = node["lifecycle"]["required_agent_count"]
        if len(assigned_agents) != required:
            raise TaskDependencyError(
                f"task {task_id} requires exactly {required} assigned agent(s)"
            )
        if any(not isinstance(agent, str) or not agent for agent in assigned_agents):
            raise TaskDependencyError(f"task {task_id} assigned agents must be non-empty names")
        if len({agent.casefold() for agent in assigned_agents}) != len(assigned_agents):
            raise TaskDependencyError(f"task {task_id} assigned agents must be unique")
        candidates = node["lifecycle"]["candidate_agents"]
        if candidates and any(agent not in candidates for agent in assigned_agents):
            raise TaskDependencyError(f"task {task_id} assigned an agent outside its candidates")
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
            "mutation_history": deepcopy(self.mutation_history),
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
            if isinstance(predecessor_index, bool) or not isinstance(predecessor_index, int):
                raise TaskDependencyError(
                    f'task "{task.description}" references non-integer predecessor index {predecessor_index!r}'
                )
            normalized = predecessor_index
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
        direct_predecessors = set(self.direct_predecessor_ids(node_id))
        predecessors = list(dict.fromkeys(self.all_predecessor_ids(node_id)))
        blocker_ids = [
            predecessor_id
            for predecessor_id in predecessors
            if self._status(predecessor_id) != Task.success
        ]
        node["derived"] = {
            "dependency_ready": all(self._status(predecessor_id) == Task.success for predecessor_id in predecessors),
            "blocked_by_tasks": blocker_ids,
            "dependency_blockers": [
                {
                    "task_id": predecessor_id,
                    "description": self.nodes[predecessor_id]["content"].get("description", ""),
                    "status": self._status(predecessor_id),
                    "relation": "direct" if predecessor_id in direct_predecessors else "transitive",
                }
                for predecessor_id in blocker_ids
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
        task._candidate_agents_explicit = bool(lifecycle.get("candidate_agents_explicit", False))
        task._candidate_agent_count_exact = bool(lifecycle.get("candidate_agent_count_exact", False))
        active_agents = lifecycle.get("active_agents")
        if active_agents is None:
            active_agents = lifecycle.get("assigned_agents", [])
        task._agent = list(active_agents or [])
        task.number = lifecycle.get("required_agent_count")
        task.available = self._is_dependency_ready(node_id) and task.status == Task.unknown
        return task

    def _is_dependency_ready(self, node_id: str) -> bool:
        return all(
            self._status(predecessor_id) == Task.success
            for predecessor_id in self.all_predecessor_ids(node_id)
        )

    def _status(self, node_id: str) -> str:
        return self.nodes[node_id].get("lifecycle", {}).get("status", Task.unknown)

    def _direct_successor_ids(self, node_id: str) -> list[str]:
        return [
            edge["target_id"]
            for edge in self.edges
            if edge.get("edge_type") == "precedes_task" and edge.get("source_id") == node_id
        ]

    def _remove_edges(self, *, source_id: str | None = None, target_id: str | None = None) -> None:
        self.edges = [
            edge for edge in self.edges
            if not (
                (source_id is None or edge.get("source_id") == source_id)
                and (target_id is None or edge.get("target_id") == target_id)
            )
        ]

    def _require_node(self, node_id: str) -> dict:
        if node_id not in self.nodes:
            raise TaskDependencyError(f"runtime task does not exist: {node_id}")
        return self.nodes[node_id]

    def _record_revision(self, node: dict, *, source: str, previous_status: str | None = None) -> None:
        previous = deepcopy(node.get("provenance", {}))
        revision = int(previous.get("revision", 0) or 0) + 1
        provenance = {
            "source": source,
            "revision": revision,
            "previous": previous,
        }
        if previous_status is not None:
            provenance["previous_status"] = previous_status
        node["provenance"] = provenance

    def _record_mutation(self, operation: str, task_ids: list[str], *, source: str) -> None:
        self.mutation_history.append({
            "revision": len(self.mutation_history) + 1,
            "operation": operation,
            "task_ids": list(task_ids),
            "source": source,
        })

    @contextmanager
    def _edit_transaction(self):
        backup = (
            deepcopy(self.nodes),
            deepcopy(self.edges),
            list(self._task_order),
            deepcopy(self.mutation_history),
        )
        try:
            yield
            self._validate_acyclic()
        except Exception:
            self.nodes, self.edges, self._task_order, self.mutation_history = backup
            raise


# Deprecated compatibility alias. Use RuntimeTaskDAGStore.
DualDAGTaskStore = RuntimeTaskDAGStore
