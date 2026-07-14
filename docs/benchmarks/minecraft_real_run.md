# Minecraft Real-Run Validation

Issue: #226

## Purpose

Minecraft/Villager Agent benchmark execute mode is optional and must stay outside CI. The default benchmark paths remain dry-run artifact validation. Real execute mode is for local, bounded checks against an explicitly configured Minecraft server and judger.

## Required Runtime Assets

Do not commit these assets or generated outputs:

- A reachable Minecraft server and resettable world for the selected task.
- Working mineflayer/FastAPI bridge dependencies used by `env.minecraft_client.Agent`.
- The relevant judger script for the selected `task_type`:
- `construction`: `env/build_judger.py`.
- `farming`: `env/farm_craft_judger.py`.
- `puzzle`: `env/escape_room_judger.py`.
- `meta`: `env/meta_judger.py`.
- `gen`: `env/llm_gen_judger.py`.
- Local model configuration and credentials, usually `API_KEY_LIST` or equivalent Ollama configuration.
- Writable runtime directories: `data/`, `.cache/`, `logs/`, and `result/`.

## Bounded Execute Command

Use `--execute-timeout-seconds` whenever `--execute` is set:

```bash
python -m benchmarks.minecraft.experiment \
  --config /path/to/minecraft_config.json \
  --output-root result/minecraft_real \
  --run-name bounded_real_smoke \
  --execute \
  --execute-timeout-seconds 600
```

Each execute run checkpoints partial state under its own `<output_dir>/.runtime/runtime_result.json`. The checkpoint is atomically replaced and removed after normalized artifacts are written. Add `--retain-runtime-result` only when the internal checkpoint is needed for debugging.

Run and matrix output directories are single-attempt bundles and are rejected when already non-empty. Pass `--overwrite` explicitly to replace a previous bundle; the new run receives a different attempt ID and artifact manifest.

The real runtime runs in a child process. The parent waits up to `--execute-timeout-seconds`, reads any completed partial checkpoint, sends terminate, waits for a grace period, and uses kill as a fallback before generating timeout artifacts. A timeout therefore cannot leave controller/executor threads in the parent process or continue Minecraft actions from the child.

For a matrix run, pass the same bound to `benchmarks.minecraft.matrix`:

```bash
python -m benchmarks.minecraft.matrix \
  --config /path/to/minecraft_config_list.json \
  --output-dir result/minecraft_real_matrix \
  --execute \
  --execute-timeout-seconds 600
```

The harness preserves normalized artifacts even when the runtime raises or times out:

- `summary.json`
- `metrics.json`
- `action_log.json`
- `task_graph_snapshot.json`
- `runtime_dual_dag_snapshot.json`
- `dual_dag_artifact.json`
- `decision_support.json`
- provenance files

Execute mode consumes action logs and scores only from the current run-local runtime result. Repository-global `data/action_log.json` and `data/score.json` files are not fallback inputs because they may belong to an earlier attempt.

Timeout metadata is recorded in `summary.json`:

- `execute_timeout_seconds`
- `error`
- `error_type`
- `timed_out`
- `snapshot_source`
- `task_state_source`
- `runtime_process_isolated`
- `runtime_process_exit_code`
- `runtime_process_terminated`
- `runtime_process_killed`

In dry-run, `snapshot_source` is `config_fixture`. In execute mode, it is `real_runtime` when the harness recovers a runtime task DAG snapshot from `start_with_config.run()` or the run-local `.runtime/runtime_result.json`. If no real runtime snapshot exists, the harness falls back to the config fixture snapshot for artifact completeness.

`task_state_source` applies the same provenance to `dual_dag_artifact.json`, `decision_support.json`, summary task order, and post-hoc ranking. When it is `real_runtime`, those outputs are reconstructed from the runtime task nodes and `precedes_task` edges rather than the pre-run fixture. `runtime_selected_task_ids` contains only selection history explicitly returned by the runtime; `posthoc_ranked_task_order` is analysis performed after the run and is not runtime history.

Mutation metadata separates four concerns. `mutates_environment` is `true` for execute mode, including failed and timed-out attempts, and `false` for dry-run. `artifact_generation_mutates_runtime` is `false` because normalized artifact and decision-support builders are read-only. `task_selection_mutates_order` reports whether the selected policy can reorder tasks (`false` for `original`, `true` for `dual-dag`), while `task_order_changed` reports whether the ranked task IDs actually differ from their input order. The deprecated `mutates_runtime: false` field is retained for compatibility with consumers of the old read-only projection metadata and must not be interpreted as environment mutation.

## Connectivity Smoke Check

When server access is available, start with `env_type.none` before running a judged task. This checks the bridge and a non-destructive action without measuring benchmark success.

Prior local connectivity evidence verified Python `VillagerBench` to FastAPI/mineflayer bridge to a remote Minecraft server and back, but it did not launch a benchmark judger or measure scored task completion. Keep new evidence in this document or in run-specific notes under `result/`; the legacy documentation directory has been removed.

## Tool Limitations

The current runtime does not implement deterministic anvil or enchanting table interactions. Calls routed through `env.env_api.interact_nearest()` for `anvil` or `enchanting_table` return `status=False` with structured detail:

```json
{
  "error_type": "unsupported_tool",
  "tool": "anvil",
  "supported": false
}
```

Tasks that require these tools should be treated as unsupported until an implementation with mocked bridge tests is added.

## Judged Task Check

After connectivity is confirmed, run one small judged task on a resettable server/world. Prefer a single-agent construction task first because it should produce `data/score.json` through `env.get_score()`.

Record the following in a verification note before making any performance claims:

- Command and config path.
- Git branch and commit.
- Server host, port, and world assumptions.
- Task type, task index, agent count, and timeout.
- Whether `.cache/load_status.cache` reached `loaded`.
- Whether `data/score.json` was produced.
- Observed `summary.json` `progress`, `error`, `error_type`, and `timed_out` fields.
- Any runtime failure mode.

## Validation Status For This Change

This change validates the bounded execute artifact path without requiring a server:

- A monkeypatched runtime error preserves `summary.json` and `metrics.json` and records `error_type == "RuntimeError"`.
- A monkeypatched slow runtime triggers `--execute-timeout-seconds`, preserves artifacts, and records `error_type == "timeout"` and `timed_out == true`.
- A monkeypatched runtime snapshot with a task different from the config fixture drives execute task artifacts, lifecycle status, dependency edges, and post-hoc ranking without mixing fixture state.

No real Minecraft server was launched for this repository change, and no benchmark performance is claimed.
