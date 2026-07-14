# Dual-DAG Runtime Boundary

VillagerAgent uses Dual-DAG as the canonical runtime task store. The legacy Task Graph remains as a compatibility projection for prompts, visualizations, and older code paths.

## Runtime Dual-DAG

The runtime Dual-DAG is the controller's source of truth for execution state.

- Runtime subtask dependencies.
- Task status: `unknown`, `running`, `success`, `failure`.
- Candidate agents and assigned agents.
- Runnable task checks.
- Controller loop scheduling.

Runtime task nodes use `node_type="runtime_task"`. Dependency edges use `edge_type="precedes_task"` and mean the target task depends on completion of the source task.

`pipeline.dual_dag_task_store.DualDAGTaskStore` owns task lifecycle state. `TaskManager.graph` is regenerated from this store and should be treated as a projection, not a separate authority.

## Task Graph Projection

The Task Graph projection preserves compatibility with existing `Task`, `Graph`, assignment prompts, Mermaid/JSON graph files, and benchmark snapshots.

- `TaskManager.query_subtask_list()` reads from Dual-DAG and returns projected `Task` objects.
- `GlobalController` writes running/success/failure state to Dual-DAG through `TaskManager`.
- `TaskManager.graph` mirrors the latest Dual-DAG task/dependency lifecycle projection.

## Epistemic DAG

The Epistemic DAG models information state for benchmark adapters and policy analysis.

- Observations.
- Facts and reported claims.
- Hypotheses and uncertainty.
- Resolution state.
- Public/private information boundaries.

## Action Candidate DAG

The Action Candidate DAG models candidate action state.

- Action candidates.
- Supporting evidence.
- Blockers and dependencies.
- Executability.
- Expected effects.

## Runtime Task Selection

Current Minecraft runtime task selection uses Dual-DAG-derived decision support as an additional ranking input:

1. `TaskManager` produces open tasks from the runtime Dual-DAG store.
2. The Minecraft Dual-DAG runtime selector ranks candidate tasks when configured.
3. `GlobalController` still checks agent availability and candidate constraints.
4. The selected task is executed through the controller loop and lifecycle updates are written back to Dual-DAG.

Dual-DAG task lifecycle state replaces Task Graph status, dependency edges, and assignment state as the runtime source of truth. Ranking policy is still separate from lifecycle ownership.

## Artifacts

- `task_graph_snapshot.json`: compatibility projection of runtime tasks and edges.
- `dual_dag_artifact.json`: public analysis projection of tasks, actions, observations, and claims.
- `decision_support.json`: public read-only recommendation context derived from artifacts.

These artifacts are safe for benchmark analysis and should not expose credentials, hidden evaluator state, private observations from other agents, or simulator debug fields.
