# Dual-DAG Runtime Boundary

VillagerAgent currently separates three concepts that should not be conflated:

- `RuntimeTaskDAGStore`: implemented runtime source of truth for task dependency and lifecycle state.
- Epistemic DAG: research concept and CRAFT-supported runtime structure for observations, claims, uncertainty, and resolution; not currently the Minecraft runtime authority.
- Action Candidate DAG: research concept and CRAFT-supported candidate/action structure; not currently the Minecraft runtime authority.

The legacy `type_define.Graph` remains a compatibility projection for prompts, visualizations, and older code paths.

## Runtime Task DAG Store

`pipeline.dual_dag_task_store.RuntimeTaskDAGStore` owns the Minecraft runtime task subgraph:

- Runtime subtask dependencies.
- Task status: `unknown`, `running`, `success`, `failure`.
- Candidate agents.
- Active agents currently executing a task.
- Last assigned agents for audit/history.
- Required agent count.
- Runnable task checks.

Runtime task nodes use `node_type="runtime_task"`. Dependency edges use `edge_type="precedes_task"` and mean the target task depends on completion of the source task.

`DualDAGTaskStore` remains as a deprecated compatibility alias. New code should use `RuntimeTaskDAGStore`.

## Task Graph Projection

The Task Graph projection preserves compatibility with existing `Task`, `Graph`, assignment prompts, Mermaid/JSON graph files, and benchmark snapshots.

- `TaskManager.query_subtask_list()` reads open tasks from `RuntimeTaskDAGStore` and returns projected `Task` objects.
- `TaskManager.query_runnable_subtasks(free_agent_names)` asks `RuntimeTaskDAGStore` for runnable tasks.
- `GlobalController` computes free agents, requests runnable tasks, applies the configured selection policy, then writes running/success/failure state through `TaskManager`.
- `TaskManager.graph` mirrors the latest runtime task DAG projection.

## Epistemic DAG

The Epistemic DAG models information state for benchmark adapters and policy analysis:

- Observations.
- Facts and reported claims.
- Hypotheses and uncertainty.
- Resolution state.
- Public/private information boundaries.

For Minecraft, current `dual_dag_artifact.json` contains public analysis nodes for observations and claims, but this is not a live Minecraft runtime source of truth.

## Action Candidate DAG

The Action Candidate DAG models candidate action state:

- Action candidates.
- Supporting evidence.
- Blockers and dependencies.
- Executability.
- Expected effects.

For Minecraft, current `decision_support.json` is read-only recommendation context derived from public artifacts. It does not own runtime lifecycle state.

## Runtime Task Selection

Minecraft runtime task selection has two layers:

1. `RuntimeTaskDAGStore` always owns task lifecycle and runnable filtering.
2. `task_selection_policy` orders runnable tasks.

Supported policies:

- `dual-dag`: use public Dual-DAG-style decision support to rank runnable tasks.
- `original`: preserve runnable task order from the runtime task DAG projection.

## Artifacts

- `runtime_dual_dag_snapshot.json`: canonical runtime task subgraph snapshot. The filename is retained for compatibility; read it as runtime task DAG state. It includes `snapshot_source` as `config_fixture` or `real_runtime`.
- `task_graph_snapshot.json`: compatibility projection of runtime tasks and edges.
- `dual_dag_artifact.json`: public analysis projection of tasks, actions, observations, and claims.
- `decision_support.json`: public read-only recommendation context derived from artifacts.

Artifacts must not expose credentials, hidden evaluator state, private observations from other agents, or simulator debug fields.
