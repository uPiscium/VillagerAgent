# Dual-DAG Runtime Boundary

VillagerAgent uses two related but separate graph concepts: the runtime Task Graph and benchmark Dual-DAG artifacts.

## Task Graph

The Task Graph is the controller's source of truth for execution state.

- Runtime subtask dependencies.
- Task status: `unknown`, `running`, `success`, `failure`.
- Candidate agents and assigned agents.
- Runnable task checks.
- Controller loop scheduling.

Task Graph edges are stored as `(start_task, end_task)` and mean `end_task` depends on completion of `start_task`.

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

Current Minecraft runtime task selection uses Dual-DAG-derived decision support as a read-only ranking input:

1. `TaskManager` produces open tasks from the Task Graph.
2. The Dual-DAG runtime selector ranks candidate tasks when enabled.
3. `GlobalController` still checks agent availability and candidate constraints.
4. The selected task is executed through the Task Graph/controller loop.

Dual-DAG ranking does not replace Task Graph status, dependency edges, or assignment state. The Task Graph remains the source of truth for execution.

## Artifacts

- `task_graph_snapshot.json`: public snapshot of Task Graph tasks and edges for the run.
- `dual_dag_artifact.json`: public analysis projection of tasks, actions, observations, and claims.
- `decision_support.json`: public read-only recommendation context derived from artifacts.

These artifacts are safe for benchmark analysis and should not expose credentials, hidden evaluator state, private observations from other agents, or simulator debug fields.
