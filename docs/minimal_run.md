# Minimal Startup

This path checks the current Ollama-first Minecraft runtime with the fewest moving parts.

## Requirements

- Python `3.10.19` or the version pinned by `pyproject.toml`.
- JavaScript bridge dependencies installed with `python js_setup.py` when the bridge is needed.
- Ollama serving an OpenAI-compatible endpoint.
- A reachable Minecraft server for real environment runs.

## Ollama Defaults

The runtime default is local Ollama:

```bash
ollama serve
ollama pull gemma4:12b
```

Optional overrides:

```bash
export OLLAMA_API_BASE=http://localhost:11434/v1
export OLLAMA_MODEL=gemma4:12b
export OLLAMA_API_KEY=ollama
```

`API_KEY_LIST` is optional for Ollama. If it is absent, `model/ollama_config.py` supplies `[OLLAMA_API_KEY]`, which defaults to `['ollama']`.

## Minecraft Connectivity Smoke

Use `env_type.none` before judged tasks. It starts the bridge and performs one simple movement without loading a task judger.

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

## Bounded Real Benchmark

Use the benchmark harness when artifacts should survive failures or timeouts:

```bash
python -m benchmarks.minecraft.experiment \
  --config configs/minecraft/experiments/issue110_smoke.json \
  --output-root result/minecraft_real \
  --run-name bounded_real_run \
  --execute \
  --execute-timeout-seconds 600
```

Omit `--execute` for dry-run mode. Dry-run does not need Minecraft, Ollama, a judger, or credentials.

## Known Local Endpoints

- Ollama: `127.0.0.1:11434`
- Minecraft: `10.12.3.1:40000`

These endpoints are local-environment examples, not global project requirements.
