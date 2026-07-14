# Villager Agent Benchmark Integration Plan

Issue: #209

## Purpose

The Villager Agent benchmark should measure the repository's native Minecraft/VillagerBench runtime without mixing it into the existing CRAFT or C-WAH integrations. The initial implementation should reuse the current Minecraft dry-run harness and normalized artifact shape, then add the smallest real-environment execution path needed for bounded validation.

This plan is intentionally pre-implementation. It defines scope, state boundaries, artifacts, validation, and follow-up work before changing benchmark behavior.

## Current Inventory

Runtime entrypoints:

- `start_with_config.py`: main configurable VillagerBench runtime. It builds `VillagerBench`, registers agents/tools by task type, starts `DataManager`, `TaskManager`, and `GlobalController`, runs the controller, and reads the final score.
- `tiny_start.py`: minimal example for launching one Minecraft agent against a server through `VillagerBench` and the controller stack.
- `env/env.py`: `VillagerBench` environment wrapper. It launches the Minecraft bridge, starts task-specific judgers, registers agents, records action logs, exposes environment state, and reads scores.
- `benchmarks/minecraft/experiment.py`: existing benchmark harness. It writes normalized artifacts in dry-run mode by default and can call the real runtime with `execute=True`.

Task and environment types:

- `env_type.construction`: uses `env/build_judger.py`, optional `dig_needed`, and `data/score.json`.
- `env_type.farming`: uses `env/farm_craft_judger.py`, task-specific material hints, and `data/score.json`.
- `env_type.puzzle`: uses `env/escape_room_judger.py`, `max_task_num`, and `data/score.json`.
- `env_type.meta`: uses `env/meta_judger.py` and optional `evaluation_arg` document context.
- `env_type.gen`: uses `env/llm_gen_judger.py`.
- `env_type.none`: launches only the agent bridge and is useful for non-scored smoke checks.

Existing normalized artifacts:

- `summary.json`: run metadata, selected/recommended task ids, score/progress, error, and artifact counts.
- `metrics.json`: scalar normalized metrics documented in `benchmarks/minecraft/METRICS_SCHEMA.md`.
- `action_log.json`: sanitized public action log from `data/action_log.json` or dry-run fixture input.
- `task_graph_snapshot.json`: sanitized read-only task graph projection.
- `dual_dag_artifact.json`: read-only Minecraft Dual-DAG-style projection built by `env/minecraft_dual_dag.py`.
- `decision_support.json`: read-only task recommendation context.
- `launch_config.json`, `config.resolved.json`, `command.txt`, and `provenance.json`: sanitized launch/provenance metadata.

Existing tests:

- `tests/test_minecraft_experiment.py`: dry-run artifact generation, run-name sanitization, config-list selection, task reordering, and metric extraction.
- `tests/test_minecraft_dual_dag.py`: task/action/observation/claim mapping, credential/private-field sanitization, read-only decision support, and config-gated task ranking.

Prior real-runtime evidence:

- `docs/benchmarks/minecraft_real_run.md` records the current bounded real-run procedure and the prior minimal `env_type.none` connectivity evidence.
- That verification did not run a judged benchmark task or claim task completion performance.

## Benchmark Definition

The first Villager Agent benchmark should be a Minecraft/VillagerBench run matrix with two modes:

- Dry-run mode: CI-safe artifact contract validation using configured smoke tasks and smoke action logs. This remains the default.
- Execute mode: bounded real-environment validation against a configured Minecraft server and judger, preserving artifacts even when the run fails.

The benchmark unit should be one run of one launch-config entry. A later matrix wrapper can aggregate multiple config entries, seeds, task types, or agent counts.

The first real validation should use a small, resettable task and a bounded controller/run timeout. `env_type.none` is acceptable only for connectivity smoke tests; scored benchmark validation should use a judged task type that produces `data/score.json`.

## Common Protocol Integration

The implementation should not reimplement CRAFT Dual-DAG runtime or C-WAH adapters. It should add a Minecraft-specific adapter only where useful:

- Use `benchmarks.common` dataclasses and protocol shapes for new integration seams.
- Keep `env/minecraft_dual_dag.py` as a read-only projection of public Minecraft records.
- Keep `benchmarks/minecraft/experiment.py` as the canonical harness entrypoint.
- Extend `benchmarks/common/report.py` to summarize Minecraft `summary.json` and `metrics.json` artifacts instead of creating a separate reporting stack.

The initial adapter can be thin. It does not need to drive every VillagerBench step through `BenchmarkAdapter` if the legacy controller remains the execution owner. It should expose common metadata, final metrics, and sanitized observations/artifacts first.

## Leakage Boundaries

Agent-facing context may include:

- The agent's own current public environment state returned by the runtime.
- Tool descriptions/capabilities for that agent.
- Public chat/messages intended for that agent.
- The task goal and public task document fields already supplied by the launch config.
- Public action feedback from that agent's own tool calls.

Agent-facing context must not include:

- Evaluator progress, final score, or `data/score.json` before the episode is complete.
- Full normalized artifacts, common report rows, or `dual_dag_artifact.json` internals during action selection.
- Private fields, underscore-prefixed fields, credentials, API keys, tokens, passwords, base URLs, or raw LLM provider config.
- Private observations or tool feedback belonging only to another agent unless communicated through the environment's public/message channels.
- Judger/debug process internals, simulator control fields, `.cache` state, or hidden server logs.

Report-facing artifacts may include sanitized aggregate diagnostics after the run. These diagnostics are for analysis only and must not be fed back into the active episode unless explicitly converted into public observations by the environment.

## Artifact Contract

The implementation should preserve the current Minecraft artifact set and add only fields needed by common reporting:

- `benchmark`: `minecraft` or `villager_agent` should be chosen once and used consistently. Prefer `minecraft` only if maintaining compatibility with existing files is more important than naming the benchmark after the project.
- `run_name`, `task_id`, `task_type`, `task_idx`, `seed` when available.
- `mode`: `dry_run` or `execute`.
- `status`: completed, failed, or timed_out.
- `episodes`: `1` for a single run.
- `successes` or a success boolean derived from the score schema when available.
- `progress`: normalized numeric score/progress when available.
- `mean_steps` or action count proxy when true step counts are unavailable.
- Physical/communication action counts derived from `action_log.json`.
- Failed action counts and retry/replan counts from public action results.
- Sanitized `error_type` and `error_message` for failed real runs.

`benchmarks/minecraft/METRICS_SCHEMA.md` should be updated when fields are added or renamed.

## Runtime Assets And Assumptions

Real execute mode requires runtime assets that should not be committed:

- A reachable Minecraft server and world appropriate for the selected task.
- Working mineflayer/FastAPI bridge dependencies.
- Judger scripts for the selected `env_type`.
- Local `API_KEY_LIST` or equivalent model configuration.
- Writable runtime directories such as `data/`, `.cache/`, `logs/`, and `result/`.

The dry-run path must not require those assets. Tests should continue to validate artifact contracts without launching Minecraft, mineflayer, external LLMs, or judgers.

## Validation Plan

Dry-run validation:

- Keep `pytest tests/test_minecraft_experiment.py tests/test_minecraft_dual_dag.py` passing.
- Add common-report tests once Minecraft summarization is implemented.
- Run full `just test` before merging implementation slices.

Real validation:

- First run a bounded `env_type.none` connectivity check only when server access is available.
- Then run one judged task with a bounded timeout and preserve artifacts on error.
- Record command, config, server assumptions, observed score/progress, and any failure mode in a verification note.
- Do not claim performance unless multiple comparable scored runs show task success/progress improvements.

## Follow-Up Implementation Issues

The implementation is split into these issues:

- #224: Add common-report support for Minecraft/Villager Agent artifacts.
- #225: Add a CI-safe Minecraft benchmark matrix wrapper around `benchmarks.minecraft.experiment`.
- #226: Add a bounded real-run validation path and documentation for VillagerBench runtime assets.
- #227: Add an optional thin common-protocol adapter for Minecraft/VillagerBench metadata and sanitized observations.

## Non-Goals

- Do not modify CRAFT Dual-DAG runtime for this benchmark.
- Do not refactor C-WAH policy code for Minecraft needs.
- Do not make real Minecraft server access a CI requirement.
- Do not expose evaluator or private runtime state to agents.
- Do not claim benchmark performance from dry-runs or single connectivity checks.
