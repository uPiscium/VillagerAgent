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
- Required fields: `artifact_generation_mutates_runtime`, deprecated `mutates_runtime`, `tasks`, and `edges`. Projection generation is read-only, so both mutation fields are `false`.

## `runtime_dual_dag_snapshot.json`

- Producer: `pipeline.dual_dag_task_store.RuntimeTaskDAGStore.snapshot()`.
- Timing: during normalized artifact generation.
- Modes: dry-run and execute.
- Failure behavior: written even on execute failure/timeout.
- Classification: canonical public runtime task subgraph artifact. The filename is retained for compatibility.
- Producer details: dry-run uses a config fixture store; execute mode prefers the real runtime result returned by `start_with_config.run()` or atomically checkpointed at `<output_dir>/.runtime/runtime_result.json`.
- Required fields include `schema_version`, `runtime`, `source_of_truth`, `snapshot_source`, `summary`, `nodes`, `edges`, and `schema`.
- Runtime task lifecycle fields include `status`, `candidate_agents`, `active_agents`, `last_assigned_agents`, and `required_agent_count`. `available` is derived and is not canonical stored lifecycle state.
- Every runtime task node has derived `dependency_ready`, deprecated `blocked_by_tasks`, and structured `dependency_blockers`. Each blocker contains `task_id`, `description`, non-success `status`, and `relation` (`direct` or `transitive`). `blocked_by_tasks` remains an ID-only compatibility field and now includes all non-success predecessors, including failures.
- `mutation_history` is an additive ordered list of store-first replan operations with revision, operation, affected task IDs, and source. Retained nodes also carry revision provenance; decomposition child provenance retains parent execution history.
- For multi-agent execution, `active_agents` contains the complete running group and terminal transitions preserve that group in `last_assigned_agents`. `content.reflect.agent_results` records each agent's `success`, `failure`, or `timeout` result; the task has one terminal status. Single-agent tasks retain the existing direct detail value in `content.reflect` for compatibility.

## `dual_dag_artifact.json`

- Producer: `env.minecraft_dual_dag.build_minecraft_dual_dag_artifact()`.
- Timing: after task/action-log collection.
- Modes: dry-run and execute.
- Failure behavior: written even on execute failure/timeout.
- Classification: public analysis projection.
- Required fields include `schema_version`, `schema`, `nodes`, `edges`, `summary`, `task_state_source`, and `artifact_generation_mutates_runtime` (`false`).
- `task_state_source` is `config_fixture` for dry-run and `real_runtime` when execute mode recovers a runtime task snapshot. Runtime task lifecycle/provenance is projected into task nodes before action-log analysis.

## `decision_support.json`

- Producer: `env.minecraft_dual_dag.build_minecraft_runtime_decision_support()`.
- Timing: after Dual-DAG artifact generation.
- Modes: dry-run and execute.
- Failure behavior: written even on execute failure/timeout.
- Classification: public read-only recommendation context.
- Required fields include `mode`, `artifact_generation_mutates_runtime`, deprecated `mutates_runtime`, `recommended_task_id`, `recommended_description`, `candidates`, and `task_state_source`. Candidate tasks use the same source as `dual_dag_artifact.json`; recommendation generation is read-only.

## `metrics.json`

- Producer: `benchmarks.minecraft.metrics.build_minecraft_metrics()`.
- Timing: after summary, action log, graph snapshot, and decision support are available.
- Modes: dry-run and execute.
- Failure behavior: written even on execute failure/timeout.
- Classification: public metrics.
- Fields include task counts, completion rate, action counts, failure counts, timing, recommendation adoption, `error`, `error_type`, `timed_out`, and the four explicit mutation fields copied from the run summary.

## `summary.json`

- Producer: `benchmarks.minecraft.experiment`.
- Timing: immediately before metrics are written.
- Modes: dry-run and execute.
- Failure behavior: written even on execute failure/timeout.
- Classification: public run summary.
- Task provenance fields include `snapshot_source` and `task_state_source` (`config_fixture` or `real_runtime`).
- Selection fields distinguish `runtime_selection_policy`, recorded `runtime_selected_task_ids`, and `posthoc_ranked_task_order`. `ranked_task_order` remains a compatibility alias for the post-hoc order. In execute mode, `selected_task_id` and `selected_description` are empty unless runtime selection history was recorded; post-hoc ranking is never presented as runtime history.
- Mutation fields are `mutates_environment` (true for execute, false for dry-run), `artifact_generation_mutates_runtime` (always false), `task_selection_mutates_order` (whether the selected policy can reorder), and `task_order_changed` (whether ranked task IDs differ from their input order). A Dual-DAG policy can therefore report `task_selection_mutates_order: true` with `task_order_changed: false`.
- `mutates_runtime` remains `false` as deprecated compatibility metadata for the read-only projection behavior. It does not describe Minecraft environment mutation; consumers must use the explicit fields above.
- Other fields include `run_name`, `mode`, `started_at`, `output_dir`, `task_name`, `task_type`, `task_idx`, `dual_dag_runtime_enabled`, `dual_dag_task_selection_enabled`, `task_selection_policy`, `runtime_task_store`, `source_of_truth`, `execute_real_environment`, `execute_timeout_seconds`, `artifact_summary`, `recommended_task_id`, `recommended_description`, `task_order`, `final_score`, `progress`, `error`, `error_type`, and `timed_out`.
- Execute process fields are `runtime_process_isolated`, `runtime_process_exit_code`, `runtime_process_terminated`, and `runtime_process_killed`. Timeout summaries are written only after the child is no longer alive.

## Provenance Files

- Producer: `benchmarks.experiment_provenance.write_provenance()`.
- Output: `command.txt`, `config.resolved.json`, and `provenance.json`.
- Modes: dry-run and execute.
- Failure behavior: written after normalized artifacts are produced.
- Classification: public sanitized command/config/provenance metadata.
- Credential values are recursively replaced with `[REDACTED]`; credential-source fields such as `api_key_env` remain so the authentication setup is reproducible without exposing the value.
- Commands redact credential flags and known secret literals before writing. Benchmark subprocess output and failure summaries apply the same literal redaction when a runtime credential is available.

## Run Attempt And Artifact Manifest

- Producers: CRAFT, C-WAH matrix/baseline, and Minecraft single/matrix harnesses.
- `attempt.json` is written before benchmark artifacts and contains a unique `attempt_id`, producer, and lifecycle status.
- Existing non-empty run directories are rejected by default. `--overwrite` explicitly replaces the complete run directory and starts a new attempt ID; implicit resume is not supported.
- JSON, JSONL, CSV, and YAML artifacts carry the attempt ID when the bundle is finalized.
- `artifact_manifest.json` records the attempt ID, producer, final status, relative artifact paths, byte sizes, and SHA-256 hashes.
- `_COMPLETED` is written last and only for successfully finalized harness runs. Failed or interrupted attempts do not carry this marker.
- Matrix aggregators validate child attempt manifests before accepting child results. C-WAH additionally rejects summaries whose attempt ID does not match the launched child.

## Internal Runtime Result

- Path: `<output_dir>/.runtime/runtime_result.json`; each run has its own path, including matrix runs.
- Classification: internal checkpoint used to recover partial execute state. It is not a normalized artifact.
- Writes use `runtime_result.json.tmp`, flush/fsync, and `os.replace()`; readers consume only the completed JSON path.
- The child checkpoints after TaskManager initialization, decomposition, running transitions, terminal transitions, normal completion, and exceptions. Lifecycle checkpoints prioritize the runtime task snapshot and current action log without repeatedly evaluating score.
- The checkpoint and temporary file are removed after normalized artifact generation by default. `--retain-runtime-result` keeps the completed checkpoint for debugging and records `runtime_result_retained: true`.

## Versioning

Artifacts that already expose a schema version keep it in the payload. Artifacts without `schema_version` should be treated as versioned by producer and repository commit until a future migration adds explicit versions.
