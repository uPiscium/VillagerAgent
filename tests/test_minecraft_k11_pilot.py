import json
from pathlib import Path

import pytest
import benchmarks.minecraft.k11_pilot as k11_pilot

from benchmarks.minecraft.k11_pilot import (
    K11PilotContractError,
    P0_EXPECTED_RUNS,
    _apply_process_outcome,
    _coverage_summary,
    _p0_passes,
    _primary_terminal_count,
    load_p0_manifest,
    run_development_smoke,
)


def _runtime(index: int) -> dict:
    return {
        "api_model": "qwen-test",
        "api_base": "http://127.0.0.1:11434/v1",
        "controller_reasoning_effort": "none",
        "task_type": "none",
        "task_idx": index,
        "agent_num": 2,
        "dig_needed": False,
        "max_task_num": 1,
        "task_goal": f"natural pilot task {index}",
        "host": "127.0.0.1",
        "port": 25565,
        "task_name": f"k11-p0-{index:02d}",
        "minecraft_dual_dag_config": {
            "eac_mode": "dual_dag_advisory",
            "judged_execution": False,
            "production": False,
        },
    }


def _manifest() -> dict:
    return {
        "artifact_id": "minecraft-k11-p0-manifest",
        "artifact_version": 1,
        "study_phase": "K11-P0-instrumentation-validation",
        "prevalence_inference_allowed": False,
        "eac_identity_source": "current_immutable_checkout",
        "runtime_hygiene": {
            "classification": "pre-freeze-runtime-hygiene-change",
            "legacy_default_paths_preserved": True,
            "legacy_cache_lookup_result_preserved": True,
            "legacy_first_save_cache_write_preserved": False,
            "first_save_cache_change": "The first response is retained when the cache file is absent.",
            "scientific_disclosure": "General subject-runtime hygiene change; not K11-only instrumentation.",
        },
        "runs": [
            {"run_id": f"K11-P0-{index:02d}", "runtime": _runtime(index)}
            for index in range(1, 9)
        ],
    }


def _write(tmp_path: Path, value: dict) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_k11_p0_manifest_requires_exactly_eight_advisory_natural_runs(tmp_path: Path) -> None:
    document = _manifest()
    loaded = load_p0_manifest(_write(tmp_path, document))
    assert len(loaded["runs"]) == 8
    assert all(
        row["runtime"]["minecraft_dual_dag_config"]["eac_mode"] == "dual_dag_advisory"
        for row in loaded["runs"]
    )


def test_k11_p0_manifest_rejects_intervention_configuration(tmp_path: Path) -> None:
    document = _manifest()
    document["runs"][0]["runtime"]["forced_sleep"] = 0.01
    with pytest.raises(K11PilotContractError, match="intervention"):
        load_p0_manifest(_write(tmp_path, document))


def test_k11_p0_manifest_requires_qualified_reasoning_setting(tmp_path: Path) -> None:
    document = _manifest()
    document["runs"][0]["runtime"]["controller_reasoning_effort"] = None

    with pytest.raises(K11PilotContractError, match="controller_reasoning_effort=none"):
        load_p0_manifest(_write(tmp_path, document))


def test_k11_p0_manifest_rejects_authority_primary_cohort(tmp_path: Path) -> None:
    document = _manifest()
    document["runs"][0]["runtime"]["minecraft_dual_dag_config"]["eac_mode"] = "dual_dag_authority"
    with pytest.raises(K11PilotContractError, match="dual_dag_advisory"):
        load_p0_manifest(_write(tmp_path, document))


def test_k11_p0_manifest_rejects_judged_or_production_execution(tmp_path: Path) -> None:
    for field in ("judged_execution", "production"):
        document = _manifest()
        document["runs"][0]["runtime"]["minecraft_dual_dag_config"][field] = True
        with pytest.raises(K11PilotContractError, match="non-judged/non-production"):
            load_p0_manifest(_write(tmp_path, document))


def test_k11_p0_manifest_explicitly_forbids_prevalence_inference(tmp_path: Path) -> None:
    document = _manifest()
    document["prevalence_inference_allowed"] = True
    with pytest.raises(K11PilotContractError, match="prevalence inference"):
        load_p0_manifest(_write(tmp_path, document))


def test_k11_p0_manifest_requires_current_checkout_identity_source(tmp_path: Path) -> None:
    document = _manifest()
    document["eac_identity_source"] = "checked_in_stale_premanifest"
    with pytest.raises(K11PilotContractError, match="current immutable checkout"):
        load_p0_manifest(_write(tmp_path, document))


def test_k11_p0_manifest_requires_runtime_hygiene_disclosure(tmp_path: Path) -> None:
    document = _manifest()
    document.pop("runtime_hygiene")

    with pytest.raises(K11PilotContractError, match="runtime-hygiene disclosure"):
        load_p0_manifest(_write(tmp_path, document))


def test_k11_p0_manifest_rejects_user_supplied_eac_identity(tmp_path: Path) -> None:
    for field, value in (
        ("eac_premanifest", "/tmp/stale.json"),
        ("eac_execution_revision", "0" * 40),
    ):
        document = _manifest()
        document["runs"][0]["runtime"]["minecraft_dual_dag_config"][field] = value
        with pytest.raises(K11PilotContractError, match="generated from the current immutable checkout"):
            load_p0_manifest(_write(tmp_path, document))


def test_k11_p0_manifest_contract_tests_remain_independent_of_analysis_error() -> None:
    # The run-level validators, rather than a missing analysis_error field, are
    # the pilot's source of truth (exercised by the pilot integration path).
    assert P0_EXPECTED_RUNS == 8


def test_k11_p0_coverage_requires_exercised_direct_openai_compatible_path() -> None:
    counts = {
        "k11.model_call_started": 1,
        "k11.tool_call_entered": 1,
        "k11.eac_action_prepared": 1,
        "k11.eac_evidence_ingested": 1,
    }
    actor_threads = {("Alice", 1), ("Bob", 2)}

    langchain_only = _coverage_summary(counts, actor_threads, {"LLMHandler.on_llm_start"})
    direct = _coverage_summary(counts, actor_threads, {"OpenAILanguageModel.gpt_api_stream"})

    assert langchain_only["model_calls_observed"] is True
    assert langchain_only["direct_openai_compatible_calls_observed"] is False
    assert all(direct.values())


def test_k11_p0_final_gate_requires_every_run_validation() -> None:
    summaries = [{
        "runtime_error": None,
        "trace_validation": {"valid": True},
        "analysis_validation": {"valid": True},
    } for _ in range(P0_EXPECTED_RUNS)]
    calibration = {"traced": {"trace_validation": {"valid": True}}}

    assert _p0_passes(
        summaries=summaries, calibration_error=None,
        calibration=calibration, coverage_sufficient=True,
    ) is True

    summaries[3]["trace_validation"] = {"valid": False}
    assert _p0_passes(
        summaries=summaries, calibration_error=None,
        calibration=calibration, coverage_sufficient=True,
    ) is False


def test_k11_p0_final_gate_requires_every_offline_analysis() -> None:
    summaries = [{
        "runtime_error": None,
        "trace_validation": {"valid": True},
        "analysis_validation": {"valid": True},
    } for _ in range(P0_EXPECTED_RUNS)]
    summaries[-1]["analysis_validation"] = {"valid": False}

    assert _p0_passes(
        summaries=summaries, calibration_error=None,
        calibration={"traced": {"trace_validation": {"valid": True}}},
        coverage_sufficient=True,
    ) is False


def test_k11_p0_timeout_fails_even_when_validation_artifact_exists() -> None:
    summary = {"runtime_error": None, "runtime_error_type": None}

    result = _apply_process_outcome(summary, {
        "timed_out": True,
        "exit_code": -15,
        "process_group_alive_after_cleanup": False,
        "post_artifact_linger": False,
        "post_parent_group_linger": False,
    })

    assert result["runtime_error_type"] == "RunProcessTimeout"


def test_k11_p0_worker_mode_uses_validated_manifest(tmp_path: Path, monkeypatch) -> None:
    row = {"run_id": "K11-P0-01", "runtime": {}}
    manifest = {"runs": [row]}
    execution = object()
    called = []
    monkeypatch.setattr(k11_pilot, "load_p0_manifest", lambda _path: manifest)
    monkeypatch.setattr(k11_pilot.RuntimeExecution, "resolve", lambda _root: execution)
    monkeypatch.setattr(k11_pilot, "verify_eac_premanifest", lambda *_args, **_kwargs: {})
    def run_single(*args, **kwargs):
        called.append((args, kwargs))
        args[1].mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(k11_pilot, "_run_single_row", run_single)
    monkeypatch.setattr(
        k11_pilot, "cleanup_process_group_descendants",
        lambda **_kwargs: {
            "lingering_processes_before_cleanup": [], "term_sent": False,
            "kill_sent": False, "processes_after_cleanup": [],
        },
    )

    result = k11_pilot.main([
        "--manifest", str(tmp_path / "manifest.json"),
        "--output-root", str(tmp_path / "output"),
        "--worker-run-id", "K11-P0-01",
        "--execution-revision", "a" * 40,
        "--premanifest", str(tmp_path / "premanifest.json"),
        "--manifest-digest", k11_pilot._manifest_digest(manifest),
    ])

    assert result == 0
    assert called[0][0][0] == row
    assert (tmp_path / "output" / "K11-P0-01" / "worker_shutdown.json").is_file()


def test_k11_development_smoke_requires_full_one_run_lifecycle(tmp_path: Path, monkeypatch) -> None:
    row = {"run_id": "K11-P0-01", "runtime": {}}
    monkeypatch.setattr(k11_pilot, "load_p0_manifest", lambda _path: {"runs": [row]})
    monkeypatch.setattr(
        k11_pilot,
        "_prepare_execution_identity",
        lambda root: (
            object(), "a" * 40, root / "K11_P0_EAC_PREMANIFEST.json",
            {"runtime_digest": "sha256:runtime", "premanifest_identity": "premanifest"},
        ),
    )
    summary = {
        "runtime_error": None,
        "trace_validation": {"valid": True},
        "analysis_validation": {"valid": True},
        "primary_terminal_count": 1,
        "event_type_counts": {
            "k11.model_call_started": 1,
            "k11.tool_call_entered": 1,
            "k11.eac_action_prepared": 1,
            "k11.eac_action_terminal": 1,
        },
    }
    monkeypatch.setattr(k11_pilot, "_run_isolated_row", lambda *_args, **_kwargs: summary)

    result = run_development_smoke(
        tmp_path / "manifest.json", output_root=tmp_path / "smoke", run_id="K11-P0-01",
    )

    assert result["smoke_passed"] is True
    assert result["formal_p0"] is False
    artifact = json.loads((tmp_path / "smoke" / "DEV_SMOKE_VALIDATION.json").read_text())
    assert artifact == result


def test_k11_development_smoke_fails_without_terminal_disposition(tmp_path: Path, monkeypatch) -> None:
    row = {"run_id": "K11-P0-01", "runtime": {}}
    monkeypatch.setattr(k11_pilot, "load_p0_manifest", lambda _path: {"runs": [row]})
    monkeypatch.setattr(
        k11_pilot,
        "_prepare_execution_identity",
        lambda root: (
            object(), "a" * 40, root / "premanifest.json",
            {"runtime_digest": "sha256:runtime", "premanifest_identity": "premanifest"},
        ),
    )
    monkeypatch.setattr(k11_pilot, "_run_isolated_row", lambda *_args, **_kwargs: {
        "runtime_error": None,
        "trace_validation": {"valid": True},
        "analysis_validation": {"valid": True},
        "event_type_counts": {
            "k11.model_call_started": 1,
            "k11.tool_call_entered": 1,
            "k11.eac_action_prepared": 1,
        },
    })

    result = run_development_smoke(
        tmp_path / "manifest.json", output_root=tmp_path / "smoke", run_id="K11-P0-01",
    )

    assert result["smoke_passed"] is False


def test_k11_cli_requires_explicit_formal_or_smoke_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        k11_pilot,
        "run_p0_manifest",
        lambda *_args, **_kwargs: pytest.fail("formal P0 must not start implicitly"),
    )

    with pytest.raises(SystemExit) as raised:
        k11_pilot.main([
            "--manifest", str(tmp_path / "manifest.json"),
            "--output-root", str(tmp_path / "output"),
        ])

    assert raised.value.code == 2


def test_k11_smoke_does_not_count_unrelated_terminal_for_primary_preparation() -> None:
    trace = {"events": [
        {
            "event_type": "k11.eac_action_prepared",
            "payload": {"exact_request": {
                "candidate_id": "primary",
                "action": {"identity": "placeBlock"},
            }},
        },
        {
            "event_type": "k11.eac_action_terminal",
            "payload": {"exact_request": {"candidate_id": "other"}},
        },
    ]}

    assert _primary_terminal_count(trace) == 0


def test_k11_worker_rejects_manifest_changed_after_parent_validation(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        k11_pilot,
        "load_p0_manifest",
        lambda _path: {"runs": [{"run_id": "K11-P0-01", "runtime": {}}]},
    )

    with pytest.raises(K11PilotContractError, match="parent-validated snapshot"):
        k11_pilot.main([
            "--manifest", str(tmp_path / "manifest.json"),
            "--output-root", str(tmp_path / "output"),
            "--worker-run-id", "K11-P0-01",
            "--execution-revision", "a" * 40,
            "--premanifest", str(tmp_path / "premanifest.json"),
            "--manifest-digest", "0" * 64,
        ])


def test_k11_cli_routes_formal_p0_only_when_explicit(tmp_path: Path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        k11_pilot,
        "run_p0_manifest",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"p0_passed": True},
    )

    result = k11_pilot.main([
        "--manifest", str(tmp_path / "manifest.json"),
        "--output-root", str(tmp_path / "output"),
        "--formal-p0",
    ])

    assert result == 0
    assert len(calls) == 1
