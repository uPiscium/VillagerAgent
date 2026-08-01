from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.minecraft.approved_experiment import ApprovedExperimentError
from benchmarks.minecraft.production import (
    ProductionAdmissionError,
    run_approved_production,
)


def _resolved(tmp_path: Path, endpoint: str = "http://approved.example:11434"):
    runtime = {"name": "runtime", "image": "runtime@sha256:" + "1" * 64, "digest": "sha256:" + "2" * 64}
    model = {"provider": "ollama", "name": "model:tag", "digest": "3" * 64}
    return SimpleNamespace(
        record=SimpleNamespace(
            experiment_id="approved-test",
            approved_source_revision="a" * 40,
            canonical_premanifest_identity="b" * 64,
            model_endpoint=endpoint,
            runtime_identity=runtime,
            expected={"model": model},
        ),
        premanifest_path=tmp_path / "premanifest.json",
        spec=SimpleNamespace(
            runtime=SimpleNamespace(**runtime),
            model=SimpleNamespace(**model),
            generation=SimpleNamespace(
                temperature=0.0, top_p=1.0, max_tokens=1,
                timeout_seconds=1.0, max_iterations=1,
            ),
        ),
    )


def _valid_environment(monkeypatch, resolved):
    model = resolved.record.expected["model"]
    monkeypatch.setenv("VILLAGER_MINECRAFT_MODEL_API_BASE", resolved.record.model_endpoint)
    monkeypatch.setenv("VILLAGER_MINECRAFT_MODEL_PROVIDER", model["provider"])
    monkeypatch.setenv("VILLAGER_MINECRAFT_MODEL_NAME", model["name"])
    monkeypatch.setenv("VILLAGER_MINECRAFT_MODEL_DIGEST", model["digest"])
    monkeypatch.setenv("VILLAGER_MINECRAFT_MODEL_API_KEY_ENV", "TEST_OLLAMA_KEY")
    monkeypatch.setenv("TEST_OLLAMA_KEY", "test-key")


@pytest.mark.parametrize("failure", ["resolution", "endpoint", "credential", "runtime"])
def test_failed_admission_starts_zero_judged_attempts(tmp_path, monkeypatch, failure):
    calls = {"executor": 0, "run": 0}
    execution = tmp_path / "execution"
    execution.mkdir()
    resolved = _resolved(tmp_path)
    _valid_environment(monkeypatch, resolved)
    monkeypatch.setattr(
        "benchmarks.minecraft.production.get_approved_experiment",
        lambda *_args, **_kwargs: resolved.record,
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.production.pinned_runtime_identity",
        lambda: resolved.record.runtime_identity,
    )
    if failure == "resolution":
        def resolver(*_args, **_kwargs):
            raise ApprovedExperimentError("rejected")
    else:
        resolver = lambda *_args, **_kwargs: resolved
    if failure == "endpoint":
        monkeypatch.setenv("VILLAGER_MINECRAFT_MODEL_API_BASE", "http://drift.example:11434")
    if failure == "credential":
        monkeypatch.delenv("TEST_OLLAMA_KEY")
    if failure == "runtime":
        monkeypatch.setattr(
            "benchmarks.minecraft.production.pinned_runtime_identity",
            lambda: {"name": "drifted"},
        )
    monkeypatch.setattr("benchmarks.minecraft.production.resolve_approved_experiment", resolver)

    def executor(*_args, **_kwargs):
        calls["executor"] += 1

    def run(*_args, **_kwargs):
        calls["run"] += 1

    monkeypatch.setattr("benchmarks.minecraft.production.DockerMatrixExecutor", executor)
    monkeypatch.setattr("benchmarks.minecraft.production.run_finalized_matrix", run)

    with pytest.raises(ProductionAdmissionError):
        run_approved_production("approved-test", execution, tmp_path / "output")
    assert calls == {"executor": 0, "run": 0}


def test_successful_admission_passes_historical_worktree_to_registration_and_runner(
    tmp_path, monkeypatch
):
    execution = tmp_path / "execution"
    execution.mkdir()
    resolved = _resolved(tmp_path)
    _valid_environment(monkeypatch, resolved)
    monkeypatch.setenv("VILLAGER_MINECRAFT_MODEL_API_BASE", "http://approved.example:11434/v1")
    monkeypatch.setattr(
        "benchmarks.minecraft.production.get_approved_experiment",
        lambda *_args, **_kwargs: resolved.record,
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.production.pinned_runtime_identity",
        lambda: resolved.record.runtime_identity,
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.production.resolve_approved_experiment",
        lambda *_args, **_kwargs: resolved,
    )
    observed = {}

    executor = object()

    def build_executor(identity):
        observed["identity"] = identity
        return executor

    def run(path, output, *, executor, repo_root):
        observed["run"] = (path, output, executor, repo_root)
        return {"gate_passed": True}

    monkeypatch.setattr("benchmarks.minecraft.production.DockerMatrixExecutor", build_executor)
    monkeypatch.setattr("benchmarks.minecraft.production.run_finalized_matrix", run)

    result = run_approved_production("approved-test", execution, tmp_path / "output")

    assert result["gate_passed"] is True
    assert observed["identity"]["runtime"] == resolved.record.runtime_identity
    assert observed["run"][2] is executor
    assert observed["run"][3] == execution.resolve()
