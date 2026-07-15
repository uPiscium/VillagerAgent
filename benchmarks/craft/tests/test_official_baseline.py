import json
import subprocess
import sys

import pytest
import requests

from benchmarks.common.report import summarize_inputs
from benchmarks.craft.config import InvalidConfigError, load_config
from benchmarks.craft.craft_env_adapter import (
    CraftEnvAdapter,
    _inspect_official_runner_leakage,
    _official_runner_environment,
    _require_official_runner_semantic_output,
)
from benchmarks.craft.leakage_guard import PartialInformationLeakageError
from benchmarks.craft.run import _provenance_assets, run_config
from benchmarks.craft.result_converter import normalize_results
from benchmarks.craft.tests.fixtures import write_minimal_structures_dataset


def load_config_with_minimal_dataset(tmp_path, path, *, overrides=None):
    dataset_path = write_minimal_structures_dataset(tmp_path / "structures_dataset_20.json")
    merged_overrides = {
        **(overrides or {}),
        "craft": {
            "dataset_path": str(dataset_path),
            "official_runner_interpreter": sys.executable,
            **((overrides or {}).get("craft") or {}),
        },
    }
    return load_config(path, overrides=merged_overrides)


def test_official_baseline_generates_comparable_turn_artifacts(tmp_path):
    config = load_config_with_minimal_dataset(
        tmp_path,
        "configs/craft/official_baseline.yaml",
        overrides={"structures": [0]},
    )
    adapter = CraftEnvAdapter(config, tmp_path)
    raw_result = adapter.run("official_baseline")

    assert raw_result["condition"] == "official_baseline"
    assert raw_result["structure_id"] == config["run"]["structures"][0]
    assert len(raw_result["turns"]) == config["run"]["turns"]
    assert raw_result["official_craft_runner"]["seed"] == config["run"]["seed"]
    assert raw_result["official_craft_runner"]["turns"] == config["run"]["turns"]
    assert (tmp_path / "raw" / "official_baseline.json").exists()

    normalize_results(
        config=config,
        condition="official_baseline",
        raw_result=raw_result,
        output_dir=tmp_path,
    )
    turns = (tmp_path / "normalized" / "turns.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(turns) == config["run"]["turns"]
    assert json.loads(turns[0])["leakage_check"]["passed"] is True


def test_official_baseline_runs_all_requested_structures(tmp_path):
    config = load_config_with_minimal_dataset(
        tmp_path,
        "configs/craft/official_baseline.yaml",
        overrides={"structures": [0, 1], "turns": 2},
    )
    adapter = CraftEnvAdapter(config, tmp_path)
    raw_result = adapter.run("official_baseline")

    assert raw_result["structure_ids"] == [0, 1]
    assert len(raw_result["games"]) == 2
    assert len(raw_result["turns"]) == 4
    assert raw_result["official_craft_runner"]["structure_indices"] == [0, 1]


def test_official_baseline_external_cli_normalizes_runner_output(tmp_path, monkeypatch):
    secret = "sentinel-secret-value-12345"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example/v1")
    config = load_config_with_minimal_dataset(
        tmp_path,
        "configs/craft/official_baseline_full.yaml",
        overrides={"structures": [0], "turns": 2, "seed": 7},
    )

    def fake_run(command, cwd, env, timeout, check, capture_output, text):
        output_dir = command[command.index("--output") + 1]
        result_dir = tmp_path / "raw" / "official_craft_runner" / "gpt-4o-mini_gpt-4o-mini"
        assert output_dir == str(tmp_path / "raw" / "official_craft_runner")
        assert cwd == config["craft"]["repo_path"]
        assert command[0] == config["craft"]["official_runner_interpreter"]
        assert env["OPENAI_API_KEY"] == secret
        assert env["OPENAI_BASE_URL"] == "https://gateway.example/v1"
        assert "--oracle" in command
        assert "--no_tools" in command
        result_dir.mkdir(parents=True)
        (result_dir / "craft_structure_001_7.json").write_text(
            json.dumps({
                "experiment_info": {
                    "structure_index": 0,
                    "max_turns": 2,
                    "run": 7,
                    "models": {"director": "gpt-4o-mini", "builder": "gpt-4o-mini"},
                },
                "games": [{
                    "structure_id": "structure_001",
                    "target_structure": {"hidden": True},
                    "completed": True,
                    "final_progress": 1.0,
                    "turns": [{
                        "turn_number": 1,
                        "director_prompt_D1": "private prompt",
                        "builder_prompt": "private builder prompt",
                        "director_responses": {
                            "D1": {
                                "public_message": f"place red {secret}",
                                "internal_thinking": "hidden",
                                "raw_response": "private raw response",
                            },
                        },
                        "target_director_views": {"D1": {"hidden": True}},
                        "oracle_moves": [{"hidden": True}],
                        "move_attempted": {"action": "place", "color": "red"},
                        "move_executed": True,
                        "progress_data": {"overall_progress": 0.5},
                    }],
                }],
            }),
            encoding="utf-8",
        )
        (result_dir / "craft_structure_001_7.md").write_text(
            f"private markdown {secret}", encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, stdout=f"ok {secret}", stderr=f"warning {secret}")

    monkeypatch.setattr("benchmarks.craft.craft_env_adapter.subprocess.run", fake_run)

    adapter = CraftEnvAdapter(config, tmp_path)
    raw_result = adapter.run("official_baseline")

    assert raw_result["official_craft_runner"]["mode"] == "external_cli"
    assert raw_result["final_progress"] == 1.0
    assert raw_result["turns"][0]["director_messages"] == {"D1": "place red [REDACTED]"}
    assert raw_result["turns"][0]["progress"] == {"overall_progress": 0.5}
    assert secret not in json.dumps(raw_result)
    assert config["craft"]["official_runner_environment_forward"] == [
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    ]
    assert "api_key" not in config["models"]["director"]
    sanitized_runner_output = json.loads(
        (tmp_path / "raw" / "official_craft_runner" / "gpt-4o-mini_gpt-4o-mini" / "craft_structure_001_7.json").read_text(
            encoding="utf-8"
        )
    )
    serialized = json.dumps(sanitized_runner_output)
    assert "target_structure" not in serialized
    assert "oracle_moves" not in serialized
    assert "internal_thinking" not in serialized
    assert "director_prompt_D1" not in serialized
    assert "target_director_views" not in serialized
    assert "raw_response" not in serialized
    assert "builder_prompt" not in serialized
    assert secret not in serialized
    assert not (tmp_path / "raw" / "official_craft_runner" / "gpt-4o-mini_gpt-4o-mini" / "craft_structure_001_7.md").exists()
    assert "stdout" not in raw_result["official_craft_runner"]
    assert "stderr" not in raw_result["official_craft_runner"]

    normalize_results(
        config=config,
        condition="official_baseline",
        raw_result=raw_result,
        output_dir=tmp_path,
    )
    summary = json.loads((tmp_path / "normalized" / "summary.json").read_text(encoding="utf-8"))
    assert summary["runtime"]["baseline_type"] == "full_official_runner"


def test_forwarded_openai_environment_rejects_unsafe_parent_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "parent-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example/v1?api_key=secret")
    craft = {
        "official_runner_environment": {},
        "official_runner_environment_forward": ["OPENAI_API_KEY", "OPENAI_BASE_URL"],
    }

    with pytest.raises(InvalidConfigError, match="must not contain credentials"):
        _official_runner_environment(craft, seed=3)


def test_official_runner_forwards_only_safe_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-be-forwarded")
    config = load_config_with_minimal_dataset(
        tmp_path,
        "configs/craft/official_baseline_gemma4_12b_ollama.yaml",
        overrides={"structures": [0], "turns": 1},
    )

    proxy_url = None
    proxy_token = None

    def fake_run(command, *, cwd, env, **kwargs):
        nonlocal proxy_token, proxy_url
        assert env["OPENAI_API_KEY"] != "ollama"
        assert len(env["OPENAI_API_KEY"]) >= 32
        proxy_token = env["OPENAI_API_KEY"]
        assert env["OPENAI_BASE_URL"].startswith("http://127.0.0.1:")
        assert env["PYTHONHASHSEED"] == "3"
        proxy_url = env["OPENAI_BASE_URL"]
        assert "UNRELATED_SECRET" not in env
        result_dir = tmp_path / "raw" / "official_craft_runner" / "gemma4_12b_gemma4_12b"
        result_dir.mkdir(parents=True)
        (result_dir / "craft_structure_001_3.json").write_text(
            json.dumps({
                "experiment_info": {"structure_index": 0},
                "games": [{
                    "structure_id": "structure_001",
                    "completed": False,
                    "final_progress": 0.0,
                    "turns": [{
                        "turn_number": 1,
                        "director_responses": {
                            "D1": {"public_message": "Place the red block."},
                        },
                        "move_attempted": {"action": "place", "color": "red"},
                        "move_executed": True,
                    }],
                }],
            }),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr("benchmarks.craft.craft_env_adapter.subprocess.run", fake_run)
    result = CraftEnvAdapter(config, tmp_path).run("official_baseline")

    persisted = json.dumps(result)
    assert "ollama.arc.upiscium.dev" in persisted
    assert '"OPENAI_API_KEY": "[REDACTED]"' in persisted
    assert proxy_token not in persisted
    assert result["official_craft_runner"]["compatibility_proxy"]["think"] is False
    assert result["official_craft_runner"]["compatibility_proxy"]["effective_generation_settings"] == {
        "director": {"temperature": 0.2, "max_tokens": 4096, "seed": 3},
        "builder": {"temperature": 0.0, "max_tokens": 4096, "seed": 3},
    }
    with pytest.raises(requests.ConnectionError):
        requests.get(f"{proxy_url}/models", timeout=0.2)


def test_official_runner_semantic_gate_rejects_placeholder_messages(tmp_path, monkeypatch):
    config = load_config_with_minimal_dataset(
        tmp_path,
        "configs/craft/official_baseline_gemma4_12b_ollama.yaml",
        overrides={"structures": [0], "turns": 1},
    )

    def fake_run(command, **kwargs):
        result_dir = tmp_path / "raw" / "official_craft_runner" / "gemma4_12b_gemma4_12b"
        result_dir.mkdir(parents=True)
        (result_dir / "craft_structure_001_3.json").write_text(
            json.dumps({
                "experiment_info": {"structure_index": 0},
                "games": [{
                    "structure_id": "structure_001",
                    "completed": False,
                    "turns": [{
                        "turn_number": 1,
                        "director_responses": {
                            "D1": {"public_message": "No message provided"},
                        },
                    }],
                }],
            }),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("benchmarks.craft.craft_env_adapter.subprocess.run", fake_run)
    with pytest.raises(RuntimeError, match="fallback/error"):
        CraftEnvAdapter(config, tmp_path).run("official_baseline")


@pytest.mark.parametrize("message", [
    "No message provided",
    "Error generating response: endpoint failed",
    "Could not generate a response",
    "Could not parse response: empty",
    "Empty API response",
    "No response from API",
    "API request failed after retries",
])
def test_official_runner_semantic_gate_rejects_known_fallback_phrases(message):
    games = [{
        "structure_id": 0,
        "turns": [{
            "director_messages": {"D1": message},
            "builder_action": {"action": "place"},
            "move_executed": True,
        }],
    }]

    with pytest.raises(RuntimeError, match="fallback/error"):
        _require_official_runner_semantic_output(games)


def test_official_runner_semantic_gate_rejects_fallback_among_valid_messages():
    games = [{
        "structure_id": 0,
        "turns": [{
            "director_messages": {
                "D1": "Place a small red block.",
                "D2": "No message provided",
            },
            "builder_action": {"action": "place"},
            "move_executed": True,
        }],
    }]

    with pytest.raises(RuntimeError, match="fallback/error"):
        _require_official_runner_semantic_output(games)


def test_official_runner_semantic_gate_requires_executed_builder_action():
    games = [{
        "structure_id": 0,
        "turns": [{
            "director_messages": {"D1": "Place a small red block."},
            "builder_action": {"action": "place"},
            "move_executed": False,
        }],
    }]

    with pytest.raises(RuntimeError, match="no parsed, executed builder action"):
        _require_official_runner_semantic_output(games)


def test_official_runner_leakage_inspection_rejects_sentinel_public_exposure(tmp_path, monkeypatch):
    sentinel = "SENTINEL-HIDDEN-TARGET-291"
    config = load_config_with_minimal_dataset(
        tmp_path,
        "configs/craft/official_baseline_gemma4_12b_ollama.yaml",
        overrides={"structures": [0], "turns": 1},
    )

    def fake_run(command, **kwargs):
        result_dir = tmp_path / "raw" / "official_craft_runner" / "gemma4_12b_gemma4_12b"
        result_dir.mkdir(parents=True)
        (result_dir / "craft_structure_001_3.json").write_text(json.dumps({
            "experiment_info": {"structure_index": 0},
            "games": [{
                "structure_id": "structure_001",
                "target_structure": {"secret": sentinel},
                "turns": [{
                    "turn_number": 1,
                    "director_prompt_D1": "Use only your own view.",
                    "director_responses": {"D1": {"public_message": sentinel}},
                    "move_attempted": {"action": "place"},
                    "move_executed": True,
                }],
            }],
        }), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("benchmarks.craft.craft_env_adapter.subprocess.run", fake_run)
    with pytest.raises(PartialInformationLeakageError, match="target_structure"):
        CraftEnvAdapter(config, tmp_path).run("official_baseline")


def test_public_director_output_rejects_other_director_private_view(tmp_path):
    sentinel = "SENTINEL-D2-PRIVATE-VIEW-291"
    game = {
        "target_director_views": {
            "D1": {"marker": "SENTINEL-D1-OWN-VIEW-291"},
            "D2": {"marker": sentinel},
        },
        "turns": [{
            "director_prompt_D1": "SENTINEL-D1-OWN-VIEW-291",
            "director_responses": {"D1": {"public_message": sentinel}},
            "move_attempted": {"action": "place"},
        }],
    }

    with pytest.raises(PartialInformationLeakageError, match="other_private_view:D2"):
        _inspect_official_runner_leakage(game, artifact_path=tmp_path / "runner.json")


def test_public_builder_output_rejects_every_director_private_view(tmp_path):
    sentinel = "SENTINEL-D1-PRIVATE-VIEW-291"
    game = {
        "target_director_views": {
            "D1": {"marker": sentinel},
            "D2": {"marker": "SENTINEL-D2-PRIVATE-VIEW-291"},
        },
        "turns": [{
            "director_prompt_D1": "Use only your view.",
            "director_responses": {"D1": {"public_message": "Place a block."}},
            "move_attempted": {"action": "place", "confirmation": sentinel},
        }],
    }

    with pytest.raises(PartialInformationLeakageError, match="private_view:D1"):
        _inspect_official_runner_leakage(game, artifact_path=tmp_path / "runner.json")


def test_external_ollama_runner_provenance_includes_proxy_and_environment(tmp_path):
    config = load_config_with_minimal_dataset(
        tmp_path,
        "configs/craft/official_baseline_gemma4_12b_ollama.yaml",
        overrides={"structures": [0], "turns": 1},
    )

    assets = {item["name"]: item for item in _provenance_assets(
        config,
        condition="official_baseline",
    )}

    assert assets["official_runner_interpreter"]["available"] is True
    assert assets["official_runner_python_environment"]["sha256"]
    assert assets["official_runner_dependencies"]["sha256"]
    assert assets["official_runner_config"]["sha256"]
    assert assets["official_runner_compatibility_proxy"]["available"] is True
    assert assets["official_runner_compatibility_proxy"]["sha256"]


def test_official_baseline_failure_redacts_partial_runner_output(tmp_path, monkeypatch):
    secret = "runtime-secret"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    config = load_config_with_minimal_dataset(
        tmp_path,
        "configs/craft/official_baseline_full.yaml",
        overrides={"structures": [0], "turns": 2, "seed": 7},
    )

    def fail_run(command, **kwargs):
        output_dir = command[command.index("--output") + 1]
        partial = tmp_path / "raw" / "official_craft_runner" / "partial" / "craft_structure_001_7.json"
        assert output_dir == str(tmp_path / "raw" / "official_craft_runner")
        partial.parent.mkdir(parents=True)
        partial.write_text(f'{{"error": "rejected {secret}"', encoding="utf-8")
        (partial.parent / "runner.log").write_text(f"provider rejected {secret}", encoding="utf-8")
        (partial.parent / "metadata.json").write_text(
            json.dumps({"api_key": secret, "message": f"rejected {secret}"}),
            encoding="utf-8",
        )
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("benchmarks.craft.craft_env_adapter.subprocess.run", fail_run)

    with pytest.raises(subprocess.CalledProcessError):
        CraftEnvAdapter(config, tmp_path).run("official_baseline")

    assert not (
        tmp_path / "raw" / "official_craft_runner" / "partial" / "craft_structure_001_7.json"
    ).exists()
    for artifact in (tmp_path / "raw" / "official_craft_runner").rglob("*"):
        if artifact.is_file():
            assert secret not in artifact.read_text(encoding="utf-8")


def test_direct_craft_failure_writes_summarizable_failed_bundle(tmp_path, monkeypatch):
    config = load_config_with_minimal_dataset(
        tmp_path,
        "configs/craft/official_baseline_full.yaml",
        overrides={"structures": [0], "turns": 2, "seed": 7},
    )
    config["run"]["output_dir"] = str(tmp_path / "results")
    config["run"]["name"] = "failed_direct_run"
    monkeypatch.setattr("benchmarks.craft.run.load_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(
        "benchmarks.craft.run.CraftEnvAdapter.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("runner failed")),
    )

    with pytest.raises(RuntimeError, match="runner failed"):
        run_config("unused.yaml")

    run_dir = tmp_path / "results" / "failed_direct_run"
    row = summarize_inputs([run_dir])[0]
    assert row["status"] == "failed"
    assert row["failed_runs"] == 1
    assert row["success_rate"] is None
