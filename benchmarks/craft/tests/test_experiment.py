import csv
import json

import pytest
import yaml

from benchmarks.common.run_artifacts import (
    RunDirectoryExistsError,
    finalize_run_directory,
    prepare_run_directory,
)
from benchmarks.craft.config import repo_root
from benchmarks.craft.experiment import (
    ExperimentConfigError,
    _expand_run_specs,
    _experiment_overrides,
    _has_validated_completed_result,
    _is_reusable_completed_run,
    _report_path,
    _structure_override,
    load_experiment,
    run_experiment,
)
from benchmarks.craft.tests.fixtures import write_minimal_structures_dataset
from benchmarks.experiment_provenance import finalize_provenance, write_provenance


def test_load_experiment_manifest():
    manifest = load_experiment("configs/craft/experiments/qwen_batch_v1.yaml")
    experiment = manifest["experiment"]
    assert experiment["name"] == "craft_qwen_batch_v1"
    assert experiment["runs"] == [
        "configs/craft/eval_qwen_ollama.yaml",
        "configs/craft/single_director_qwen_ollama.yaml",
        "configs/craft/official_baseline.yaml",
    ]


def test_load_ollama_model_comparison_manifest():
    manifest = load_experiment("configs/craft/experiments/ollama_model_comparison_v1.yaml")
    experiment = manifest["experiment"]
    assert experiment["name"] == "craft_ollama_model_comparison_v1"
    assert experiment["runs"] == [
        "configs/craft/eval_qwen_ollama.yaml",
        "configs/craft/eval_qwen35_4b_ollama.yaml",
        "configs/craft/eval_qwen36_27b_ollama.yaml",
        "configs/craft/eval_gemma4_26b_ollama.yaml",
        "configs/craft/eval_gemma4_e4b_ollama.yaml",
    ]
    assert experiment["continue_on_error"] is True
    assert experiment["report"]["compact_summary_output"].endswith("summary_ollama_models_v1.csv")


def test_load_qwen_dual_dag_manifest():
    manifest = load_experiment("configs/craft/experiments/qwen_dual_dag_v1.yaml")
    experiment = manifest["experiment"]
    assert experiment["name"] == "craft_qwen_dual_dag_v1"
    assert experiment["runs"] == [
        "configs/craft/eval_qwen_ollama.yaml",
        "configs/craft/eval_qwen_ollama_dual_dag.yaml",
        "configs/craft/single_director_qwen_ollama_dual_dag.yaml",
        "configs/craft/official_baseline.yaml",
    ]


def test_load_qwen_robustness_manifest():
    manifest = load_experiment("configs/craft/experiments/qwen_robustness_v1.yaml")
    experiment = manifest["experiment"]
    assert experiment["name"] == "craft_qwen_robustness_v1"
    assert experiment["runs"][0] == {
        "config": "configs/craft/eval_qwen_ollama.yaml",
        "suffix": "_robust",
        "seeds": [1, 3, 5],
        "structures": [0, 1, 2, 3, 4],
    }
    assert experiment["report"]["variance_summary_output"].endswith("variance_qwen_robustness_v1.csv")


def test_load_qwen_adaptive_gating_manifest():
    manifest = load_experiment("configs/craft/experiments/qwen_adaptive_gating_v1.yaml")
    experiment = manifest["experiment"]
    assert experiment["name"] == "craft_qwen_adaptive_gating_v1"
    assert experiment["runs"] == [
        "configs/craft/eval_qwen_ollama_dual_dag.yaml",
        "configs/craft/eval_qwen_ollama_dual_dag_adaptive.yaml",
    ]
    assert experiment["report"]["compact_summary_output"].endswith("summary_qwen_adaptive_gating_v1.csv")


def test_load_gemma4_progress_smoke_manifest():
    manifest = load_experiment("configs/craft/experiments/gemma4_12b_progress_smoke.yaml")
    experiment = manifest["experiment"]
    assert experiment["name"] == "craft_gemma4_12b_progress_smoke"
    assert "Diagnostic smoke" in experiment["description"]
    assert experiment["overrides"]["structures"] == [0, 1, 2, 3, 4]
    assert experiment["overrides"]["turns"] == 5
    assert experiment["report"]["variance_group_by"] == "run_group"
    assert [run["suffix"] for run in experiment["runs"]] == [
        "_official",
        "_baseline",
        "_dual_dag",
    ]


def test_load_gemma4_progress_full_manifest():
    manifest = load_experiment("configs/craft/experiments/gemma4_12b_progress_full.yaml")
    experiment = manifest["experiment"]
    assert experiment["name"] == "craft_gemma4_12b_progress_full"
    assert experiment["overrides"]["structures"] == list(range(20))
    assert experiment["overrides"]["turns"] == 20
    assert experiment["report"]["variance_group_by"] == "run_group"
    for run in experiment["runs"]:
        assert run["seeds"] == [1, 3, 5, 7, 11]
        assert run["structures"] == list(range(20))


def test_load_gemma4_ablation_smoke_manifest_covers_c0_to_c6():
    manifest = load_experiment("configs/craft/experiments/gemma4_12b_dual_dag_ablation_smoke.yaml")
    experiment = manifest["experiment"]
    assert experiment["name"] == "craft_gemma4_12b_dual_dag_ablation_smoke"
    assert experiment["overrides"]["turns"] == 5
    assert experiment["report"]["variance_group_by"] == "run_group"
    assert [run["suffix"] for run in experiment["runs"]] == [
        "_c0_va_baseline",
        "_c1_metadata_only",
        "_c2_current_evidence",
        "_c3_retrieval",
        "_c4_gating_no_coordination",
        "_c5_clarify_only",
        "_c6_full_dual_dag",
    ]


def test_load_gemma4_clarify_policy_manifests():
    smoke = load_experiment("configs/craft/experiments/gemma4_12b_clarify_policy_smoke.yaml")["experiment"]
    official = load_experiment("configs/craft/experiments/gemma4_12b_clarify_policy_official.yaml")["experiment"]
    sensitivity = load_experiment("configs/craft/experiments/gemma4_12b_clarify_policy_sensitivity.yaml")["experiment"]

    assert smoke["overrides"]["turns"] == 5
    assert [run["suffix"] for run in smoke["runs"]] == [
        "_v0_va_baseline",
        "_v1_dual_dag_clarify_disabled",
        "_v2_dual_dag_current_clarify",
        "_v3_dual_dag_throughput_fix",
        "_v4_dual_dag_value_of_information",
        "_v5_dual_dag_voi_repeated_zero_fix",
    ]
    assert official["overrides"]["turns"] == 20
    assert all(run["overrides"]["craft"]["oracle_n"] == 5 for run in official["runs"])
    assert all(run["structures"] == list(range(20)) for run in official["runs"])
    assert official["runs"][0]["config"] == (
        "configs/craft/official_baseline_gemma4_12b_ollama.yaml"
    )
    assert official["runs"][0]["suffix"] == "_full_upstream_baseline_oracle5"
    assert len(sensitivity["runs"]) == 19
    assert any(run.get("overrides", {}).get("turns") == 30 for run in sensitivity["runs"])


def test_issue_291_final_matrix_checkpoints_only_missing_work():
    experiment = load_experiment(
        "configs/craft/experiments/issue_291_final_replication.yaml"
    )["experiment"]

    expanded = _expand_run_specs(experiment["runs"], experiment["overrides"])

    assert len(expanded) == 221
    assert sum(len(spec["overrides"]["structures"]) for spec in expanded) == 240
    assert sum(
        spec["overrides"]["run_name_suffix"] == "_issue291_final_v0_oracle5_seed1"
        for spec in expanded
    ) == 1
    assert all(
        "_structure" in spec["overrides"]["run_name_suffix"]
        for spec in expanded
        if spec["overrides"]["run_name_suffix"] != "_issue291_final_v0_oracle5_seed1"
    )
    assert "compact_summary_output" not in experiment["report"]


def test_issue_370_remaining_matrix_checkpoints_only_lifecycle_affected_conditions():
    experiment = load_experiment(
        "configs/craft/experiments/gemma4_12b_clarify_policy_official_remaining.yaml"
    )["experiment"]

    expanded = _expand_run_specs(experiment["runs"], experiment["overrides"])

    assert len(experiment["runs"]) == 3
    assert len(expanded) == 180
    assert all(len(spec["overrides"]["structures"]) == 1 for spec in expanded)
    assert {spec["config"] for spec in expanded} == {
        "configs/craft/eval_gemma4_12b_ollama_dual_dag.yaml",
        "configs/craft/eval_gemma4_12b_ollama_dual_dag_clarify_throughput_fix.yaml",
        "configs/craft/eval_gemma4_12b_ollama_dual_dag_value_of_information.yaml",
    }
    assert all(spec["overrides"]["craft"]["oracle_n"] == 5 for spec in expanded)
    assert all(spec["overrides"]["turns"] == 20 for spec in expanded)
    assert "compact_summary_output" not in experiment["report"]


def test_load_experiment_rejects_empty_runs(tmp_path):
    manifest_path = tmp_path / "empty.yaml"
    manifest_path.write_text(yaml.safe_dump({"experiment": {"runs": []}}), encoding="utf-8")
    with pytest.raises(ExperimentConfigError, match="experiment.runs"):
        load_experiment(str(manifest_path))


def test_load_experiment_rejects_non_mapping_overrides(tmp_path):
    manifest_path = tmp_path / "bad_overrides.yaml"
    manifest_path.write_text(
        yaml.safe_dump({"experiment": {"runs": ["config.yaml"], "overrides": ["bad"]}}),
        encoding="utf-8",
    )
    with pytest.raises(ExperimentConfigError, match="experiment.overrides"):
        load_experiment(str(manifest_path))


def test_load_experiment_rejects_bad_run_spec(tmp_path):
    manifest_path = tmp_path / "bad_run_spec.yaml"
    manifest_path.write_text(
        yaml.safe_dump({"experiment": {"runs": [{"config": "config.yaml", "seeds": ["bad"]}]}}),
        encoding="utf-8",
    )
    with pytest.raises(ExperimentConfigError, match="seeds"):
        load_experiment(str(manifest_path))


def test_load_experiment_rejects_bad_run_overrides(tmp_path):
    manifest_path = tmp_path / "bad_run_overrides.yaml"
    manifest_path.write_text(
        yaml.safe_dump({"experiment": {"runs": [{"config": "config.yaml", "overrides": ["bad"]}]}}),
        encoding="utf-8",
    )
    with pytest.raises(ExperimentConfigError, match=r"runs\[\].overrides"):
        load_experiment(str(manifest_path))


def test_experiment_cli_overrides_replace_manifest_overrides():
    overrides = _experiment_overrides(
        {"overrides": {"structures": [0, 1], "turns": 5, "run_name_suffix": "_manifest"}},
        {"structures": [2], "turns": 1, "seed": None, "run_name_suffix": "_smoke"},
    )

    assert overrides == {"structures": [2], "turns": 1, "run_name_suffix": "_smoke"}
    assert _structure_override("0,2") == [0, 2]
    assert str(_report_path("result/craft/comparison.csv", overrides)).endswith(
        "result/craft/comparison_smoke.csv"
    )


def test_expand_run_specs_adds_seed_suffix_and_structure_override():
    runs = [{
        "config": "configs/craft/eval_qwen_ollama.yaml",
        "suffix": "_robust",
        "seeds": [1, 3],
        "structures": [0, 1, 2, 3, 4],
    }]

    expanded = _expand_run_specs(runs, {"run_name_suffix": "_final", "turns": 5})

    assert expanded == [
        {
            "config": "configs/craft/eval_qwen_ollama.yaml",
            "overrides": {
                "run_name_suffix": "_final_robust_seed1",
                "turns": 5,
                "seed": 1,
                "structures": [0, 1, 2, 3, 4],
            },
        },
        {
            "config": "configs/craft/eval_qwen_ollama.yaml",
            "overrides": {
                "run_name_suffix": "_final_robust_seed3",
                "turns": 5,
                "seed": 3,
                "structures": [0, 1, 2, 3, 4],
            },
        },
    ]


def test_expand_run_specs_merges_nested_run_overrides():
    runs = [{
        "config": "configs/craft/eval_gemma4_12b_ollama.yaml",
        "suffix": "_oracle5",
        "overrides": {"craft": {"oracle_n": 5}},
    }]

    expanded = _expand_run_specs(runs, {"turns": 20, "craft": {"use_oracle": True}})

    assert expanded == [{
        "config": "configs/craft/eval_gemma4_12b_ollama.yaml",
        "overrides": {
            "turns": 20,
            "craft": {"use_oracle": True, "oracle_n": 5},
            "run_name_suffix": "_oracle5",
        },
    }]


def test_expand_run_specs_can_checkpoint_each_structure():
    expanded = _expand_run_specs([{
        "config": "config.yaml",
        "suffix": "_final",
        "seeds": [1, 3],
        "structures": [0, 2],
        "split_structures": True,
    }], {"turns": 20})

    assert [spec["overrides"] for spec in expanded] == [
        {"turns": 20, "seed": 1, "structures": [0], "run_name_suffix": "_final_structure0_seed1"},
        {"turns": 20, "seed": 1, "structures": [2], "run_name_suffix": "_final_structure2_seed1"},
        {"turns": 20, "seed": 3, "structures": [0], "run_name_suffix": "_final_structure0_seed3"},
        {"turns": 20, "seed": 3, "structures": [2], "run_name_suffix": "_final_structure2_seed3"},
    ]


def test_load_experiment_rejects_split_without_structures(tmp_path):
    manifest_path = tmp_path / "bad_split.yaml"
    manifest_path.write_text(
        yaml.safe_dump({
            "experiment": {
                "runs": [{"config": "config.yaml", "split_structures": True}],
            }
        }),
        encoding="utf-8",
    )

    with pytest.raises(ExperimentConfigError, match="non-empty structures"):
        load_experiment(str(manifest_path))


def test_run_experiment_dry_run_creates_run_output(tmp_path):
    root = repo_root()
    dataset_path = write_minimal_structures_dataset(tmp_path / "structures_dataset_20.json")
    config_path = tmp_path / "official.yaml"
    config_path.write_text(
        yaml.safe_dump({
            "run": {
                "name": "craft_experiment_dry_run",
                "seed": 3,
                "output_dir": str(tmp_path / "results"),
                "structures": [0],
                "turns": 1,
            },
            "craft": {
                "repo_path": str(root / "external/CRAFT"),
                "dataset_path": str(dataset_path),
                "use_oracle": True,
                "oracle_n": 1,
                "builder_tool_use": False,
            },
            "villageragent": {"enabled": False},
            "models": {
                "director": {
                    "provider": "openai_compatible",
                    "model": "qwen3.5:9b",
                    "base_url": "http://ollama.arc.upiscium.dev/v1",
                    "api_key": "ollama",
                },
                "builder": {
                    "provider": "openai_compatible",
                    "model": "gpt-4o-mini",
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "test",
                },
            },
        }),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "experiment.yaml"
    manifest_path.write_text(
        yaml.safe_dump({
            "experiment": {
                "name": "dry_run",
                "runs": [str(config_path)],
                "report": {"output": str(tmp_path / "comparison.csv")},
            }
        }),
        encoding="utf-8",
    )

    assert run_experiment(
        str(manifest_path),
        dry_run=True,
        overrides={"structures": [0], "turns": 1, "seed": 9, "run_name_suffix": "_smoke"},
    ) == []
    output = tmp_path / "results" / "craft_experiment_dry_run_smoke"
    resolved_config = yaml.safe_load((output / "config.resolved.yaml").read_text())
    command_text = (output / "command.txt").read_text()
    provenance = json.loads((output / "provenance.json").read_text(encoding="utf-8"))
    first_attempt = json.loads((output / "attempt.json").read_text(encoding="utf-8"))["attempt_id"]
    assert resolved_config["run"]["structures"] == [0]
    assert resolved_config["run"]["turns"] == 1
    assert resolved_config["run"]["seed"] == 9
    assert resolved_config["models"]["director"]["api_key"] == "[REDACTED]"
    assert resolved_config["models"]["builder"]["api_key"] == "[REDACTED]"
    assert "--run-name-suffix _smoke" in command_text
    assert provenance["benchmark"] == "craft"
    assert provenance["schema_version"] == "2.0.0"
    experiment_manifest = json.loads(
        (tmp_path / "comparison_smoke.manifest.json").read_text(encoding="utf-8")
    )
    assert experiment_manifest["run_plan"][0]["provenance"] == str(output / "provenance.json")
    assert experiment_manifest["run_plan"][0]["overrides"]["seed"] == 9

    with pytest.raises(RunDirectoryExistsError, match="not empty"):
        run_experiment(
            str(manifest_path),
            dry_run=True,
            overrides={"structures": [0], "turns": 1, "seed": 9, "run_name_suffix": "_smoke"},
        )
    run_experiment(
        str(manifest_path),
        dry_run=True,
        overrides={"structures": [0], "turns": 1, "seed": 9, "run_name_suffix": "_smoke"},
        overwrite=True,
    )
    second_attempt = json.loads((output / "attempt.json").read_text(encoding="utf-8"))["attempt_id"]
    assert second_attempt != first_attempt


def test_run_experiment_records_failed_run_and_writes_summaries(tmp_path, monkeypatch):
    secret = "sentinel-secret-value-12345"
    root = repo_root()
    dataset_path = write_minimal_structures_dataset(tmp_path / "structures_dataset_20.json")
    config_path = tmp_path / "ollama.yaml"
    config_path.write_text(
        yaml.safe_dump({
            "run": {
                "name": "craft_failed_model",
                "seed": 3,
                "output_dir": str(tmp_path / "results"),
                "structures": [0],
                "turns": 1,
            },
            "craft": {
                "repo_path": str(root / "external/CRAFT"),
                "dataset_path": str(dataset_path),
                "use_oracle": True,
                "oracle_n": 1,
                "builder_tool_use": False,
            },
            "villageragent": {"enabled": False},
            "models": {
                "director": {
                    "provider": "openai_compatible",
                    "model": "missing-model",
                    "base_url": "https://ollama.invalid/v1",
                    "api_key": secret,
                },
                "builder": {
                    "provider": "openai_compatible",
                    "model": "missing-model",
                    "base_url": "https://ollama.invalid/v1",
                    "api_key": secret,
                },
            },
        }),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "experiment.yaml"
    manifest_path.write_text(
        yaml.safe_dump({
            "experiment": {
                "name": "failed_run",
                "continue_on_error": True,
                "result_root": str(tmp_path / "results"),
                "runs": [str(config_path)],
                "report": {
                    "output": str(tmp_path / "comparison.csv"),
                    "json_output": str(tmp_path / "comparison.json"),
                    "compact_summary_output": str(tmp_path / "summary.csv"),
                    "compact_summary_json_output": str(tmp_path / "summary.json"),
                    "variance_summary_output": str(tmp_path / "variance.csv"),
                    "variance_summary_json_output": str(tmp_path / "variance.json"),
                },
            }
        }),
        encoding="utf-8",
    )

    original_provenance = {}

    def fail_run(*args, **kwargs):
        run_dir = tmp_path / "results" / "craft_failed_model"
        prepare_run_directory(run_dir, producer="benchmarks.craft.run")
        write_provenance(
            run_dir,
            benchmark="craft",
            command="python -m benchmarks.craft.run",
            resolved_config={"api_key": secret},
            assets=[{
                "name": "craft_dataset",
                "kind": "dataset",
                "required": True,
                "available": True,
                "sha256": "preserved-digest",
            }],
        )
        original_provenance.update(finalize_provenance(run_dir, status="failure"))
        raise RuntimeError(f"model unavailable with {secret}")

    monkeypatch.setattr("benchmarks.craft.experiment.run_config", fail_run)

    rows = run_experiment(str(manifest_path))

    assert rows[0]["run_name"] == "craft_failed_model"
    assert rows[0]["status"] == "failed"
    assert rows[0]["error_type"] == "RuntimeError"
    normalized = tmp_path / "results" / "craft_failed_model" / "normalized"
    failure_summary = json.loads((normalized / "summary.json").read_text(encoding="utf-8"))
    assert failure_summary["failure"]["message"] == "model unavailable with [REDACTED]"
    persisted_provenance = json.loads(
        (tmp_path / "results" / "craft_failed_model" / "provenance.json").read_text(encoding="utf-8")
    )
    assert persisted_provenance["lifecycle"] == original_provenance["lifecycle"]
    assert persisted_provenance["assets"] == original_provenance["assets"]
    assert persisted_provenance["assets"][0]["sha256"] == "preserved-digest"
    for artifact in (tmp_path / "results" / "craft_failed_model").rglob("*"):
        if artifact.is_file():
            assert secret not in artifact.read_text(encoding="utf-8")
    with (tmp_path / "summary.csv").open("r", encoding="utf-8", newline="") as f:
        summary_rows = list(csv.DictReader(f))
    assert summary_rows[0]["status"] == "failed"
    assert summary_rows[0]["leakage_passed"] == "False"
    with (tmp_path / "variance.csv").open("r", encoding="utf-8", newline="") as f:
        variance_rows = list(csv.DictReader(f))
    assert variance_rows[0]["failed_run_count"] == "1"
    assert json.loads((tmp_path / "variance.json").read_text(encoding="utf-8"))["groups"][0][
        "failed_run_count"
    ] == 1

    run_dir = tmp_path / "results" / "craft_failed_model"
    manifest_before = (run_dir / "artifact_manifest.json").read_text(encoding="utf-8")
    monkeypatch.setattr(
        "benchmarks.craft.experiment.run_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(RunDirectoryExistsError("not empty")),
    )
    with pytest.raises(RunDirectoryExistsError, match="not empty"):
        run_experiment(str(manifest_path))
    assert (run_dir / "artifact_manifest.json").read_text(encoding="utf-8") == manifest_before


def test_reusable_completed_run_requires_real_normalized_outputs(tmp_path):
    run_dir = tmp_path / "run"
    attempt_id = prepare_run_directory(run_dir, producer="benchmarks.craft.run")
    finalize_run_directory(
        run_dir,
        attempt_id=attempt_id,
        producer="benchmarks.craft.run",
        status="completed",
    )

    assert _is_reusable_completed_run(run_dir, expected_config={}) is False


def test_reusable_completed_run_requires_matching_resolved_config(tmp_path):
    run_dir = tmp_path / "run"
    attempt_id = prepare_run_directory(run_dir, producer="benchmarks.craft.run")
    normalized = run_dir / "normalized"
    normalized.mkdir()
    (normalized / "summary.json").write_text('{"status": "completed"}\n', encoding="utf-8")
    (normalized / "metrics.csv").write_text("final_progress\n0.5\n", encoding="utf-8")
    (run_dir / "config.resolved.json").write_text(
        json.dumps({"run": {"seed": 1}, "_meta": {"attempt_id": attempt_id}}) + "\n",
        encoding="utf-8",
    )
    finalize_run_directory(
        run_dir,
        attempt_id=attempt_id,
        producer="benchmarks.craft.run",
        status="completed",
    )

    assert _is_reusable_completed_run(
        run_dir,
        expected_config={"run": {"seed": 1}},
    ) is True
    assert _is_reusable_completed_run(
        run_dir,
        expected_config={"run": {"seed": 3}},
    ) is False
    assert _has_validated_completed_result(run_dir) is True


def test_run_experiment_resume_reuses_completed_run(tmp_path, monkeypatch):
    run_dir = tmp_path / "results" / "completed_run"
    manifest_path = tmp_path / "experiment.yaml"
    manifest_path.write_text(
        yaml.safe_dump({
            "experiment": {
                "name": "resume",
                "runs": ["config.yaml"],
                "result_root": str(tmp_path / "results"),
                "report": {"output": str(tmp_path / "comparison.csv")},
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "benchmarks.craft.experiment.load_config",
        lambda *args, **kwargs: {"run": {"name": "completed_run"}},
    )
    monkeypatch.setattr("benchmarks.craft.experiment.output_dir_for_config", lambda config: run_dir)
    monkeypatch.setattr(
        "benchmarks.craft.experiment._is_reusable_completed_run",
        lambda path, **kwargs: True,
    )
    monkeypatch.setattr(
        "benchmarks.craft.experiment.run_config",
        lambda *args, **kwargs: pytest.fail("completed run must not execute"),
    )
    monkeypatch.setattr("benchmarks.craft.experiment.build_comparison_report", lambda *args, **kwargs: [])

    run_experiment(str(manifest_path), resume=True)

    experiment_manifest = json.loads(
        (tmp_path / "comparison.manifest.json").read_text(encoding="utf-8")
    )
    assert experiment_manifest["run_plan"][0]["disposition"] == "reused"


def test_run_experiment_resume_replaces_incomplete_run(tmp_path, monkeypatch):
    run_dir = tmp_path / "results" / "incomplete_run"
    manifest_path = tmp_path / "experiment.yaml"
    manifest_path.write_text(
        yaml.safe_dump({
            "experiment": {
                "name": "resume",
                "runs": ["config.yaml"],
                "result_root": str(tmp_path / "results"),
                "report": {"output": str(tmp_path / "comparison.csv")},
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "benchmarks.craft.experiment.load_config",
        lambda *args, **kwargs: {"run": {"name": "incomplete_run"}},
    )
    monkeypatch.setattr("benchmarks.craft.experiment.output_dir_for_config", lambda config: run_dir)
    monkeypatch.setattr(
        "benchmarks.craft.experiment._is_reusable_completed_run",
        lambda path, **kwargs: False,
    )
    observed = {}

    def fake_run_config(*args, **kwargs):
        observed.update(kwargs)
        return run_dir

    monkeypatch.setattr("benchmarks.craft.experiment.run_config", fake_run_config)
    monkeypatch.setattr("benchmarks.craft.experiment.read_attempt_id", lambda path: "attempt")
    monkeypatch.setattr("benchmarks.craft.experiment.validate_run_attempt", lambda *args, **kwargs: {})
    monkeypatch.setattr("benchmarks.craft.experiment.build_comparison_report", lambda *args, **kwargs: [])

    run_experiment(str(manifest_path), resume=True)

    assert observed["overwrite"] is True
    assert "--resume" in observed["command_text"]


def test_run_experiment_rejects_overwrite_with_resume():
    with pytest.raises(ValueError, match="mutually exclusive"):
        run_experiment("unused.yaml", overwrite=True, resume=True)


def test_run_experiment_resume_rejects_completed_config_mismatch(tmp_path, monkeypatch):
    manifest_path = tmp_path / "experiment.yaml"
    manifest_path.write_text(
        yaml.safe_dump({
            "experiment": {
                "name": "resume",
                "runs": ["config.yaml"],
                "report": {"output": str(tmp_path / "comparison.csv")},
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "benchmarks.craft.experiment.load_config",
        lambda *args, **kwargs: {"run": {"name": "completed_run"}},
    )
    monkeypatch.setattr(
        "benchmarks.craft.experiment.output_dir_for_config",
        lambda config: tmp_path / "completed_run",
    )
    monkeypatch.setattr(
        "benchmarks.craft.experiment._is_reusable_completed_run",
        lambda path, **kwargs: False,
    )
    monkeypatch.setattr(
        "benchmarks.craft.experiment._has_validated_completed_result",
        lambda path: True,
    )
    monkeypatch.setattr(
        "benchmarks.craft.experiment.run_config",
        lambda *args, **kwargs: pytest.fail("mismatched completed run must not execute"),
    )

    with pytest.raises(RunDirectoryExistsError, match="does not match"):
        run_experiment(str(manifest_path), resume=True)
