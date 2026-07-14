# Artifact Schema Reference

Minecraft benchmark runs write normalized public artifacts under the selected run directory.

## `launch_config.json`

- Producer: `benchmarks.minecraft.experiment`.
- Timing: after dry-run fixture construction or after execute mode finishes/fails.
- Modes: dry-run and execute.
- Failure behavior: written even when execute mode raises or times out.
- Classification: public sanitized config; credential-like fields are removed.
- Schema: sanitized copy of the selected Minecraft config object.

## `action_log.json`

- Producer: dry-run fixture loader or real runtime via `data/action_log.json`.
- Timing: before metrics/artifact generation.
- Modes: dry-run and execute.
- Failure behavior: written with available data, or `{}` if no log exists.
- Classification: public sanitized action records.
- Schema: mapping from agent name to action records with action/tool name, kwargs, duration, and result when available.

## `task_graph_snapshot.json`

- Producer: `benchmarks.minecraft.experiment._task_graph_snapshot()`.
- Timing: during normalized artifact generation.
- Modes: dry-run and execute.
- Failure behavior: written even on execute failure/timeout.
- Classification: public compatibility projection of canonical runtime task DAG state.
- Required fields: `mutates_runtime`, `tasks`, `edges`.

## `runtime_dual_dag_snapshot.json`

- Producer: `pipeline.dual_dag_task_store.RuntimeTaskDAGStore.snapshot()`.
- Timing: during normalized artifact generation.
- Modes: dry-run and execute.
- Failure behavior: written even on execute failure/timeout.
- Classification: canonical public runtime task subgraph artifact. The filename is retained for compatibility.
- Producer details: dry-run uses a config fixture store; execute mode prefers the real runtime result from `start_with_config.run()` or `.cache/minecraft_runtime_result.json`.
- Required fields include `schema_version`, `runtime`, `source_of_truth`, `snapshot_source`, `summary`, `nodes`, `edges`, and `schema`.
- Runtime task lifecycle fields include `status`, `candidate_agents`, `active_agents`, `last_assigned_agents`, and `required_agent_count`. `available` is derived and is not canonical stored lifecycle state.
- For multi-agent execution, `active_agents` contains the complete running group and terminal transitions preserve that group in `last_assigned_agents`. `content.reflect.agent_results` records each agent's `success`, `failure`, or `timeout` result; the task has one terminal status. Single-agent tasks retain the existing direct detail value in `content.reflect` for compatibility.

## `dual_dag_artifact.json`

- Producer: `env.minecraft_dual_dag.build_minecraft_dual_dag_artifact()`.
- Timing: after task/action-log collection.
- Modes: dry-run and execute.
- Failure behavior: written even on execute failure/timeout.
- Classification: public analysis projection.
- Required fields include `schema_version`, `schema`, `nodes`, `edges`, and `summary`.

## `decision_support.json`

- Producer: `env.minecraft_dual_dag.build_minecraft_runtime_decision_support()`.
- Timing: after Dual-DAG artifact generation.
- Modes: dry-run and execute.
- Failure behavior: written even on execute failure/timeout.
- Classification: public read-only recommendation context.
- Required fields include `mode`, `mutates_runtime`, `recommended_task_id`, `recommended_description`, and `candidates`.

## `metrics.json`

- Producer: `benchmarks.minecraft.metrics.build_minecraft_metrics()`.
- Timing: after summary, action log, graph snapshot, and decision support are available.
- Modes: dry-run and execute.
- Failure behavior: written even on execute failure/timeout.
- Classification: public metrics.
- Fields include task counts, completion rate, action counts, failure counts, timing, recommendation adoption, `error`, `error_type`, and `timed_out`.

## `summary.json`

- Producer: `benchmarks.minecraft.experiment`.
- Timing: immediately before metrics are written.
- Modes: dry-run and execute.
- Failure behavior: written even on execute failure/timeout.
- Classification: public run summary.
- Fields include `run_name`, `mode`, `started_at`, `output_dir`, `task_name`, `task_type`, `task_idx`, `dual_dag_runtime_enabled`, `dual_dag_task_selection_enabled`, `task_selection_policy`, `runtime_task_store`, `source_of_truth`, `snapshot_source`, `execute_real_environment`, `execute_timeout_seconds`, `mutates_runtime`, `artifact_summary`, `recommended_task_id`, `recommended_description`, `task_order`, `ranked_task_order`, `selected_task_id`, `selected_description`, `final_score`, `progress`, `error`, `error_type`, and `timed_out`.

## Provenance Files

- Producer: `benchmarks.experiment_provenance.write_provenance()`.
- Output: `command.txt`, `config.resolved.json`, and `provenance.json`.
- Modes: dry-run and execute.
- Failure behavior: written after normalized artifacts are produced.
- Classification: public sanitized command/config/provenance metadata.

## Versioning

Artifacts that already expose a schema version keep it in the payload. Artifacts without `schema_version` should be treated as versioned by producer and repository commit until a future migration adds explicit versions.
