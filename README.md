# VillagerAgent

VillagerAgent is a Minecraft multi-agent research framework. It runs LLM-driven agents in a Minecraft server, decomposes high-level tasks into graph-structured subtasks, assigns those subtasks to agents, and records normalized benchmark artifacts for analysis.

Japanese documentation: [README.ja.md](README.ja.md)

## Current Status

This repository is currently optimized for local or OpenAI-compatible Ollama execution.

- Default LLM endpoint: `http://localhost:11434/v1`
- Default model: `gemma4:12b`
- Dummy API key for Ollama: `ollama`
- `API_KEY_LIST` is optional for Ollama. If the file is missing, the runtime falls back to `OLLAMA_API_KEY` or `ollama`.
- Minecraft bridge defaults to the FastAPI path used by `env.run(fast_api=True)`.
- The verified Minecraft server endpoint in the local environment is `10.12.3.1:40000`.

The legacy OpenAI/Gemini/Zhipu style API-key paths still exist, but the current default path is local Ollama.

## Repository Map

- `start_with_config.py`: main configured runtime entrypoint for VillagerBench runs.
- `tiny_start.py`: minimal example for manually launching one agent.
- `env/env.py`: `VillagerBench` environment wrapper, agent registration, bridge launch, judger launch, score/action-log access.
- `env/minecraft_client.py`: Mineflayer/FastAPI bridge client and Minecraft tool definitions.
- `pipeline/controller_tiny.py`: main controller currently used by `start_with_config.py`.
- `pipeline/controller.py`: older/full controller path with extra decomposition logic.
- `pipeline/task_manager.py`: task decomposition and task graph management.
- `pipeline/data_manager.py`: environment, agent, history, and experience state management.
- `pipeline/agent.py`: per-agent prompt, action, and reflection loop.
- `model/ollama_config.py`: Ollama defaults and API-key fallback logic.
- `model/init_model.py`: LLM provider routing.
- `benchmarks/minecraft/experiment.py`: single-run dry-run/execute benchmark harness.
- `benchmarks/minecraft/matrix.py`: CI-safe dry-run matrix wrapper.
- `benchmarks/common/report.py`: shared benchmark report generator.

## Detailed Guides

- [Minimal startup](docs/minimal_run.md): Ollama defaults, Minecraft smoke, and bounded execute.
- [Architecture diagrams](docs/architecture.md): current runtime task DAG source-of-truth architecture, Dual-DAG boundary, paper figure layout, and before/after diagrams.
- [Task graph structure](docs/graph_structure.md): `Task`, `Graph`, statuses, dependencies, and artifacts.
- [Dual-DAG runtime boundary](docs/dual_dag_runtime.md): Task Graph, Epistemic DAG, Action Candidate DAG, and artifact responsibilities.
- [Configuration](docs/configuration.md): Minecraft JSON fields, LLM defaults, API key fallback, and benchmark CLI options.
- [Execution flow](docs/execution_flow.md): end-to-end runtime flow from config load to artifacts.
- [Artifact schema](docs/artifact_schema.md): Minecraft benchmark artifact producers, fields, and public/private boundary.
- [Termination semantics](docs/termination_semantics.md): success, failure, blocked, timeout, runtime error, partial, and cancelled states.

## Architecture In One Pass

1. `VillagerBench` starts the Minecraft bridge and exposes agent tools.
2. `DataManager` stores and summarizes environment/agent/history information.
3. `TaskManager` initializes the top-level task, decomposes it, and writes canonical task dependency/lifecycle state to `RuntimeTaskDAGStore`.
4. `GlobalController` asks `TaskManager.query_runnable_subtasks()` for store-filtered runnable tasks, applies the configured selection policy, assigns exactly each task's required number of free candidate agents, and writes lifecycle updates back to the runtime task DAG. Independent tasks may be assigned to remaining free agents in the same scheduler iteration.
5. `GlobalController` executes a multi-agent task as one execution group: every assigned `BaseAgent` receives the task, and the task succeeds only when every future completes and every agent reflection succeeds.
6. `BaseAgent` queries state through `DataManager`, calls the LLM, executes Minecraft tools, and returns per-agent detail for group reflection.
7. Benchmark harnesses write normalized artifacts: `summary.json`, `metrics.json`, `action_log.json`, `task_graph_snapshot.json`, `dual_dag_artifact.json`, and `decision_support.json`. Dry-run task artifacts use config fixtures; execute task artifacts use the recovered real runtime task DAG snapshot when available.

For code reading, start with `start_with_config.py`, then `pipeline/controller_tiny.py`, then `task_manager.py`, `data_manager.py`, and `agent.py`. See [Execution flow](docs/execution_flow.md) for the full processing path.

## Setup

Python is pinned in `pyproject.toml`:

```bash
python --version
```

Expected version:

```text
3.10.19
```

Install dependencies with the primary local path:

```bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
python js_setup.py
```

Start or expose an Ollama OpenAI-compatible endpoint:

```bash
ollama serve
ollama pull gemma4:12b
```

Optional environment overrides:

```bash
export OLLAMA_API_BASE=http://localhost:11434/v1
export OLLAMA_MODEL=gemma4:12b
export OLLAMA_API_KEY=ollama
```

If you use paid or remote providers, create `API_KEY_LIST` in the repository root. For local Ollama, this file is not required.

## Minecraft Server

The current verified endpoint is:

```text
10.12.3.1:40000
```

Agents must be allowed to join and use required commands/tools. In a Minecraft server console, grant operator permissions to the agent names you use, for example:

```text
/op Alice
/op Bob
```

The non-judged connectivity path has been verified with `env_type.none`, `Alice`, and `performMovement(jump)`. A judged task also requires the relevant judger script to finish loading and produce `data/score.json`.

## Run A Connectivity Smoke

Use a non-destructive `env_type.none` smoke before judged runs. The full minimal startup guide is in [docs/minimal_run.md](docs/minimal_run.md).

```bash
python - <<'PY'
from env.env import VillagerBench, env_type, Agent

env = VillagerBench(
    env_type.none,
    task_id=0,
    dig_needed=False,
    host='10.12.3.1',
    port=40000,
    task_name='smoke_env_none',
    _virtual_debug=False,
)
env.agent_register(agent_tool=[Agent.performMovement], agent_number=1, name_list=['Alice'])
with env.run(fast_api=True):
    print('ping', env.agents_ping())
    print('before', env.get_init_state())
    print('action', Agent.performMovement.func(player_name='Alice', action_name='jump', seconds=1, emotion=[], murmur=''))
    print('after', env.get_init_state())
PY
```

## Run A Bounded Benchmark Execute

Use the benchmark harness for artifact-preserving runs:

```bash
python -m benchmarks.minecraft.experiment \
  --config path/to/minecraft_config.json \
  --output-root result/minecraft_real \
  --run-name bounded_real_run \
  --execute \
  --execute-timeout-seconds 600
```

Dry-run is the default if `--execute` is omitted. Dry-run does not require a Minecraft server, LLM, judger, or credentials.

Execute checkpoints are isolated under each run directory and cleaned after normalized artifacts are written. Use `--retain-runtime-result` only to keep the internal `.runtime/runtime_result.json` checkpoint for debugging.

Config field details are documented in [docs/configuration.md](docs/configuration.md).

## Run A Dry Matrix

```bash
python -m benchmarks.minecraft.matrix \
  --config path/to/minecraft_config_list.json \
  --output-dir result/minecraft_matrix \
  --run-names run_a,run_b
```

Runtime task DAG lifecycle is always enabled. Task selection policy is configurable with `--task-selection-policy dual-dag` or `--task-selection-policy original`.

Generate a shared report:

```bash
python -m benchmarks.common.report result/minecraft_matrix \
  --output result/minecraft_matrix/common_report.csv \
  --json-output result/minecraft_matrix/common_report.json
```

## Verification Notes

Useful documents:

- `docs/benchmarks/minecraft_real_run.md`: bounded real-run procedure and runtime asset assumptions.
- `docs/benchmarks/minecraft_real_run.md`: current bridge and bounded execute verification guidance.
- `/tmp/opencode/minecraft-verification-20260712.md`: local verification log from the recent development session.

Current known state:

- Local Ollama is reachable at `127.0.0.1:11434`.
- Minecraft server is reachable at `10.12.3.1:40000`.
- `API_KEY_LIST` is no longer required for Ollama runs.
- The next blocker for judged `meta` runs is judger/server-side task loading, not OpenAI billing credentials.

## Tests

Compile check:

```bash
just validate
```

Full test suite:

```bash
just test
```

This includes common benchmark tests, CRAFT tests, C-WAH unit/mock tests, and repository-level tests. Real environment smoke tests remain opt-in.

Targeted Minecraft/Ollama tests:

```bash
pytest tests/test_ollama_config.py tests/test_minecraft_experiment.py tests/test_minecraft_matrix.py tests/test_minecraft_adapter.py
```

## Citation

```bibtex
@inproceedings{dong2024villageragent,
  title={VillagerAgent: A Graph-Based Multi-Agent Framework for Coordinating Complex Task Dependencies in Minecraft},
  author={Dong, Yubo and Zhu, Xukun and Pan, Zhengzhe and Zhu, Linchao and Yang, Yi},
  booktitle={Proceedings of the 62nd Annual Meeting of the Association for Computational Linguistics (ACL)},
  year={2024},
  url={https://arxiv.org/abs/2406.05720}
}
```

## License

This project is available under the MIT License.
