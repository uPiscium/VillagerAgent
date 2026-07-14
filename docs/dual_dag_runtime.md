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

Replanning is store-first. `RuntimeTaskDAGStore` provides validated replace, insert-after, move-after, delete, and replace-with-subgraph operations. Each edit runs transactionally, validates the resulting DAG, records mutation provenance, and then `TaskManager` regenerates `graph`. The deprecated `sync_dual_dag_from_graph()` compatibility method is not used by runtime replanning.

Identity and history policy:

- Replace and move preserve the runtime task ID. Replace resets status to `unknown` for the new plan while preserving reflect and `last_assigned_agents`; provenance records the previous status.
- Insert creates a new ID.
- Decompose deletes the parent node, creates new subtask IDs, and copies parent status, reflect, and assignment history into child provenance. No `decomposed` lifecycle status is introduced.
- Delete removes the ID and reconnects predecessors to successors.

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

- `runtime_dual_dag_snapshot.json`: canonical runtime task subgraph snapshot. The filename is retained for compatibility; read it as runtime task DAG state. It includes `snapshot_source` as `config_fixture` or `real_runtime` and additive `mutation_history` entries for store edits.
- `task_graph_snapshot.json`: compatibility projection of runtime tasks and edges.
- `dual_dag_artifact.json`: public analysis projection of tasks, actions, observations, and claims.
- `decision_support.json`: public read-only recommendation context derived from artifacts.

Artifacts must not expose credentials, hidden evaluator state, private observations from other agents, or simulator debug fields.
