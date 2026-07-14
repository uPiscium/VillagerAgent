# VillagerAgent

VillagerAgent は Minecraft 上で LLM 駆動の複数エージェントを動かす研究用フレームワークです。高レベルタスクをグラフ構造のサブタスクへ分解し、各エージェントへ割り当て、Minecraft ツール実行と結果の記録を行います。

English documentation: [README.md](README.md)

## 現在の前提

このリポジトリは、現状ではローカル Ollama または OpenAI 互換 Ollama endpoint を使う前提に寄せています。

- 既定 LLM endpoint: `http://localhost:11434/v1`
- 既定モデル: `gemma4:12b`
- Ollama 用 dummy API key: `ollama`
- Ollama 利用時は `API_KEY_LIST` は不要です。無い場合は `OLLAMA_API_KEY`、未設定なら `ollama` にフォールバックします。
- Minecraft bridge は `env.run(fast_api=True)` の FastAPI 経路を使います。
- ローカル検証済みの Minecraft server endpoint は `10.12.3.1:40000` です。

従来の OpenAI/Gemini/Zhipu 向け API key 経路も残っていますが、現在の既定はローカル Ollama です。

## 主要ファイル

- `start_with_config.py`: 設定ファイルから実行する主 entrypoint。
- `tiny_start.py`: 単一エージェントの最小起動例。
- `env/env.py`: `VillagerBench` 環境、agent 登録、bridge 起動、judger 起動、score/action log 取得。
- `env/minecraft_client.py`: Mineflayer/FastAPI bridge client と Minecraft tool 定義。
- `pipeline/controller_tiny.py`: 現在 `start_with_config.py` で使われる中心 controller。
- `pipeline/controller.py`: 旧/フル controller 経路。
- `pipeline/task_manager.py`: タスク分解とタスクグラフ管理。
- `pipeline/data_manager.py`: 環境、agent、履歴、経験の状態管理。
- `pipeline/agent.py`: agent ごとの prompt、action、reflection loop。
- `model/ollama_config.py`: Ollama 既定値と API key fallback。
- `model/init_model.py`: LLM provider の振り分け。
- `benchmarks/minecraft/experiment.py`: 単一 run の dry-run/execute benchmark harness。
- `benchmarks/minecraft/matrix.py`: CI-safe な dry-run matrix wrapper。
- `benchmarks/common/report.py`: 共通 benchmark report 生成器。

## 詳細ガイド

- [Minimal startup](docs/minimal_run.md): Ollama 既定値、Minecraft smoke、bounded execute。
- [Task graph structure](docs/graph_structure.md): `Task`, `Graph`, status、依存関係、artifact。
- [Dual-DAG runtime boundary](docs/dual_dag_runtime.md): Task Graph、Epistemic DAG、Action Candidate DAG、artifact の責務境界。
- [Configuration](docs/configuration.md): Minecraft JSON field、LLM 既定値、API key fallback、benchmark CLI option。
- [Execution flow](docs/execution_flow.md): config 読み込みから artifact 生成までの全体処理。
- [Artifact schema](docs/artifact_schema.md): Minecraft benchmark artifact の生成元、field、公開境界。
- [Termination semantics](docs/termination_semantics.md): success、failure、blocked、timeout、runtime error、partial、cancelled の意味。

## アーキテクチャ概要

1. `VillagerBench` が Minecraft bridge を起動し、agent tool を公開する。
2. `DataManager` が環境状態、agent 状態、履歴、経験を保持・要約する。
3. `TaskManager` がトップレベルタスクを初期化し、分解し、タスクグラフを管理する。
4. `GlobalController` が実行可能なタスクを利用可能な agent に割り当てる。
5. `BaseAgent` が `DataManager` から状態を取得し、LLM を呼び、Minecraft tool を実行し、成功/失敗を reflection する。
6. Benchmark harness が `summary.json`, `metrics.json`, `action_log.json`, `task_graph_snapshot.json`, `dual_dag_artifact.json`, `decision_support.json` を保存する。

コードを読むなら、`start_with_config.py`、`pipeline/controller_tiny.py`、`task_manager.py`、`data_manager.py`、`agent.py` の順が分かりやすいです。全体処理は [docs/execution_flow.md](docs/execution_flow.md) にまとめています。

## セットアップ

Python version は `pyproject.toml` で固定されています。

```bash
python --version
```

期待値:

```text
3.10.19
```

推奨するローカルセットアップ手順:

```bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
python js_setup.py
```

Ollama の OpenAI 互換 endpoint を起動し、モデルを用意します。

```bash
ollama serve
ollama pull gemma4:12b
```

任意の環境変数:

```bash
export OLLAMA_API_BASE=http://localhost:11434/v1
export OLLAMA_MODEL=gemma4:12b
export OLLAMA_API_KEY=ollama
```

有料/リモート provider を使う場合は repository root に `API_KEY_LIST` を置いてください。ローカル Ollama だけなら不要です。

## Minecraft Server

現在検証済みの endpoint:

```text
10.12.3.1:40000
```

agent がサーバーへ参加し、必要な command/tool を使える必要があります。必要に応じて Minecraft server console で権限を付与します。

```text
/op Alice
/op Bob
```

`env_type.none` の非 judged 接続経路では、`Alice` と `performMovement(jump)` による bridge/action response まで確認済みです。judged task では、さらに対象 judger が load 完了し、`data/score.json` を生成する必要があります。

## 接続スモーク

judged run の前に、非破壊の `env_type.none` smoke を実行してください。最小起動手順は [docs/minimal_run.md](docs/minimal_run.md) にあります。

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

## Bounded Benchmark Execute

artifact を残す real-run には benchmark harness を使います。

```bash
python -m benchmarks.minecraft.experiment \
  --config path/to/minecraft_config.json \
  --output-root result/minecraft_real \
  --run-name bounded_real_run \
  --execute \
  --execute-timeout-seconds 600
```

`--execute` を省略すると dry-run になります。dry-run は Minecraft server、LLM、judger、credential を必要としません。

設定 field の詳細は [docs/configuration.md](docs/configuration.md) にあります。

## Dry Matrix

```bash
python -m benchmarks.minecraft.matrix \
  --config path/to/minecraft_config_list.json \
  --output-dir result/minecraft_matrix \
  --run-names run_a,run_b \
  --dual-dag-task-selection
```

共通 report の生成:

```bash
python -m benchmarks.common.report result/minecraft_matrix \
  --output result/minecraft_matrix/common_report.csv \
  --json-output result/minecraft_matrix/common_report.json
```

## 検証メモ

関連ドキュメント:

- `docs/benchmarks/minecraft_real_run.md`: bounded real-run 手順と runtime asset 前提。
- `doc/minecraft_e2e_verification.md`: 以前の bridge-level E2E 検証。
- `/tmp/opencode/minecraft-verification-20260712.md`: 直近のローカル検証ログ。

現在分かっている状態:

- ローカル Ollama は `127.0.0.1:11434` で到達可能。
- Minecraft server は `10.12.3.1:40000` で到達可能。
- Ollama 実行では `API_KEY_LIST` は不要。
- judged `meta` run の次の blocker は OpenAI 課金 credential ではなく、judger/server-side task loading 側です。

## テスト

Compile check:

```bash
just validate
```

Full test suite:

```bash
just test
```

Minecraft/Ollama 関連の targeted test:

```bash
pytest tests/test_ollama_config.py tests/test_minecraft_experiment.py tests/test_minecraft_matrix.py tests/test_minecraft_adapter.py
```

## 引用

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

このプロジェクトは MIT License で提供されています。
