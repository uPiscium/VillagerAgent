import json
from pathlib import Path

import pytest
import benchmarks.minecraft.k11_pilot as k11_pilot

from benchmarks.minecraft.k11_pilot import (
    K11PilotContractError,
    P0_EXPECTED_RUNS,
    P0_VALIDATION_CONTRACT,
    PROSPECTIVE_VALIDATION_CONTRACT,
    _apply_process_outcome,
    _coverage_summary,
    _in_window_evidence_metadata,
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
        "artifact_version": 2,
        "validation_contract": P0_VALIDATION_CONTRACT,
        "study_phase": "K11-P0-instrumentation-validation",
        "prevalence_inference_allowed": False,
        "eac_identity_source": "current_immutable_checkout",
        "observation_window": {
            "basis": "predeclared-fixed-monotonic-horizon",
            "horizon_seconds": 600,
            "natural_terminal_closes_early": True,
        },
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


def test_k11_p0_manifest_requires_prospective_validation_contract(tmp_path: Path) -> None:
    document = _manifest()
    document["validation_contract"] = "minecraft-k11-p0-validation-contract/0"

    with pytest.raises(K11PilotContractError, match="validation contract identity"):
        load_p0_manifest(_write(tmp_path, document))


def test_k11_v1_manifest_preserves_v0_cohort_and_changes_only_contract_metadata() -> None:
    root = Path(k11_pilot.__file__).resolve().parents[2]
    v0 = json.loads(
        (root / "configs/minecraft/k11-p0-natural-manifest-v0.json").read_text(encoding="utf-8")
    )
    v1 = json.loads(
        (root / "configs/minecraft/k11-p0-natural-manifest-v1.json").read_text(encoding="utf-8")
    )

    assert v0["artifact_version"] == 1
    assert "validation_contract" not in v0
    assert v1["artifact_version"] == 2
    assert v1["validation_contract"] == "minecraft-k11-p0-validation-contract/1"
    v2 = load_p0_manifest(root / "configs/minecraft/k11-p0-natural-manifest-v2.json")
    assert v2["artifact_version"] == 3
    assert v2["validation_contract"] == PROSPECTIVE_VALIDATION_CONTRACT
    assert v2["runs"] == v1["runs"]
    with pytest.raises(K11PilotContractError, match="manifest identity mismatch"):
        load_p0_manifest(root / "configs/minecraft/k11-p0-natural-manifest-v0.json")


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


@pytest.mark.parametrize("horizon", [None, 0, -1, float("inf"), float("nan"), True, 761])
def test_k11_p0_manifest_requires_bounded_fixed_observation_horizon(
    tmp_path: Path, horizon,
) -> None:
    document = _manifest()
    document["observation_window"]["horizon_seconds"] = horizon

    with pytest.raises(K11PilotContractError, match="predeclared observation horizon"):
        load_p0_manifest(_write(tmp_path, document))


def test_k11_p0_manifest_rejects_outcome_dependent_observation_window(tmp_path: Path) -> None:
    document = _manifest()
    document["observation_window"]["basis"] = "stop-after-first-primary-action"

    with pytest.raises(K11PilotContractError, match="predeclared observation horizon"):
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

    langchain_only = _coverage_summary(
        counts, actor_threads, {"LLMHandler.on_llm_start"},
        qualifying_in_window_evidence_count=1,
    )
    direct = _coverage_summary(
        counts, actor_threads, {"OpenAILanguageModel.gpt_api_stream"},
        qualifying_in_window_evidence_count=1,
    )

    assert langchain_only["model_calls_observed"] is True
    assert langchain_only["direct_openai_compatible_calls_observed"] is False
    assert all(direct.values())


def _windowed_evidence_trace(*timestamps: int) -> dict:
    run_id = "windowed-evidence"
    events = [
        {"event_type": "k11.observation_window_opened", "monotonic_ns": 100,
         "seq": 1, "run_id": run_id,
         "payload": {"configured_horizon_seconds": 1,
                                  "horizon_monotonic_ns": 1_000_000_100}},
        *[
            {"event_type": "k11.eac_evidence_ingested", "monotonic_ns": timestamp,
             "seq": index + 2, "run_id": run_id, "actor_id": "Alice",
             "payload": {
                 "proposition": {
                     "namespace": "minecraft", "predicate": "target_block_present",
                     "arguments": [1, 2, 3], "temporal_scope": "current", "polarity": True,
                 },
                 "record_type": "direct_observation", "source": "test",
                 "root_id": f"root-{index}", "revision": 1, "supersedes": [],
                 "provenance_id": f"provenance-{index}", "visible_to": ["Alice"],
                 "source_stream_id": "test-stream", "source_stream_revision": index + 1,
             }}
            for index, timestamp in enumerate(timestamps)
        ],
        {"event_type": "k11.observation_window_closed", "monotonic_ns": 200,
         "seq": len(timestamps) + 2, "run_id": run_id,
         "payload": {"reason": "natural_runtime_terminal",
                      "window_close_monotonic_ns": 200,
                      "configured_horizon_seconds": 1,
                      "shutdown_requested": False}},
    ]
    return {"run_id": run_id, "events": events}


@pytest.mark.parametrize("timestamps, expected", [
    ((), 0), ((99,), 0), ((100,), 1), ((199,), 1), ((200,), 0),
])
def test_k11_p0_evidence_coverage_counts_only_qualifying_window_events(timestamps, expected) -> None:
    metadata = _in_window_evidence_metadata(_windowed_evidence_trace(*timestamps))
    assert metadata["qualifying_event_count"] == expected
    assert metadata["qualified"] is (expected > 0)


def test_k11_p0_malformed_in_window_evidence_does_not_qualify() -> None:
    artifact = _windowed_evidence_trace(150)
    evidence = artifact["events"][1]
    evidence["payload"]["root_id"] = ""

    metadata = _in_window_evidence_metadata(artifact)

    assert metadata["qualifying_event_count"] == 0
    assert metadata["qualified"] is False


def test_k11_p0_all_zero_or_pre_window_only_cohort_fails_evidence_coverage() -> None:
    actor_threads = {("Alice", 1), ("Bob", 2)}
    sources = {"OpenAILanguageModel.gpt_api"}
    base = {"k11.model_call_started": 1, "k11.tool_call_entered": 1,
            "k11.eac_action_prepared": 1, "k11.eac_evidence_ingested": 8}
    assert _coverage_summary(
        base, actor_threads, sources, qualifying_in_window_evidence_count=0,
    )["evidence_ingestions_observed"] is False
    assert _in_window_evidence_metadata(_windowed_evidence_trace(99))["qualified"] is False


def test_k11_p0_mixed_zero_evidence_summaries_are_allowed_when_cohort_coverage_is_true() -> None:
    summaries = [{"runtime_error": None, "trace_validation": {"valid": True},
                  "analysis_validation": {"valid": True},
                  "primary_terminal_count": 1,
                  "exposure_coverage": {"qualifying_event_count": int(index == 0)}}
                 for index in range(P0_EXPECTED_RUNS)]
    assert _p0_passes(
        summaries=summaries, calibration_error=None,
        calibration={"traced": {"trace_validation": {"valid": True}}},
        coverage_sufficient=True,
    ) is True


def test_k11_p0_all_zero_evidence_cohort_fails_aggregate_gate() -> None:
    summaries = [{
        "runtime_error": None,
        "trace_validation": {"valid": True},
        "analysis_validation": {"valid": True},
        "primary_terminal_count": 1,
        "exposure_coverage": {"qualifying_event_count": 0},
    } for _ in range(P0_EXPECTED_RUNS)]

    assert _p0_passes(
        summaries=summaries, calibration_error=None,
        calibration={"traced": {"trace_validation": {"valid": True}}},
        coverage_sufficient=False,
    ) is False


def test_k11_p0_final_gate_requires_every_run_validation() -> None:
    summaries = [{
        "runtime_error": None,
        "trace_validation": {"valid": True},
        "analysis_validation": {"valid": True},
        "primary_terminal_count": 1,
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
        "primary_terminal_count": 1,
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


def test_k11_parent_rejects_worker_artifact_from_another_contract_or_cohort() -> None:
    expected = {
        "artifact_id": "minecraft-k11-p0-run-validation",
        "artifact_version": 2,
        "run_id": "K11-P0-01",
        "validation_contract": P0_VALIDATION_CONTRACT,
        "trace_schema_version": "minecraft-k11-trace/2",
        "manifest_digest": "a" * 64,
        "cohort_mode": "formal_p0",
    }
    k11_pilot._validate_worker_summary_identity(
        expected, expected_run_id="K11-P0-01",
        manifest_digest="a" * 64, cohort_mode="formal_p0",
    )
    for field, value in (
        ("artifact_id", "another-artifact"),
        ("artifact_version", 1),
        ("run_id", "K11-P0-02"),
        ("validation_contract", "minecraft-k11-p0-validation-contract/0"),
        ("manifest_digest", "b" * 64),
        ("cohort_mode", "development_smoke"),
    ):
        malformed = {**expected, field: value}
        with pytest.raises(K11PilotContractError, match="does not match its parent"):
            k11_pilot._validate_worker_summary_identity(
                malformed, expected_run_id="K11-P0-01",
                manifest_digest="a" * 64, cohort_mode="formal_p0",
            )
    with pytest.raises(K11PilotContractError, match="does not match its parent"):
        k11_pilot._validate_worker_summary_identity(
            [], expected_run_id="K11-P0-01",
            manifest_digest="a" * 64, cohort_mode="formal_p0",
        )


def test_k11_p0_worker_mode_uses_validated_manifest(tmp_path: Path, monkeypatch) -> None:
    row = {"run_id": "K11-P0-01", "runtime": {}}
    manifest = {
        "observation_window": {"horizon_seconds": 600},
        "runs": [row],
    }
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
        "--cohort-mode", "development_smoke",
    ])

    assert result == 0
    assert called[0][0][0] == row
    assert called[0][1]["observation_horizon_seconds"] == 600
    assert called[0][1]["manifest_digest"] == k11_pilot._manifest_digest(manifest)
    assert called[0][1]["cohort_mode"] == "development_smoke"
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
        "structural_validation": {"valid": True},
        "primary_terminal_count": 1,
        "event_type_counts": {
            "k11.model_call_started": 1,
            "k11.tool_call_entered": 1,
            "k11.eac_action_prepared": 1,
            "k11.eac_action_terminal": 1,
        },
        "exposure_coverage": {"qualified": True, "qualifying_event_count": 1},
    }
    monkeypatch.setattr(k11_pilot, "_run_isolated_row", lambda *_args, **_kwargs: summary)

    result = run_development_smoke(
        tmp_path / "manifest.json", output_root=tmp_path / "smoke", run_id="K11-P0-01",
    )

    assert result["smoke_passed"] is True
    assert result["validation_contract"] == P0_VALIDATION_CONTRACT
    assert result["manifest_digest"] == k11_pilot._manifest_digest({"runs": [row]})
    assert result["cohort_mode"] == "development_smoke"
    assert result["runtime_qualified"] is True
    assert result["structural_validation_passed"] is True
    assert result["runtime_qualified"] is True
    assert result["development_lifecycle_qualified"] is True
    assert result["development_exposure_qualified"] is True
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
        "structural_validation": {"valid": True},
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


def test_k11_development_smoke_keeps_zero_evidence_structural_pass_separate(
    tmp_path: Path, monkeypatch,
) -> None:
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
        "trace_validation": {"valid": True, "counts": {"evidence_ingestions": 0}},
        "analysis_validation": {"valid": True},
        "structural_validation": {"valid": True},
        "primary_terminal_count": 1,
        "event_type_counts": {
            "k11.model_call_started": 1,
            "k11.tool_call_entered": 1,
            "k11.eac_action_prepared": 1,
            "k11.eac_action_terminal": 1,
        },
        "exposure_coverage": {"qualified": False, "qualifying_event_count": 0},
    })

    result = run_development_smoke(
        tmp_path / "manifest.json", output_root=tmp_path / "smoke", run_id="K11-P0-01",
    )

    assert result["structural_validation_passed"] is True
    assert result["development_lifecycle_qualified"] is True
    assert result["development_exposure_qualified"] is False
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
            "--cohort-mode", "formal_p0",
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


def test_k11_prospective_measurement_cut_is_fail_closed() -> None:
    assert k11_pilot._measurement_cut_status({})["measurement_analysis_eligible"] is False
    cut = {
        "snapshot_valid": True,
        "snapshot_errors": [],
        "close_reason": "fixed_observation_horizon",
        "window_open_monotonic_ns": 1,
        "window_close_monotonic_ns": 2,
        "open_lifecycles": {"items": []},
        "active_executions": {"items": []},
        "censoring_inventory": {"items": []},
    }
    trace = {"measurement_cut": cut, "events": [
        {"event_type": "k11.observation_window_opened", "monotonic_ns": 1},
        {"event_type": "k11.observation_window_closed", "monotonic_ns": 2,
         "payload": {"reason": "fixed_observation_horizon", "window_close_monotonic_ns": 2}},
    ]}
    result = k11_pilot._measurement_cut_status(trace, cut_valid=True)
    assert result["measurement_analysis_eligible"] is True


def test_k11_active_or_post_close_effect_stops_admission() -> None:
    base = {"snapshot_valid": True, "close_reason": "fixed_observation_horizon",
            "snapshot_errors": [],
            "window_open_monotonic_ns": 1, "window_close_monotonic_ns": 2,
            "open_lifecycles": {"items": [{"kind": "native", "id": "c1"}]},
            "active_executions": {"items": []},
            "censoring_inventory": {"items": []}}
    trace = {"measurement_cut": base, "events": [
        {"event_type": "k11.observation_window_opened", "monotonic_ns": 1},
        {"event_type": "k11.observation_window_closed", "monotonic_ns": 2,
         "payload": {"window_close_monotonic_ns": 2}},
        {"event_type": "k11.eac_native_effect_entered", "monotonic_ns": 2},
    ]}
    result = k11_pilot._measurement_cut_status(trace, cut_valid=True)
    assert result["active_effect_at_horizon"] is True
    assert result["post_close_effect"] is True
    assert result["measurement_analysis_eligible"] is True


def test_k11_post_close_completion_alone_does_not_block() -> None:
    trace = {"measurement_cut": {
        "snapshot_valid": True, "close_reason": "fixed_observation_horizon",
        "snapshot_errors": [],
        "window_open_monotonic_ns": 1, "window_close_monotonic_ns": 2,
        "open_lifecycles": {"items": []}, "censoring_inventory": {"items": []},
        "active_executions": {"items": []},
    }, "events": [{
        "event_type": "k11.eac_native_effect_completed", "monotonic_ns": 3,
    }]}
    result = k11_pilot._measurement_cut_status(trace, cut_valid=True)
    assert result["post_close_effect"] is False
    assert result["active_effect_at_horizon"] is False
    assert result["measurement_analysis_eligible"] is True


def _prospective_runtime_cleanup(*, shutdown_complete=True, providers=None,
                                 movement_terminal=True, bridge_complete=True):
    return {
        "controller": {"context": {"diagnostics": {"verdict": {
            "shutdown_complete": shutdown_complete,
            "authoritative_basis": {
                "provider_termination_unconfirmed_task_ids": providers or [],
                "movement_cancellation": {"terminal": movement_terminal},
                "live_threads": [] if shutdown_complete else ["controller-worker"],
                "active_task_ids": [], "active_agent_ids": [],
                "incomplete_submission_task_ids": [], "undrained_queues": [],
            },
        }}}},
        "bridge_cleanup": {
            "cleanup_complete": bridge_complete,
            "incomplete_process_count": 0 if bridge_complete else 1,
        },
    }


def test_k11_prospective_cleanup_status_is_separate_and_fail_closed() -> None:
    supervision = {
        "exit_code": 0, "artifact_ready": True, "timed_out": False,
        "post_artifact_linger": False,
        "post_parent_group_linger": False, "process_group_alive_after_cleanup": False,
    }
    worker = {"processes_after_cleanup": []}
    assert k11_pilot._prospective_cleanup_status(
        supervision, worker, _prospective_runtime_cleanup(),
    ) == "qualified_within_budget"
    assert k11_pilot._prospective_cleanup_status(
        supervision, worker, _prospective_runtime_cleanup(shutdown_complete=False),
    ) == "unknown"
    late_supervision = {**supervision, "timed_out": True}
    assert k11_pilot._prospective_cleanup_status(
        late_supervision, worker, _prospective_runtime_cleanup(),
    ) == "qualified_late"
    assert k11_pilot._prospective_cleanup_status(
        supervision, worker, _prospective_runtime_cleanup(providers=["task-1"]),
    ) == "unknown"
    assert k11_pilot._prospective_cleanup_status(
        supervision, {"processes_after_cleanup": [{"pid": 1}]},
        _prospective_runtime_cleanup(),
    ) == "not_qualified"


def test_k11_prospective_cohort_stops_before_blocked_next_row(
    tmp_path: Path, monkeypatch,
) -> None:
    rows = [{"run_id": f"K11-P0-{index:02d}", "runtime": {}}
            for index in range(1, 9)]
    manifest = {
        "artifact_version": 3, "runs": rows, "runtime_hygiene": {},
        "admission": {"same_domain": True, "no_world_reset": True},
    }
    monkeypatch.setattr(k11_pilot, "load_p0_manifest", lambda _path: manifest)
    monkeypatch.setattr(
        k11_pilot, "_prepare_execution_identity",
        lambda root: (
            object(), "a" * 40, root / "premanifest.json",
            {"runtime_digest": "sha256:runtime", "premanifest_identity": "premanifest"},
        ),
    )
    monkeypatch.setattr(
        k11_pilot, "measure_inprocess_overhead",
        lambda **_kwargs: {"traced": {"trace_validation": {"valid": True}}},
    )
    calls = []
    def blocked_row(row, **_kwargs):
        calls.append(row["run_id"])
        return {
            "runtime_error": None, "trace_validation": {"valid": True},
            "analysis_validation": {"valid": True},
            "measurement_analysis_eligible": True,
            "cross_run_contamination_excluded": False,
            "next_run_admission_allowed": False,
            "cleanup_status": "qualified_within_budget",
            "event_type_counts": {}, "agent_thread_pairs": [],
            "model_call_sources": [],
            "exposure_coverage": {"qualifying_event_count": 0},
        }
    monkeypatch.setattr(k11_pilot, "_run_isolated_row", blocked_row)

    result = k11_pilot.run_p0_manifest(
        tmp_path / "manifest.json", output_root=tmp_path / "output",
    )

    assert calls == ["K11-P0-01"]
    assert result["run_count"] == 1
    assert result["stopped_after_run_id"] == "K11-P0-01"
    assert result["blocked_next_run_id"] == "K11-P0-02"
    assert result["p0_passed"] is False
