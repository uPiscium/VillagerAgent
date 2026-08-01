# Minecraft Real-Run Validation

<!-- benchmark-result: minecraft-ollama-real-smoke-v1 -->
<!-- benchmark-result: minecraft-port-real-smoke-v1 -->
<!-- benchmark-result: minecraft-bridge-real-smoke-v1 -->

Issues: #226, #243

## Purpose

Minecraft/Villager Agent benchmark execute mode is optional and must stay outside CI. The default benchmark paths remain dry-run artifact validation. Real execute mode is for local, bounded checks against an explicitly configured Minecraft server and judger.

## Runtime authority

The external judger is authoritative only for evaluator-owned Minecraft outcomes
such as score, progress, and task success or failure, and only after its payload is
verified against the current attempt and runtime task name. `RuntimeTaskDAGStore`
is authoritative for VillagerAgent task lifecycle and assignment state.
`GlobalController` owns execution submission, draining, cancellation, assignment
release, and shutdown.

A judged success is publishable only when the attempt-owned score reports success,
the runtime task DAG is terminal success with every task successful and no active
agents, controller shutdown is complete, and the run has neither an error nor a
timeout. The controller must persist this canonical state before stopping the
environment; a terminal score file alone is not success evidence.

The opt-in Ollama preflight completed against `gemma4:12b` on 2026-07-15 and
recorded immutable model digest
`4eb23ef187e2c5462566d6a1d3bbbc2f1346d0b4327cbb66d58fffbcc9b2b05c`.
Its sanitized immutable archive is registered as
`minecraft-ollama-real-smoke-v1`. This validates only model availability and
provenance; it is not Minecraft bridge, judged-task, or performance evidence.

A non-destructive TCP reachability smoke also connected to the configured
Minecraft endpoint at `10.12.3.1:40000`. Its sanitized evidence is registered
as immutable release `benchmark-minecraft-port-smoke-v1`. Port reachability does
not establish bridge actions, world reset behavior, judged scoring, or benchmark
performance.

The read-only bridge smoke then completed against the same endpoint in 14.2
seconds. It verified bridge ping and `get_environment_info_dict` for agent
`Alice`, and process-session cleanup left no Node or bridge descendants. Its
sanitized evidence is registered as immutable release
`benchmark-minecraft-bridge-smoke-v1`. This is connectivity/integration evidence
only; it does not establish world reset, judged scoring, or task performance.

On 2026-07-26, a bounded single-agent `meta` move run reached the real judger
and task runtime against the same endpoint. The source world was captured while
server autosave was disabled and flushed, then autosave was restored. The local,
git-ignored snapshot is 36,764,270 bytes with SHA-256
`8519378f5d71195ac67294acb318994ef660afdba92eada7289faa9be9f74673`.
The server was Minecraft 1.19.2 (protocol 760), and `meta_judger` had to be a
server operator because the judger creates and resets its arena with commands.

The final 600-second attempt reached `load_status=loaded` and preserved a real
runtime task snapshot, but it produced no score. `gemma4:12b` returned an empty
tool-call result for the first agent action, after which task decomposition kept
retrying a response rejected as `assigned agents not in content`. The normalized
artifacts report `snapshot_source=real_runtime`, `action_count=0`,
`score_available=false`, `progress=null`, and `error_type=timeout`. This is
actionable integration evidence, not a performance result. The retained local
bundle is
`result/minecraft/issue_243_judged_20260726_retry_after_judger_op/`.

The run used git base `fc4f7c0` plus the uncommitted runtime fixes documented
below, task index 0, one agent, and this command:

```bash
VILLAGER_MINECRAFT_JUDGED_SMOKE=1 \
OLLAMA_API_BASE=http://ollama.arc.upiscium.dev/v1 \
OLLAMA_MODEL=gemma4:12b \
python -m benchmarks.minecraft.real_smoke judged \
  --config configs/minecraft/experiments/comparison-2026-07-20.json \
  --output-dir result/minecraft/issue_243_judged_20260726_retry_after_judger_op \
  --timeout-seconds 600
```

Two local runtime failures were fixed before that attempt: a zero-byte legacy
`API_KEY_LIST` now falls back to the configured Ollama key, and the meta judger
loads its arena chunks before sampling block heights. Environment launch errors
are also re-raised so the original failure reaches normalized artifacts.

A follow-up attempt on 2026-07-26 closed the score evidence gap. Additional
runtime fixes recovered structured actions returned in Ollama's `reasoning`
field, accepted underscore JSON key variants, matched bridge movement tolerance
to the judger's one-block coordinate requirement, captured Mineflayer spawn
before handler registration could race, stopped the controller at terminal
judger status, and isolated runtime scratch paths by benchmark attempt ID.

The successful bundle is
`result/minecraft/issue_243_judged_20260726_isolated_terminal_score/`. Its parent
and experiment attempts are both complete. The experiment records
`snapshot_source=real_runtime`, one successful `navigateTo` action,
`score_available=true`, `score=100`, `progress=100`, `error=null`, and
`timed_out=false`. The server was healthy with zero players after cleanup, and
no local bridge or judger process remained. This single canary demonstrates the
judged execution and scoring path only; it is not benchmark performance evidence.

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
On POSIX, the runtime child starts a dedicated process group. The harness terminates and joins that group on timeout, interruption, or unexpected parent-side failure before finalizing the run bundle, so bridge/judger descendants are not left running.

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

Issue #243 is no longer blocked on Ollama, Minecraft, bridge, world snapshot,
judger loading, agent action parsing, or score capture. The bounded canary
satisfies the real-execution evidence path. No comparative performance claim is
made from this single smoke run.

## Opt-In Real Smoke Targets

All real checks are pytest-skipped by default, including during `just test`. Set only the component variable you intend to run and configure a dedicated output root:

```bash
export VILLAGER_REAL_SMOKE_OUTPUT_DIR=/absolute/path/to/verification-output

VILLAGER_OLLAMA_REAL_SMOKE=1 just real-smoke-ollama
VILLAGER_MINECRAFT_PORT_SMOKE=1 just real-smoke-port
VILLAGER_MINECRAFT_BRIDGE_SMOKE=1 just real-smoke-bridge
VILLAGER_MINECRAFT_JUDGED_SMOKE=1 just real-smoke-judged
```

The targets report `SKIP` unless their exact opt-in variable is `1`. An enabled target fails clearly when a required setting is absent. `just real-smoke` runs the same four tests and still only activates explicitly enabled components.

Common settings:

- `MINECRAFT_HOST` and `MINECRAFT_PORT`: required by port and bridge checks.
- `VILLAGER_REAL_SMOKE_TIMEOUT_SECONDS`: positive per-check bound, default `30` seconds.
- `VILLAGER_REAL_SMOKE_OVERWRITE=1`: explicitly replace a prior managed attempt bundle. Otherwise non-empty output directories are rejected per #296.
- `OLLAMA_API_BASE` and `OLLAMA_MODEL`: Ollama endpoint and installed model. The preflight calls `/api/tags`, verifies the model, and records its digest when exposed.
- `MINECRAFT_SMOKE_AGENT_NAME`, `MINECRAFT_SMOKE_LOCAL_PORT`, and `MINECRAFT_SMOKE_WORLD`: optional bridge settings, defaulting to `Alice`, `5000`, and `world`.
- `MINECRAFT_JUDGED_CONFIG`: required path to one single-agent `task_type=meta` config object with a non-empty `task_scenario` and `evaluation_arg`, an existing reset/world identity path, an existing bridge identity path, and explicit server version/protocol identity. TCP reachability is checked before any mutating runtime starts.
- `MINECRAFT_JUDGED_TIMEOUT_SECONDS`: positive judged execute bound, default `600` seconds.

The bridge check launches `VillagerBench(env_type.none)` through the FastAPI bridge, verifies bridge ping, and performs the read-only `get_environment_info_dict` action. It does not invoke an LLM or claim task performance.

Each preflight/bridge output is a unique managed attempt containing `attempt.json`, `verification.json`, standardized `provenance.json`, `artifact_manifest.json`, and `_COMPLETED` on success. The judged target preserves the normal `minecraft_judged_meta` experiment bundle and adds a `minecraft_judged_smoke` parent attempt whose status reflects smoke success, including score availability. Existing bundles require explicit overwrite.

`command.txt` and provenance `argv` record a public smoke command template with the timeout, host/port, judged config, and explicit overwrite flag. Repository-local paths are relative and host-local paths use `<external>`; replace that placeholder before rerunning the command. Service credentials remain environment-provided and are redacted from persisted artifacts.

For `meta`, #234 diagnostics are written directly below the judged run's `.runtime/` directory and normalized `meta_judger_diagnostics.json` is emitted when available. The summary reports `load_status`, `meta_judger_diagnostics_available`, and `score_available`. A timeout or missing score makes the judged target fail while retaining the actionable bundle; this is blocker evidence, not a performance result.

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
- Whether the task judger identity has the required server operator permission.
- Whether `data/score.json` was produced.
- Observed `summary.json` `progress`, `error`, `error_type`, and `timed_out` fields.
- Any runtime failure mode.

## Approved production matrix

Use a newer control-plane checkout for the resolver and command, but execute from
a clean detached worktree at the exact revision named by the approved bundle:

```bash
python -m benchmarks.minecraft.approved_experiment resolve \
  --experiment minecraft-judged-production-v1 \
  --execution-worktree /external/clean-approved-worktree \
  --output /external/resolved-approved-experiment

VILLAGER_MINECRAFT_MODEL_API_BASE=http://10.255.255.5:11434 \
VILLAGER_MINECRAFT_MODEL_NAME=gemma4:12b \
VILLAGER_MINECRAFT_MODEL_PROVIDER=ollama \
VILLAGER_MINECRAFT_MODEL_DIGEST=4eb23ef187e2c5462566d6a1d3bbbc2f1346d0b4327cbb66d58fffbcc9b2b05c \
VILLAGER_MINECRAFT_MODEL_API_KEY_ENV=OLLAMA_API_KEY \
python -m benchmarks.minecraft.production \
  --approved-experiment minecraft-judged-production-v1 \
  --execution-worktree /external/clean-approved-worktree \
  --output /external/minecraft-production-output
```

Set `OLLAMA_API_KEY` in the environment before the production command; do not put
its value on the command line. The registry record is
`configs/minecraft/approved-experiments/minecraft-judged-production-v1.json`.
The resolver materializes the exact pinned premanifest and sanitized provenance;
the production command repeats admission under `OUTPUT/admission` before Run 1.
Both outputs must be new absolute paths outside all tracked worktrees. Endpoint,
model identity, and credentials are environment-provided only. Do not regenerate
an approval, inspect a mutable Gist HEAD, or select the latest experiment.
Development matrix premanifest mode is not production approval and must not be
used as a substitute for this command.

## Validation Status For This Change

The automated coverage validates the bounded execute artifact path without
requiring a server:

- A monkeypatched runtime error preserves `summary.json` and `metrics.json` and records `error_type == "RuntimeError"`.
- A monkeypatched slow runtime triggers `--execute-timeout-seconds`, preserves artifacts, and records `error_type == "timeout"` and `timed_out == true`.
- A monkeypatched runtime snapshot with a task different from the config fixture drives execute task artifacts, lifecycle status, dependency edges, and post-hoc ranking without mixing fixture state.

The final 2026-07-26 real run reached judged loading, executed one Minecraft
action, captured a real runtime task snapshot, and produced score 100. No
benchmark performance is claimed.
