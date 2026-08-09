from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.minecraft.approved_experiment import (
    ApprovedExperimentError,
    get_approved_experiment,
)
from benchmarks.minecraft.docker_runtime import pinned_runtime_identity
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


def _execution_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("failure", ["resolution", "endpoint", "credential", "runtime"])
def test_failed_admission_starts_zero_judged_attempts(tmp_path, monkeypatch, failure):
    calls = {"executor": 0, "run": 0}
    execution = _execution_root()
    resolved = _resolved(tmp_path)
    _valid_environment(monkeypatch, resolved)
    monkeypatch.setattr(
        "benchmarks.minecraft.production.get_approved_experiment",
        lambda *_args, **_kwargs: resolved.record,
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.production.pinned_runtime_identity",
        lambda *_args: resolved.record.runtime_identity,
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
            lambda *_args: {"name": "drifted"},
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


def test_diagnostics_runtime_does_not_reuse_existing_approved_premanifest(
    tmp_path, monkeypatch
):
    record = get_approved_experiment("minecraft-judged-production-v1")
    execution = _execution_root()
    assert pinned_runtime_identity(execution) != dict(record.runtime_identity)
    resolved = SimpleNamespace(record=record)
    _valid_environment(monkeypatch, resolved)
    calls = {"resolve": 0, "executor": 0, "run": 0}

    def resolve(*_args, **_kwargs):
        calls["resolve"] += 1
        raise AssertionError("stale runtime must fail before artifact resolution")

    def executor(*_args, **_kwargs):
        calls["executor"] += 1

    def run(*_args, **_kwargs):
        calls["run"] += 1

    monkeypatch.setattr(
        "benchmarks.minecraft.production.resolve_approved_experiment", resolve
    )
    monkeypatch.setattr("benchmarks.minecraft.production.DockerMatrixExecutor", executor)
    monkeypatch.setattr("benchmarks.minecraft.production.run_finalized_matrix", run)

    with pytest.raises(
        ProductionAdmissionError,
        match="runtime implementation does not match the approval",
    ):
        run_approved_production(
            "minecraft-judged-production-v1",
            execution,
            tmp_path / "unused-output",
        )

    assert calls == {"resolve": 0, "executor": 0, "run": 0}


def test_symlinked_execution_root_fails_before_resolution_or_attempt(
    tmp_path, monkeypatch
):
    resolved = _resolved(tmp_path)
    _valid_environment(monkeypatch, resolved)
    execution_link = tmp_path / "execution-link"
    execution_link.symlink_to(_execution_root(), target_is_directory=True)
    calls = {"resolve": 0, "executor": 0, "run": 0}
    monkeypatch.setattr(
        "benchmarks.minecraft.production.get_approved_experiment",
        lambda *_args, **_kwargs: resolved.record,
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.production.pinned_runtime_identity",
        lambda *_args: resolved.record.runtime_identity,
    )

    def resolver(*_args, **_kwargs):
        calls["resolve"] += 1

    def executor(*_args, **_kwargs):
        calls["executor"] += 1

    def run(*_args, **_kwargs):
        calls["run"] += 1

    monkeypatch.setattr(
        "benchmarks.minecraft.production.resolve_approved_experiment", resolver
    )
    monkeypatch.setattr("benchmarks.minecraft.production.DockerMatrixExecutor", executor)
    monkeypatch.setattr("benchmarks.minecraft.production.run_finalized_matrix", run)

    with pytest.raises(
        ProductionAdmissionError,
        match="execution worktree runtime validation failed",
    ):
        run_approved_production("approved-test", execution_link, tmp_path / "output")

    assert calls == {"resolve": 0, "executor": 0, "run": 0}


@pytest.mark.parametrize("relative_argument", [False, True])
def test_successful_admission_passes_historical_worktree_to_registration_and_runner(
    tmp_path, monkeypatch, relative_argument
):
    execution = _execution_root()
    if relative_argument:
        monkeypatch.chdir(execution.parent)
        execution_argument = Path(execution.name)
    else:
        execution_argument = execution
    resolved = _resolved(tmp_path)
    _valid_environment(monkeypatch, resolved)
    monkeypatch.setenv("VILLAGER_MINECRAFT_MODEL_API_BASE", "http://approved.example:11434/v1")
    monkeypatch.setattr(
        "benchmarks.minecraft.production.get_approved_experiment",
        lambda *_args, **_kwargs: resolved.record,
    )
    monkeypatch.setattr(
        "benchmarks.minecraft.production.pinned_runtime_identity",
        lambda *_args: resolved.record.runtime_identity,
    )
    observed = {}

    def resolve(*args, **_kwargs):
        observed["resolve_root"] = args[2]
        return resolved

    monkeypatch.setattr(
        "benchmarks.minecraft.production.resolve_approved_experiment", resolve
    )

    executor = object()

    def build_executor(identity, *, execution_root):
        observed["identity"] = identity
        observed["execution"] = execution_root
        return executor

    def run(path, output, *, executor, repo_root):
        observed["run"] = (path, output, executor, repo_root)
        return {"gate_passed": True}

    monkeypatch.setattr("benchmarks.minecraft.production.DockerMatrixExecutor", build_executor)
    monkeypatch.setattr("benchmarks.minecraft.production.run_finalized_matrix", run)

    result = run_approved_production("approved-test", execution_argument, tmp_path / "output")

    assert result["gate_passed"] is True
    assert observed["identity"]["runtime"] == resolved.record.runtime_identity
    assert observed["execution"].root == execution
    assert observed["resolve_root"] == execution
    assert observed["run"][2] is executor
    assert observed["run"][3] == execution.resolve()
