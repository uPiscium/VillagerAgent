# Execution Flow

This is the current Ollama-first flow used by `start_with_config.py` and the Minecraft benchmark harness.

## Entrypoints

- `start_with_config.py`: real configured VillagerBench execution.
- `benchmarks.minecraft.experiment`: dry-run or real execute harness with normalized artifacts.
- `benchmarks.minecraft.matrix`: CI-safe dry-run matrix wrapper.
- `tiny_start.py`: small manual startup example.

## Real Runtime Flow

1. Load a Minecraft config object.
2. Build Ollama LLM config with `make_ollama_llm_config()`.
3. Configure the static Minecraft `Agent` wrapper with `configure_ollama_agent()`.
4. Create `VillagerBench` for the requested `task_type`, `task_idx`, `host`, `port`, and `task_name`.
5. Select Minecraft tools for the task type.
6. Register `Alice`, `Bob`, and other agents as needed.
7. Enter `env.run(fast_api=True)`, which starts the FastAPI bridge path.
8. Initialize `DataManager` from `env.get_init_state()`.
9. Initialize `TaskManager` and attach its LLM.
10. Create `GlobalController` with task manager, data manager, environment, tools, and task selection policy config.
11. Load optional task document data and call `TaskManager.init_task()`.
12. Run `GlobalController.run()` until graph completion, task failure, shutdown, or timeout.
13. Call `env.get_score()` and leave the environment context.

## Controller Loop

`GlobalController` maintains assignment state, a task queue, and a result queue.

1. Poll agent health with `env.agents_ping()`.
2. Request open tasks from `TaskManager` to detect completion.
3. Compute free agent names.
4. Request runnable tasks through `TaskManager.query_runnable_subtasks(free_agent_names)`, which delegates runnable filtering to `RuntimeTaskDAGStore`.
5. Order runnable tasks with `task_selection_policy` (`dual-dag` or `original`).
6. For each runnable task in policy order, select exactly `required_agent_count` free candidate agents. Each accepted assignment reserves its agents immediately, so later tasks in the same iteration cannot reuse them.
7. Validate that the task and agents exist, every agent is idle and eligible, agent names are unique, and the validated count exactly matches `required_agent_count`.
8. Mark the task `running`, create one execution group, and submit `BaseAgent.step()` once for every assigned agent. Other independent tasks can be assigned in the same scheduler iteration while enough eligible agents remain.
9. Track all futures by agent name and wait for the entire group. A pending group remains `running`.
10. Reflect on each completed result through its `BaseAgent.reflect()`. Any exception, timeout, or failed reflection makes the group fail; every reflection must succeed for group success.
11. Write one terminal task update with an `agent_results` map. This clears `active_agents` while preserving the full group in `last_assigned_agents`.

Example terminal feedback:

```json
{
  "agent_results": {
    "Alice": {"status": "success", "detail": "..."},
    "Bob": {"status": "failure", "error": "..."}
  }
}
```

## Agent Step

`BaseAgent.step()` chooses between virtual, RL, local, or normal execution. The common normal path:

1. Builds a prompt from task description, milestones, relevant task content, and `DataManager` state queries.
2. Calls the configured LLM.
3. Parses tool calls and final answer.
4. Executes Minecraft tools through `VillagerBench` and `env.minecraft_client`.
5. Records action feedback.
6. Returns final answer and detail payload for controller reflection.

## Data Manager Role

`DataManager` owns summarized runtime state:

- Environment state from `env.get_init_state()`.
- Agent status, position, inventory, nearby entities, and held items.
- History summaries and action outcomes.
- Experience retrieval from `data/experience.json` when enabled.

The task manager and agents query this state instead of reading raw bridge payloads directly.

## Benchmark Artifact Flow

`benchmarks.minecraft.experiment` always writes public, normalized artifacts:

- `launch_config.json`
- `action_log.json`
- `task_graph_snapshot.json`
- `runtime_dual_dag_snapshot.json`
- `dual_dag_artifact.json`
- `decision_support.json`
- `metrics.json`
- `summary.json`
- provenance files from `benchmarks.experiment_provenance`

Dry-run builds task artifacts from fixture data in the config and marks `runtime_dual_dag_snapshot.json` with `snapshot_source="config_fixture"`. Execute mode runs the real runtime first and uses the structured return value or the atomically completed `<output_dir>/.runtime/runtime_result.json` checkpoint when available. Every run has a separate checkpoint path; incomplete `.tmp` files and stale global cache files are ignored. Execute snapshots from the controller are marked `snapshot_source="real_runtime"`, reconstructed into compatibility `Task`/`Graph` values, and used consistently by the task artifact, decision support, graph fallback, summary order, and post-hoc ranking. `task_state_source` records this choice. Runtime-selected task IDs are reported only when explicitly captured; post-hoc ranking is separate. If execute mode fails or times out, partial artifacts are still written with `error`, `error_type`, and `timed_out` in `summary.json`. The internal checkpoint is removed after normalization unless `--retain-runtime-result` is set.

Real execute runs inside a top-level child-process entrypoint. The parent joins with the configured timeout and, on timeout, reads the latest completed checkpoint, terminates and joins the child, then kills and joins it if it remains alive. Only after the child has stopped does the parent normalize artifacts. This boundary contains `GlobalController` threads, executor workers, and the Minecraft bridge in the child and removes the previous parent-process `SIGALRM` dependency.

## Boundaries

Benchmark reports and adapter artifacts are public-analysis views. They should not expose hidden evaluator state, credentials, private observations from other agents, raw CRAFT internals, or simulator debug fields.
