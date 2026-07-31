import ast
import json
import random
from pathlib import Path

import pytest

from benchmarks.minecraft.experiment import _execute_real_runtime, run_minecraft_experiment
from benchmarks.minecraft.seed_contract import (
    KNOWN_SEED_SCOPES,
    SeedContract,
    SeedScope,
    resolve_seed_contract,
)


SUPPORTED = (SeedScope.PYTHON_RANDOM, SeedScope.META_JUDGER)


def _contract(seed=7, scopes=None):
    return {
        "seed": seed,
        "requested_scopes": scopes or ["python_random", "meta_judger"],
    }


def _config(seed_contract):
    return {
        "task_type": "meta",
        "task_idx": 0,
        "agent_num": 1,
        "task_goal": "Move",
        "host": "127.0.0.1",
        "port": 25565,
        "task_name": "seeded",
        "seed_contract": seed_contract,
    }


def test_contract_is_frozen_validated_and_has_explicit_known_scopes():
    contract = SeedContract.from_value(_contract())

    assert contract.requested_scopes == frozenset(SUPPORTED)
    assert [scope.value for scope in KNOWN_SEED_SCOPES] == [
        "python_random",
        "meta_judger",
        "task_generation",
        "model_sampling",
        "world_generation",
        "agent_ordering",
    ]
    with pytest.raises(AttributeError):
        contract.seed = 8
    with pytest.raises(ValueError, match="unknown seed scope"):
        SeedContract.from_value(_contract(scopes=["not_a_scope"]))
    with pytest.raises(ValueError, match="must be an integer"):
        SeedContract.from_value(_contract(seed=True))


def test_local_random_sequences_are_reproducible_without_global_mutation():
    before = random.getstate()
    first = SeedContract.from_value(_contract(seed=11)).random()
    second = SeedContract.from_value(_contract(seed=11)).random()
    different = SeedContract.from_value(_contract(seed=12)).random()

    first_sequence = [first.random() for _ in range(5)]
    assert first_sequence == [second.random() for _ in range(5)]
    assert first_sequence != [different.random() for _ in range(5)]
    assert random.getstate() == before


def test_requested_unsupported_and_requested_unapplied_scopes_fail():
    with pytest.raises(ValueError, match="unsupported: model_sampling"):
        resolve_seed_contract(
            _contract(scopes=["model_sampling"]),
            supported_scopes=SUPPORTED,
            applied_scopes=SUPPORTED,
        )
    with pytest.raises(ValueError, match="not applied: meta_judger"):
        resolve_seed_contract(
            _contract(),
            supported_scopes=SUPPORTED,
            applied_scopes=(SeedScope.PYTHON_RANDOM,),
        )


def test_unrequested_unsupported_scopes_are_recorded_with_reasons():
    resolution = resolve_seed_contract(
        _contract(), supported_scopes=SUPPORTED, applied_scopes=SUPPORTED
    ).to_dict()

    assert resolution["scopes"]["meta_judger"]["applied"] is True
    for scope in (
        "task_generation",
        "model_sampling",
        "world_generation",
        "agent_ordering",
    ):
        assert resolution["scopes"][scope] == {
            "requested": False,
            "supported": False,
            "applied": False,
            "reason": "not implemented by the Minecraft runtime",
        }


def test_seed_resolution_is_visible_in_run_artifacts(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config(_contract(seed=23))), encoding="utf-8")

    summary = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="seed-artifact",
    )

    output_dir = tmp_path / "result" / "seed-artifact"
    artifact = json.loads((output_dir / "seed_contract.json").read_text())
    provenance = json.loads((output_dir / "provenance.json").read_text())
    artifact.pop("attempt_id")
    assert summary["seed_contract"] == artifact
    assert artifact["seed"] == 23
    assert provenance["effective_settings"]["seed_contract"] == artifact


def test_no_contract_preserves_unseeded_artifact_behavior(tmp_path):
    config_path = tmp_path / "config.json"
    config = _config(None)
    config.pop("seed_contract")
    config_path.write_text(json.dumps(config), encoding="utf-8")

    summary = run_minecraft_experiment(
        config_path=config_path,
        output_root=tmp_path / "result",
        run_name="unseeded",
    )

    assert "seed_contract" not in summary
    assert not (tmp_path / "result" / "unseeded" / "seed_contract.json").exists()


def test_runtime_launch_forwards_normalized_seed_contract(tmp_path, monkeypatch):
    captured = {}
    config = _config(_contract(seed=31))
    config.update({"task_scenario": "move", "evaluation_arg": {"x": 1}})
    monkeypatch.setattr(
        "model.ollama_config.make_ollama_llm_config",
        lambda: {"api_model": "model", "api_base": "http://example.test"},
    )

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return {}

    monkeypatch.setattr("start_with_config.run", fake_run)

    _execute_real_runtime(
        config,
        dual_dag_config={},
        runtime_result_path=tmp_path / "runtime_result.json",
    )

    assert captured["seed_contract"] == _contract(seed=31)


def test_meta_judger_random_calls_use_the_local_rng():
    source = Path("env/meta_judger.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_random_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "random"
        and node.func.attr != "Random"
    ]

    assert module_random_calls == []
    assert "rng = seed_resolution.contract.random()" in source
