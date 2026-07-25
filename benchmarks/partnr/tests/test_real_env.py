import gzip
import json
import subprocess
from pathlib import Path

import pytest

from benchmarks.partnr import real_smoke
from benchmarks.partnr.real_env import (
    PARTNR_SOURCE_COMMIT,
    PARTNRRuntimeConfig,
    build_bounded_smoke_command,
    build_step_zero_command,
    inspect_real_preflight,
    write_bounded_dataset,
)
from benchmarks.partnr.real_smoke import _collect_official_metrics, run_official_gate


def _write_fixture_runtime(tmp_path: Path, *, episode_count: int = 5) -> PARTNRRuntimeConfig:
    source = tmp_path / "partnr"
    for relative in (
        "README.md",
        "INSTALLATION.md",
        "habitat_llm/agent/env/environment_interface.py",
        "habitat_llm/agent/env/measures.py",
        "habitat_llm/examples/verify_episodes.py",
        "habitat_llm/examples/planner_demo.py",
        "habitat_llm/conf/baselines/heuristic_full_obs.yaml",
    ):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    for relative in (
        "third_party/habitat-lab/pyproject.toml",
        "third_party/semantic_exploration/README.md",
        "third_party/transformers-CFG/setup.py",
    ):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    scenes = source / "data/hssd-hab"
    for relative in (
        "metadata/object_categories_filtered.csv",
        "metadata/fpmodels-with-decomposed.csv",
        "metadata/room_objects.json",
        "metadata/affordance_objects.csv",
    ):
        path = scenes / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    dataset = source / "data/datasets/partnr_episodes/v0_0/val_mini.json.gz"
    dataset.parent.mkdir(parents=True)
    with gzip.open(dataset, "wt", encoding="utf-8") as handle:
        json.dump({"episodes": [
            {
                "episode_id": str(index),
                "instruction": f"fixture instruction {index}",
                "scene_id": "fixture.scene_instance.json",
                "evaluation_propositions": [],
            }
            for index in range(episode_count)
        ]}, handle)
    return PARTNRRuntimeConfig(
        source_root=source,
        dataset_path=dataset,
        scene_root=scenes,
        output_dir=tmp_path / "output",
        python_executable=Path("/isolated/bin/python"),
        episode_limit=4,
        wall_timeout_seconds=30,
    )


def test_ready_preflight_requires_pinned_isolated_runtime_and_data(tmp_path):
    runtime = _write_fixture_runtime(tmp_path)

    preflight = inspect_real_preflight(
        runtime,
        module_available=lambda _name: True,
        python_version=(3, 9),
        source_commit=PARTNR_SOURCE_COMMIT,
        headless_context_ready=True,
        scene_assets_ready=True,
    )

    assert preflight["ready"] is True
    assert preflight["missing"] == []
    assert preflight["dataset_audit"]["episode_count"] == 5
    assert preflight["dataset_audit"]["first_episode_id"] == "0"
    assert all("/tmp/" not in value for value in preflight["configured_paths"].values())


def test_preflight_reports_missing_prerequisites_without_launch(tmp_path):
    runtime = PARTNRRuntimeConfig(
        source_root=tmp_path / "missing-source",
        dataset_path=tmp_path / "missing-data.json.gz",
        scene_root=tmp_path / "missing-scenes",
        output_dir=tmp_path / "output",
    )

    preflight = inspect_real_preflight(
        runtime,
        module_available=lambda _name: False,
        python_version=(3, 10),
        source_commit=None,
        headless_context_ready=False,
        scene_assets_ready=False,
    )

    assert preflight["ready"] is False
    assert "source_tree" in preflight["missing"]
    assert "source_commit" in preflight["missing"]
    assert "python_3_9" in preflight["missing"]
    assert "val_mini_dataset" in preflight["missing"]
    assert "hssd_scene_root" in preflight["missing"]
    assert "headless_context" in preflight["missing"]
    assert "bounded_scene_assets" in preflight["missing"]
    with pytest.raises(RuntimeError, match="preflight failed"):
        run_official_gate(runtime, mode="step-zero")


def test_bounded_dataset_keeps_only_prespecified_first_episodes(tmp_path):
    runtime = _write_fixture_runtime(tmp_path, episode_count=6)
    output = tmp_path / "subset.json.gz"

    audit = write_bounded_dataset(runtime.dataset_path, output, episode_limit=4)

    with gzip.open(output, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    assert audit["episode_count"] == 4
    assert audit["episode_ids"] == ["0", "1", "2", "3"]
    assert [episode["episode_id"] for episode in payload["episodes"]] == ["0", "1", "2", "3"]
    with pytest.raises(ValueError, match="between 1 and 4"):
        write_bounded_dataset(runtime.dataset_path, output, episode_limit=5)


def test_official_commands_use_bounded_dataset_and_oracle_baseline(tmp_path):
    runtime = _write_fixture_runtime(tmp_path)
    dataset = tmp_path / "bounded.json.gz"

    verifier = build_step_zero_command(runtime, dataset)
    smoke = build_bounded_smoke_command(runtime, dataset)

    assert verifier[:3] == ["/isolated/bin/python", "-m", "habitat_llm.examples.verify_episodes"]
    assert "num_proc=1" in verifier
    assert f"habitat.dataset.data_path={dataset}" in verifier
    assert smoke[:3] == ["/isolated/bin/python", "-m", "habitat_llm.examples.planner_demo"]
    assert "baselines/heuristic_full_obs.yaml" in smoke
    assert "num_proc=1" in smoke


def test_official_metrics_normalize_nested_stats_and_account_for_missing(tmp_path):
    runtime = _write_fixture_runtime(tmp_path)
    subset = runtime.output_dir / "inputs/val_mini_first_4.json.gz"
    subset.parent.mkdir(parents=True)
    with gzip.open(subset, "wt", encoding="utf-8") as handle:
        json.dump({"episodes": [{"episode_id": str(index)} for index in range(4)]}, handle)
    stats = runtime.output_dir / "bounded_heuristic/run/stats"
    stats.mkdir(parents=True)
    (stats / "0.json").write_text(
        json.dumps({
            "success": True,
            "stats": json.dumps({
                "task_percent_complete": 1.0,
                "task_state_success": 1.0,
                "runtime": 2.5,
                "sim_step_count": 12.0,
            }),
        }),
        encoding="utf-8",
    )
    (stats / "1.json").write_text(
        json.dumps({"success": False, "stats": "{}"}), encoding="utf-8"
    )

    metrics = _collect_official_metrics(runtime, "bounded")

    assert metrics["completed_episode_ids"] == ["0", "1"]
    assert metrics["successful_episode_ids"] == ["0"]
    assert metrics["failed_episode_ids"] == ["1"]
    assert metrics["missing_episode_ids"] == ["2", "3"]
    assert metrics["records"][0]["sim_step_count"] == 12.0
    assert "payload" not in metrics["records"][0]


def test_official_timeout_preserves_missing_episode_accounting(tmp_path, monkeypatch):
    runtime = _write_fixture_runtime(tmp_path)
    monkeypatch.setattr(
        real_smoke,
        "inspect_real_preflight",
        lambda _runtime: {"ready": True, "missing": []},
    )

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("partnr", runtime.wall_timeout_seconds, output=b"partial")

    monkeypatch.setattr(real_smoke.subprocess, "run", timeout)

    result = run_official_gate(runtime, mode="step-zero")

    assert result["status"] == "timed_out"
    assert result["returncode"] is None
    assert result["stdout"] == "partial"
    assert result["official_metrics"]["missing_episode_ids"] == ["0"]


def test_runtime_rejects_unbounded_limits():
    with pytest.raises(ValueError, match="between 1 and 4"):
        PARTNRRuntimeConfig(episode_limit=5)
    with pytest.raises(ValueError, match="between 1 and 1800"):
        PARTNRRuntimeConfig(wall_timeout_seconds=1801)
