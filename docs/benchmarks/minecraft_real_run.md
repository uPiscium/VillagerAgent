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
- `dual_dag_artifact.json`
- `decision_support.json`
- provenance files

Timeout metadata is recorded in `summary.json`:

- `execute_timeout_seconds`
- `error`
- `error_type`
- `timed_out`

## Connectivity Smoke Check

When server access is available, start with `env_type.none` before running a judged task. This checks the bridge and a non-destructive action without measuring benchmark success.

Prior connectivity evidence is documented in `doc/minecraft_e2e_verification.md`. That run verified Python `VillagerBench` to FastAPI/mineflayer bridge to a remote Minecraft server and back, but it did not launch a benchmark judger or measure scored task completion.

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

No real Minecraft server was launched for this repository change, and no benchmark performance is claimed.
