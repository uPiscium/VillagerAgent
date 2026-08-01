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

## Nested restart failure diagnostics

- `runtime_diagnostics.restart_failure_evidence` uses `schema_version: 2` for separated streams and structured state.
- It always contains `inspect_state`, `logs_tail`, `events_window`, and `ps_exact_name` records, plus `collection_complete` and `target_valid`.
- Each command record contains `outcome`, `exit_code`, and separate `stdout` and `stderr` records. Each stream record contains `safe_output`, `raw_bytes`, `retained_safe_lines`, `redacted_line_count`, `dropped_line_count`, and `truncated`. Sensitive tokens inside otherwise useful lines are replaced with `[REDACTED]` instead of discarding the line.
- `inspect_state.state` is an allowlisted structured projection containing lifecycle flags, exit code, error, `started_at`, `finished_at`, restart count, and bounded health status, failing streak, and health-log records. Outcomes also include `invalid_output` when inspect output cannot be parsed.
- `diagnostics_implementation_sha256` identifies the diagnostic component in failure artifacts. The approved composite runtime identity also hashes this module, so diagnostic changes require a newly approved premanifest.
- Invalid targets, exhausted collection budgets, and collector failures retain the record with an explicit non-attempted/error outcome; keys are never omitted. Output is bounded and strictly sanitized before it is placed in the public failure detail.

## `events.jsonl`

- Producer: optional Minecraft normalized event producer combining the internal runtime journal, `action_log.json`, and observation/claim entities from `dual_dag_artifact.json`.
- Runtime events retain their original event ID and sequence under `provenance`; the public file receives stable contiguous normalized sequences after ordering.
- Events with valid `occurred_at` timestamps are ordered chronologically. Equal or unknown times retain stable producer order, and unknown timing remains `null`.
- Action log rows produce `action_recorded`, never fabricated `action_started` or `action_completed` hooks. Observation and claim projection entities produce at most one event per stable entity ID.
- Payloads are sanitized. An incomplete runtime journal tail becomes a warning, and producer failure leaves all existing normalized artifacts and run results intact.

## Provenance Files

- Producers: the CRAFT, C-WAH, and Minecraft single-run and matrix harnesses through `benchmarks.experiment_provenance`.
- Output: `command.txt`, `config.resolved.json`, and `provenance.json`.
- Modes: dry-run and execute.
- Failure behavior: started before benchmark execution and finalized before the artifact manifest on success, runtime failure, or timeout. Partial bundles therefore retain terminal provenance.
- Classification: public sanitized command/config/provenance metadata.
- Credential values are recursively replaced with `[REDACTED]`; credential-source fields such as `api_key_env` remain so the authentication setup is reproducible without exposing the value.
- Commands redact credential flags and known secret literals before writing. Benchmark subprocess output and failure summaries apply the same literal redaction when a runtime credential is available.
- `provenance.json` schema `2.0.0` is shared by all three benchmarks. Common fields are `benchmark`, `lifecycle` (`started_at`, `ended_at`, `duration_seconds`, `status`), safe `argv`, `interpreter`, `platform`, `repository` (`sha`, `dirty`), `dependency_lock`, sanitized `effective_settings`, `assets`, `environment_unverifiable`, and `unverifiable_reasons`. The top-level `commit` field remains as an additive compatibility alias for `repository.sha`.
- Terminal provenance status is `success`, `failure`, or `timeout`; this is more specific than the artifact manifest's `completed`/`failed` status.
- Local files record byte size and SHA-256. Directory fingerprints hash sorted relative paths, sizes, and contents. Used Git repositories record HEAD SHA and dirty state. Dependency identity fingerprints recognized lock files, including `flake.lock`.
- Required assets that are absent or cannot expose an immutable identity do not prevent failure artifacts from being written. They set `environment_unverifiable: true` and add a machine-readable reason.
- Model assets require an immutable digest, revision, or provider system fingerprint. CRAFT and C-WAH Ollama runs record the tag digest returned by `/api/tags`; C-WAH hosted-provider runs retain immutable metadata exposed on responses. Minecraft accepts `model_digest`, `model_revision`, or `model_system_fingerprint` from the launch config. Providers that do not expose immutable metadata are explicitly unverifiable rather than treating a mutable model name as identity.
- C-WAH records CoELA repository state, dataset, VirtualHome executable, and model identity. Minecraft execute mode records server version/protocol, world/reset snapshot, bridge, selected task config, judger, and model identity. Configure Minecraft file identities with `world_snapshot_path` (or `reset_snapshot_path`) and `bridge_path`, and server identity with `server_version` and `server_protocol`.
- Matrix provenance contains the expanded `run_plan`; each matrix summary run has a `provenance` path referencing its child record. The C-WAH baseline report directory has its own shared-schema provenance and fingerprints the matrix provenance it reports. CRAFT experiments write `<report-name>.manifest.json` (or `report.manifest_output`) with the expanded overrides and child provenance paths.

## Run Attempt And Artifact Manifest

- Producers: CRAFT, C-WAH matrix/baseline, and Minecraft single/matrix harnesses.
- `attempt.json` is written before benchmark artifacts and contains a unique `attempt_id`, producer, and lifecycle status.
- Existing non-empty run directories are rejected by default. `--overwrite` replaces only a managed directory whose `attempt.json` belongs to the same benchmark family, then starts a new attempt ID; unmanaged or cross-benchmark directories are never recursively deleted. Implicit resume is not supported.
- JSON, JSONL, CSV, and YAML artifacts carry the attempt ID when the bundle is finalized.
- `artifact_manifest.json` records the attempt ID, producer, final status, relative artifact paths, byte sizes, and SHA-256 hashes.
- Manifest validation rejects absolute or parent-traversing paths, symlinked artifacts, missing or unlisted entries, duplicate paths, and checksum mismatches. Matrix manifests include nested child manifests and artifacts without rewriting child attempt IDs.
- `_COMPLETED` is written last and only for successfully finalized harness runs. Failed or interrupted attempts do not carry this marker.
- Matrix aggregators validate child attempt manifests before accepting child results. C-WAH additionally rejects summaries whose attempt ID does not match the launched child.

## Internal Runtime Result

- Path: `<output_dir>/.runtime/runtime_result.json`; each run has its own path, including matrix runs.
- Classification: internal checkpoint used to recover partial execute state. It is not a normalized artifact.
- Writes use `runtime_result.json.tmp`, flush/fsync, and `os.replace()`; readers consume only the completed JSON path.
- The child checkpoints after TaskManager initialization, decomposition, running transitions, terminal transitions, normal completion, and exceptions. Lifecycle checkpoints prioritize the runtime task snapshot and current action log without repeatedly evaluating score.
- The checkpoint and temporary file are removed after normalized artifact generation by default. `--retain-runtime-result` keeps the completed checkpoint for debugging and records `runtime_result_retained: true`.

## Optional Runtime Event Journal

- `RuntimeEventSink` is an optional UI-independent protocol. The default `NoOpRuntimeEventSink` performs no work and preserves existing runtime behavior.
- `JsonlRuntimeEventRecorder` writes an append-only JSONL journal with schema version, run ID, monotonic sequence, stable event ID, event type, emitted/occurred timestamps, entity ID, source, and sanitized payload.
- Writes are thread-safe, flushed, and optionally fsynced. Recorder and sink failures are isolated through `safe_emit_runtime_event` and must not stop runtime execution.
- Registered lifecycle types cover run, task graph, candidate ranking, actual selection, assignment, and task status transitions. Instrumentation is added separately at the authoritative runtime locations.
- Readers ignore an incomplete final line and preserve prior complete events. The internal journal is not the normalized public `events.jsonl` artifact.

## Versioning

Artifacts that already expose a schema version keep it in the payload. Artifacts without `schema_version` should be treated as versioned by producer and repository commit until a future migration adds explicit versions.

Public archive retention, validation, deterministic packaging, recovery, migration, and stable report references are specified in `docs/benchmark_artifact_retention.md`.
