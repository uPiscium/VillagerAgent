import json
from pathlib import Path

import pytest

from benchmarks.minecraft.k11_pilot import (
    K11PilotContractError,
    P0_EXPECTED_RUNS,
    _p0_passes,
    load_p0_manifest,
)


def _runtime(index: int) -> dict:
    return {
        "api_model": "qwen-test",
        "api_base": "http://127.0.0.1:11434/v1",
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
