"""K11 P0 instrumentation-validation runner.

P0 is a development pilot only. This runner rejects anything other than the
eight-run Advisory/non-judged natural cohort described by the K11 protocol
draft. It never injects semantic changes or synchronization delays.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, Mapping

from benchmarks.minecraft.eac_identity import (
    resolve_git_revision,
    runtime_identity,
    verify_eac_premanifest,
)
from benchmarks.minecraft.k11_analysis import analyze_trace, validate_p0_analysis
from benchmarks.minecraft.k11_calibration import measure_inprocess_overhead
from benchmarks.minecraft.k11_instrumentation import K11ProcessInstrumentation
from benchmarks.minecraft.k11_process import cleanup_process_group_descendants, supervise_process
from benchmarks.minecraft.k11_trace import (
    PRIMARY_EFFECT_ACTIONS,
    K11TraceRecorder,
    event_in_observation_window,
    observation_window_bounds,
    valid_evidence_ingestion,
    validate_p0_trace,
    validate_trace,
)
from env.runtime_execution import RuntimeExecution
from env.runtime_paths import RuntimePaths
from start_with_config import run as run_villageragent


ROOT = Path(__file__).resolve().parents[2]
P0_MANIFEST_ID = "minecraft-k11-p0-manifest"
P0_MANIFEST_VERSION = 2
P0_VALIDATION_CONTRACT = "minecraft-k11-p0-validation-contract/1"
P0_VALIDATION_ARTIFACT_VERSION = 2
DEVELOPMENT_SMOKE_ARTIFACT_VERSION = 2
P0_EXPECTED_RUNS = 8
COHORT_MODES = frozenset({"development_smoke", "formal_p0"})
EAC_IDENTITY_SOURCE = "current_immutable_checkout"
RUN_PROCESS_TIMEOUT_SECONDS = 900.0
RUN_COMPLETION_GRACE_SECONDS = 10.0
RUN_TERMINATION_GRACE_SECONDS = 5.0
RUN_KILL_GRACE_SECONDS = 5.0
RUN_STARTUP_BUDGET_SECONDS = 120.0
MAX_OBSERVATION_HORIZON_SECONDS = (
    RUN_PROCESS_TIMEOUT_SECONDS
    - RUN_STARTUP_BUDGET_SECONDS
    - RUN_COMPLETION_GRACE_SECONDS
    - RUN_TERMINATION_GRACE_SECONDS
    - RUN_KILL_GRACE_SECONDS
)
FORBIDDEN_CONFIG_KEYS = frozenset({
    "forced_sleep",
    "prepare_sleep",
    "semantic_revision_injection",
    "evidence_injection",
    "synchronization_barrier",
    "planner_suppression",
    "llm_suppression",
    "force_retained_request",
})


class K11PilotContractError(ValueError):
    pass


def _manifest_digest(manifest: Mapping[str, Any]) -> str:
    encoded = json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _primary_terminal_count(trace_artifact: Mapping[str, Any]) -> int:
    events = trace_artifact.get("events", [])
    primary_candidate_ids = {
        event.get("payload", {}).get("exact_request", {}).get("candidate_id")
        for event in events
        if event.get("event_type") == "k11.eac_action_prepared"
        and event.get("payload", {}).get("exact_request", {}).get("action", {}).get("identity")
        in PRIMARY_EFFECT_ACTIONS
    }
    return sum(
        event.get("event_type") == "k11.eac_action_terminal"
        and event.get("payload", {}).get("exact_request", {}).get("candidate_id")
        in primary_candidate_ids
        for event in events
    )


def _load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise K11PilotContractError("K11 P0 manifest must be a JSON object")
    return value


def load_p0_manifest(path: str | Path) -> dict[str, Any]:
    document = _load_json(path)
    if document.get("artifact_id") != P0_MANIFEST_ID or document.get("artifact_version") != P0_MANIFEST_VERSION:
        raise K11PilotContractError("K11 P0 manifest identity mismatch")
    if document.get("validation_contract") != P0_VALIDATION_CONTRACT:
        raise K11PilotContractError("K11 P0 validation contract identity mismatch")
    if document.get("study_phase") != "K11-P0-instrumentation-validation":
        raise K11PilotContractError("K11 P0 study phase mismatch")
    if document.get("prevalence_inference_allowed") is not False:
        raise K11PilotContractError("P0 must explicitly forbid prevalence inference")
    if document.get("eac_identity_source") != EAC_IDENTITY_SOURCE:
        raise K11PilotContractError("K11 P0 must bind EAC identity to the current immutable checkout")
    window = document.get("observation_window")
    horizon = window.get("horizon_seconds") if isinstance(window, Mapping) else None
    if (not isinstance(window, Mapping)
            or window.get("basis") != "predeclared-fixed-monotonic-horizon"
            or window.get("natural_terminal_closes_early") is not True
            or isinstance(horizon, bool) or not isinstance(horizon, (int, float))
            or not math.isfinite(horizon) or horizon <= 0
            or horizon > MAX_OBSERVATION_HORIZON_SECONDS):
        raise K11PilotContractError(
            "K11 P0 requires a valid predeclared observation horizon below the process deadline"
        )
    runtime_hygiene = document.get("runtime_hygiene")
    if (not isinstance(runtime_hygiene, Mapping)
            or runtime_hygiene.get("classification") != "pre-freeze-runtime-hygiene-change"
            or runtime_hygiene.get("legacy_default_paths_preserved") is not True
            or runtime_hygiene.get("legacy_cache_lookup_result_preserved") is not True
            or runtime_hygiene.get("legacy_first_save_cache_write_preserved") is not False
            or not isinstance(runtime_hygiene.get("first_save_cache_change"), str)
            or not runtime_hygiene["first_save_cache_change"]
            or not isinstance(runtime_hygiene.get("scientific_disclosure"), str)
            or not runtime_hygiene["scientific_disclosure"]):
        raise K11PilotContractError("K11 P0 manifest must bind the pre-freeze runtime-hygiene disclosure")
    runs = document.get("runs")
    if not isinstance(runs, list) or len(runs) != P0_EXPECTED_RUNS:
        raise K11PilotContractError("K11 P0 manifest must contain exactly eight runs")
    run_ids = [row.get("run_id") for row in runs if isinstance(row, Mapping)]
    if len(run_ids) != P0_EXPECTED_RUNS or len(set(run_ids)) != P0_EXPECTED_RUNS or any(
        not isinstance(value, str) or not value for value in run_ids
    ):
        raise K11PilotContractError("K11 P0 run IDs must be unique non-empty strings")
    for row in runs:
        _validate_run(row)
    return document


def _validate_run(row: Mapping[str, Any]) -> None:
    if not isinstance(row, Mapping):
        raise K11PilotContractError("K11 P0 run descriptor must be an object")
    config = row.get("runtime")
    if not isinstance(config, Mapping):
        raise K11PilotContractError("K11 P0 run requires a runtime object")
    if FORBIDDEN_CONFIG_KEYS.intersection(config):
        raise K11PilotContractError("K11 P0 runtime contains intervention-only configuration")
    if config.get("task_type") != "none":
        raise K11PilotContractError("K11 P0 must remain inside the admitted non-judged EAC task_type=none boundary")
    if config.get("controller_reasoning_effort") != "none":
        raise K11PilotContractError(
            "K11 P0 must bind the qualified controller_reasoning_effort=none setting"
        )
    dual = config.get("minecraft_dual_dag_config")
    if not isinstance(dual, Mapping):
        raise K11PilotContractError("K11 P0 requires minecraft_dual_dag_config")
    if dual.get("eac_mode") != "dual_dag_advisory":
        raise K11PilotContractError("K11 P0 primary cohort must use dual_dag_advisory")
    if dual.get("judged_execution") is not False or dual.get("production") is not False:
        raise K11PilotContractError("K11 P0 must preserve explicit non-judged/non-production EAC admission")
    if "eac_premanifest" in dual or "eac_execution_revision" in dual:
        raise K11PilotContractError("K11 P0 EAC identity is generated from the current immutable checkout, not supplied per run")
    for key in (
        "api_model", "api_base", "task_idx", "agent_num", "dig_needed", "max_task_num",
        "task_goal", "host", "port", "task_name",
    ):
        if key not in config:
            raise K11PilotContractError(f"K11 P0 runtime is missing {key}")


def _assert_clean_checkout(root: Path) -> None:
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise K11PilotContractError("K11 P0 could not verify execution checkout cleanliness")
    if result.stdout.strip():
        raise K11PilotContractError("K11 P0 requires a clean immutable execution checkout")


def _prepare_execution_identity(output_root: Path) -> tuple[RuntimeExecution, str, Path, dict[str, Any]]:
    execution_root = ROOT.resolve(strict=True)
    resolved_output = output_root.resolve()
    if resolved_output == execution_root or execution_root in resolved_output.parents:
        raise K11PilotContractError("K11 P0 output root must be outside the execution repository")
    _assert_clean_checkout(execution_root)
    execution = RuntimeExecution.resolve(execution_root)
    revision = resolve_git_revision(execution_root)
    identity = runtime_identity(execution, execution_revision=revision)
    premanifest_path = output_root / "K11_P0_EAC_PREMANIFEST.json"
    premanifest_path.write_text(
        json.dumps(identity, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return execution, revision, premanifest_path.resolve(), identity


def _runtime_kwargs(
    row: Mapping[str, Any],
    run_dir: Path,
    *,
    execution: RuntimeExecution,
    execution_revision: str,
    premanifest_path: Path,
) -> dict[str, Any]:
    config = dict(row["runtime"])
    dual = dict(config["minecraft_dual_dag_config"])
    dual.update({
        "eac_premanifest": str(premanifest_path),
        "eac_execution_revision": execution_revision,
    })
    config["minecraft_dual_dag_config"] = dual
    run_id = row["run_id"]
    runtime_root = run_dir / "runtime"
    runtime_paths = RuntimePaths.isolated(runtime_root)
    runtime_paths.ensure_directories()
    config.update({
        "runtime_paths": runtime_paths,
        "runtime_result_path": str(run_dir / "runtime_result.json"),
        "runtime_event_path": str(run_dir / "runtime_events.jsonl"),
        "attempt_id": run_id,
        "emit_controller_terminal_event": True,
        "runtime_execution": execution,
    })
    config.setdefault("role", "same")
    config.setdefault("api_key_list", None)
    config.setdefault("document_file", None)
    config.setdefault("document", {})
    config.setdefault("task_scenario", None)
    config.setdefault("require_action_evidence", False)
    config.setdefault("seed_contract", None)
    config.setdefault("world_initialization", None)
    config.setdefault("position_convention", None)
    return config


def _event_type_counts(trace_artifact: Mapping[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in trace_artifact.get("events", []):
        event_type = event.get("event_type")
        if isinstance(event_type, str):
            counts[event_type] = counts.get(event_type, 0) + 1
    return counts


def _qualifying_in_window_evidence_events(trace_artifact: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return evidence events inside the declared trace window."""
    bounds = observation_window_bounds(trace_artifact)
    if bounds is None:
        return []
    qualifying = []
    for event in trace_artifact.get("events", []):
        if (isinstance(event, Mapping)
                and event.get("event_type") == "k11.eac_evidence_ingested"
                and event_in_observation_window(event, bounds)
                and valid_evidence_ingestion(event, run_id=trace_artifact.get("run_id"))):
            qualifying.append(event)
    return qualifying


def _in_window_evidence_metadata(trace_artifact: Mapping[str, Any]) -> dict[str, Any]:
    bounds = observation_window_bounds(trace_artifact)
    events = _qualifying_in_window_evidence_events(trace_artifact)
    return {
        "observation_window_present": bounds is not None,
        "observation_window_start_monotonic_ns": bounds[0] if bounds else None,
        "observation_window_end_monotonic_ns": bounds[1] if bounds else None,
        "qualifying_event_count": len(events),
        "qualified": bool(events),
    }


def _coverage_summary(
    event_counts: Mapping[str, int], actor_threads, model_call_sources, *,
    qualifying_in_window_evidence_count: int,
) -> dict[str, bool]:
    sources = set(model_call_sources)
    return {
        "model_calls_observed": event_counts.get("k11.model_call_started", 0) > 0,
        "direct_openai_compatible_calls_observed": bool(
            {"OpenAILanguageModel.gpt_api", "OpenAILanguageModel.gpt_api_stream"} & sources
        ),
        "tool_calls_observed": event_counts.get("k11.tool_call_entered", 0) > 0,
        "prepared_actions_observed": event_counts.get("k11.eac_action_prepared", 0) > 0,
        "evidence_ingestions_observed": qualifying_in_window_evidence_count > 0,
        "multiple_actor_thread_pairs_observed": len(actor_threads) > 1,
    }


def _p0_passes(*, summaries: list[Mapping[str, Any]], calibration_error: str | None,
               calibration: Mapping[str, Any], coverage_sufficient: bool) -> bool:
    """Gate P0 on every run's validation, never aggregate event presence alone."""
    return bool(
        len(summaries) == P0_EXPECTED_RUNS
        and all(item.get("runtime_error") is None for item in summaries)
        and all(item.get("trace_validation", {}).get("valid") is True for item in summaries)
        and all(item.get("analysis_validation", {}).get("valid") is True for item in summaries)
        and calibration_error is None
        and calibration.get("traced", {}).get("trace_validation", {}).get("valid") is True
        and coverage_sufficient
    )


def _run_single_row(
    row: Mapping[str, Any],
    run_dir: Path,
    *,
    execution: RuntimeExecution,
    execution_revision: str,
    premanifest_path: Path,
    observation_horizon_seconds: float,
    manifest_digest: str,
    cohort_mode: str,
) -> dict[str, Any]:
    run_id = row["run_id"]
    if run_dir.exists() and any(run_dir.iterdir()):
        raise K11PilotContractError(f"K11 P0 run directory already contains data: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    trace = K11TraceRecorder(run_id)
    error = None
    error_type = None
    result = None
    try:
        with K11ProcessInstrumentation(
            trace, observation_horizon_seconds=observation_horizon_seconds,
        ):
            result = run_villageragent(**_runtime_kwargs(
                row,
                run_dir,
                execution=execution,
                execution_revision=execution_revision,
                premanifest_path=premanifest_path,
            ))
    except BaseException as exc:
        error = str(exc)
        error_type = type(exc).__name__
        (run_dir / "exception.txt").write_text(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            encoding="utf-8",
        )
    finally:
        trace.write_json(run_dir / "k11_trace.json")

    trace_artifact = trace.artifact()
    generic_validation = validate_trace(trace_artifact)
    validation = validate_p0_trace(trace_artifact)
    event_counts = _event_type_counts(trace_artifact)
    model_call_sources = sorted({
        event.get("source")
        for event in trace_artifact.get("events", [])
        if event.get("event_type") == "k11.model_call_started"
        and isinstance(event.get("source"), str)
    })
    actor_threads = {
        (event.get("actor_id"), event.get("thread_id"))
        for event in trace_artifact.get("events", [])
        if event.get("event_type") == "k11.agent_step_started"
        and isinstance(event.get("actor_id"), str)
        and isinstance(event.get("thread_id"), int)
    }
    primary_terminal_count = _primary_terminal_count(trace_artifact)
    evidence_metadata = _in_window_evidence_metadata(trace_artifact)
    try:
        analysis = analyze_trace(trace_artifact)
    except Exception as exc:
        analysis = {
            "artifact_id": "minecraft-k11-trace-analysis-draft",
            "artifact_version": 1,
            "prevalence_inference_allowed": False,
            "run_id": run_id,
            "analysis_error": str(exc),
            "analysis_error_type": type(exc).__name__,
        }
    analysis_validation = validate_p0_analysis(analysis, trace_artifact)
    (run_dir / "k11_analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = {
        "artifact_id": "minecraft-k11-p0-run-validation",
        "artifact_version": P0_VALIDATION_ARTIFACT_VERSION,
        "validation_contract": P0_VALIDATION_CONTRACT,
        "manifest_digest": manifest_digest,
        "cohort_mode": cohort_mode,
        "run_id": run_id,
        "runtime_error": error,
        "runtime_error_type": error_type,
        "trace_validation": validation,
        "generic_trace_validation": generic_validation,
        "analysis_validation": analysis_validation,
        "event_type_counts": event_counts,
        "model_call_sources": model_call_sources,
        "agent_thread_pairs": sorted([list(item) for item in actor_threads]),
        "primary_terminal_count": primary_terminal_count,
        "offline_analysis_error": analysis.get("analysis_error"),
        "runtime_returned": result is not None,
        "observation_horizon_seconds": observation_horizon_seconds,
        "structural_validation": {
            "valid": (validation.get("valid") is True
                      and analysis_validation.get("valid") is True),
            "trace_valid": validation.get("valid") is True,
            "analysis_valid": analysis_validation.get("valid") is True,
        },
        "exposure_coverage": evidence_metadata,
    }
    (run_dir / "p0_validation.json").write_text(
        json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def _worker_command(
    manifest_path: str | Path,
    output_root: Path,
    *,
    run_id: str,
    execution_revision: str,
    premanifest_path: Path,
    manifest_digest: str,
    cohort_mode: str,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "benchmarks.minecraft.k11_pilot",
        "--manifest", str(Path(manifest_path).resolve()),
        "--output-root", str(output_root),
        "--worker-run-id", run_id,
        "--execution-revision", execution_revision,
        "--premanifest", str(premanifest_path),
        "--manifest-digest", manifest_digest,
        "--cohort-mode", cohort_mode,
    ]


def _failed_process_summary(
    run_id: str, supervision: Mapping[str, Any], *, manifest_digest: str, cohort_mode: str,
) -> dict[str, Any]:
    error_type = "RunProcessTimeout" if supervision.get("timed_out") else "RunProcessFailure"
    return {
        "artifact_id": "minecraft-k11-p0-run-validation",
        "artifact_version": P0_VALIDATION_ARTIFACT_VERSION,
        "validation_contract": P0_VALIDATION_CONTRACT,
        "manifest_digest": manifest_digest,
        "cohort_mode": cohort_mode,
        "run_id": run_id,
        "runtime_error": "isolated run process did not produce a complete validation artifact",
        "runtime_error_type": error_type,
        "trace_validation": {"valid": False, "errors": [error_type], "warnings": [], "counts": {}},
        "generic_trace_validation": {"valid": False, "errors": [error_type], "warnings": [], "counts": {}},
        "analysis_validation": {"valid": False, "errors": [error_type], "counts": {}},
        "event_type_counts": {},
        "model_call_sources": [],
        "agent_thread_pairs": [],
        "primary_terminal_count": 0,
        "offline_analysis_error": error_type,
        "runtime_returned": False,
        "structural_validation": {"valid": False, "trace_valid": False, "analysis_valid": False},
        "exposure_coverage": {
            "observation_window_present": False,
            "observation_window_start_monotonic_ns": None,
            "observation_window_end_monotonic_ns": None,
            "qualifying_event_count": 0,
            "qualified": False,
        },
    }


def _apply_process_outcome(
    summary: dict[str, Any], supervision: Mapping[str, Any],
) -> dict[str, Any]:
    summary["process_supervision"] = dict(supervision)
    if summary.get("runtime_error"):
        return summary
    if supervision.get("timed_out"):
        summary["runtime_error"] = "isolated run process exceeded its wall-clock deadline"
        summary["runtime_error_type"] = "RunProcessTimeout"
    elif (
        supervision.get("exit_code") not in (0, None)
        or supervision.get("process_group_alive_after_cleanup")
        or supervision.get("post_artifact_linger")
        or supervision.get("post_parent_group_linger")
    ):
        summary["runtime_error"] = "isolated run process did not terminate cleanly"
        summary["runtime_error_type"] = "RunProcessShutdownError"
    return summary


def _validate_worker_summary_identity(
    summary: Any, *, expected_run_id: str, manifest_digest: str, cohort_mode: str,
) -> None:
    if (not isinstance(summary, Mapping)
            or summary.get("artifact_id") != "minecraft-k11-p0-run-validation"
            or summary.get("artifact_version") != P0_VALIDATION_ARTIFACT_VERSION
            or summary.get("run_id") != expected_run_id
            or summary.get("validation_contract") != P0_VALIDATION_CONTRACT
            or summary.get("manifest_digest") != manifest_digest
            or summary.get("cohort_mode") != cohort_mode):
        raise K11PilotContractError(
            "isolated run validation artifact identity does not match its parent"
        )


def _run_isolated_row(
    row: Mapping[str, Any],
    *,
    manifest_path: str | Path,
    output_root: Path,
    execution_revision: str,
    premanifest_path: Path,
    manifest_digest: str,
    cohort_mode: str,
) -> dict[str, Any]:
    run_id = row["run_id"]
    run_dir = output_root / run_id
    if run_dir.exists() and any(run_dir.iterdir()):
        raise K11PilotContractError(f"K11 P0 run directory already contains data: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    validation_path = run_dir / "p0_validation.json"
    supervision = supervise_process(
        _worker_command(
            manifest_path,
            output_root,
            run_id=run_id,
            execution_revision=execution_revision,
            premanifest_path=premanifest_path,
            manifest_digest=manifest_digest,
            cohort_mode=cohort_mode,
        ),
        cwd=ROOT,
        timeout_seconds=RUN_PROCESS_TIMEOUT_SECONDS,
        completion_grace_seconds=RUN_COMPLETION_GRACE_SECONDS,
        termination_grace_seconds=RUN_TERMINATION_GRACE_SECONDS,
        kill_grace_seconds=RUN_KILL_GRACE_SECONDS,
        artifact_ready_path=validation_path,
    )
    (run_dir / "process_supervision.json").write_text(
        json.dumps(supervision, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if validation_path.is_file():
        summary = json.loads(validation_path.read_text(encoding="utf-8"))
        _validate_worker_summary_identity(
            summary, expected_run_id=run_id,
            manifest_digest=manifest_digest, cohort_mode=cohort_mode,
        )
    else:
        summary = _failed_process_summary(
            run_id, supervision, manifest_digest=manifest_digest, cohort_mode=cohort_mode,
        )
    summary = _apply_process_outcome(summary, supervision)
    worker_shutdown_path = run_dir / "worker_shutdown.json"
    if worker_shutdown_path.is_file():
        summary["worker_shutdown"] = json.loads(worker_shutdown_path.read_text(encoding="utf-8"))
    validation_path.write_text(
        json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def run_development_smoke(
    manifest_path: str | Path, *, output_root: str | Path, run_id: str,
) -> dict[str, Any]:
    manifest = load_p0_manifest(manifest_path)
    matching_rows = [row for row in manifest["runs"] if row["run_id"] == run_id]
    if len(matching_rows) != 1:
        raise K11PilotContractError(f"development smoke run_id is not unique: {run_id}")
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    _, revision, premanifest_path, identity = _prepare_execution_identity(root)
    manifest_digest = _manifest_digest(manifest)
    summary = _run_isolated_row(
        matching_rows[0],
        manifest_path=manifest_path,
        output_root=root,
        execution_revision=revision,
        premanifest_path=premanifest_path,
        manifest_digest=manifest_digest,
        cohort_mode="development_smoke",
    )
    counts = summary.get("event_type_counts", {})
    structural_validation_passed = (
        summary.get("structural_validation", {}).get("valid") is True
    )
    runtime_qualified = summary.get("runtime_error") is None
    development_lifecycle_qualified = (
        counts.get("k11.model_call_started", 0) > 0
        and counts.get("k11.tool_call_entered", 0) > 0
        and counts.get("k11.eac_action_prepared", 0) > 0
        and summary.get("primary_terminal_count", 0) > 0
    )
    development_exposure_qualified = summary.get("exposure_coverage", {}).get(
        "qualified", False,
    ) is True
    smoke_passed = (
        runtime_qualified
        and structural_validation_passed
        and development_lifecycle_qualified
        and development_exposure_qualified
    )
    artifact = {
        "artifact_id": "minecraft-k11-development-smoke-validation",
        "artifact_version": DEVELOPMENT_SMOKE_ARTIFACT_VERSION,
        "validation_contract": P0_VALIDATION_CONTRACT,
        "manifest_digest": manifest_digest,
        "cohort_mode": "development_smoke",
        "study_phase": "K11-P0-development-smoke",
        "formal_p0": False,
        "prevalence_inference_allowed": False,
        "smoke_passed": smoke_passed,
        "runtime_qualified": runtime_qualified,
        "structural_validation_passed": structural_validation_passed,
        "development_lifecycle_qualified": development_lifecycle_qualified,
        "development_exposure_qualified": development_exposure_qualified,
        "manifest": str(Path(manifest_path).resolve()),
        "execution_revision": revision,
        "runtime_digest": identity["runtime_digest"],
        "premanifest_identity": identity["premanifest_identity"],
        "run": summary,
    }
    (root / "DEV_SMOKE_VALIDATION.json").write_text(
        json.dumps(artifact, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return artifact


def run_p0_manifest(manifest_path: str | Path, *, output_root: str | Path) -> dict[str, Any]:
    manifest = load_p0_manifest(manifest_path)
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    _, revision, premanifest_path, identity = _prepare_execution_identity(root)
    summaries = []
    manifest_digest = _manifest_digest(manifest)

    for row in manifest["runs"]:
        summaries.append(_run_isolated_row(
            row,
            manifest_path=manifest_path,
            output_root=root,
            execution_revision=revision,
            premanifest_path=premanifest_path,
            manifest_digest=manifest_digest,
            cohort_mode="formal_p0",
        ))

    calibration_error = None
    try:
        calibration = measure_inprocess_overhead(iterations=100)
    except Exception as exc:
        calibration_error = f"{type(exc).__name__}: {exc}"
        calibration = {
            "artifact_id": "minecraft-k11-p0-inprocess-overhead-calibration",
            "artifact_version": 1,
            "prevalence_inference_allowed": False,
            "calibration_error": calibration_error,
        }
    (root / "P0_CALIBRATION.json").write_text(
        json.dumps(calibration, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    aggregate_event_counts: dict[str, int] = {}
    all_actor_threads = set()
    all_model_call_sources = set()
    for summary in summaries:
        for event_type, count in summary["event_type_counts"].items():
            aggregate_event_counts[event_type] = aggregate_event_counts.get(event_type, 0) + count
        all_actor_threads.update(tuple(item) for item in summary["agent_thread_pairs"])
        all_model_call_sources.update(summary.get("model_call_sources", []))

    runtime_error_count = sum(item["runtime_error"] is not None for item in summaries)
    trace_valid_count = sum(item["trace_validation"]["valid"] is True for item in summaries)
    analysis_valid_count = sum(item["analysis_validation"]["valid"] is True for item in summaries)
    qualifying_evidence_count = sum(
        item.get("exposure_coverage", {}).get("qualifying_event_count", 0)
        for item in summaries
    )
    coverage = _coverage_summary(
        aggregate_event_counts, all_actor_threads, all_model_call_sources,
        qualifying_in_window_evidence_count=qualifying_evidence_count,
    )
    coverage_sufficient = all(coverage.values())
    p0_passed = _p0_passes(
        summaries=summaries,
        calibration_error=calibration_error,
        calibration=calibration,
        coverage_sufficient=coverage_sufficient,
    )

    aggregate = {
        "artifact_id": "minecraft-k11-p0-validation",
        "artifact_version": P0_VALIDATION_ARTIFACT_VERSION,
        "validation_contract": P0_VALIDATION_CONTRACT,
        "manifest_digest": manifest_digest,
        "cohort_mode": "formal_p0",
        "study_phase": "K11-P0-instrumentation-validation",
        "prevalence_inference_allowed": False,
        "p0_passed": p0_passed,
        "manifest": str(Path(manifest_path).resolve()),
        "execution_revision": revision,
        "runtime_digest": identity["runtime_digest"],
        "premanifest_identity": identity["premanifest_identity"],
        "premanifest_path": str(premanifest_path),
        "runtime_hygiene": manifest["runtime_hygiene"],
        "run_count": len(summaries),
        "trace_valid_count": trace_valid_count,
        "offline_analysis_valid_count": analysis_valid_count,
        "runtime_error_count": runtime_error_count,
        "aggregate_event_type_counts": aggregate_event_counts,
        "coverage": coverage,
        "model_call_sources": sorted(all_model_call_sources),
        "model_call_coverage_note": (
            "Observed events demonstrate exercised paths only; structural coverage is established "
            "by the K11 instrumentation contract tests."
        ),
        "coverage_sufficient": coverage_sufficient,
        "exposure_coverage": {
            "qualifying_event_count": qualifying_evidence_count,
            "qualified": qualifying_evidence_count > 0,
        },
        "calibration_error": calibration_error,
        "runs": summaries,
    }
    (root / "P0_VALIDATION.json").write_text(
        json.dumps(aggregate, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return aggregate


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run K11 P0 instrumentation validation")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-root", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--worker-run-id")
    mode.add_argument("--development-smoke-run-id")
    mode.add_argument("--formal-p0", action="store_true")
    parser.add_argument("--execution-revision")
    parser.add_argument("--premanifest")
    parser.add_argument("--manifest-digest")
    parser.add_argument("--cohort-mode", choices=sorted(COHORT_MODES))
    args = parser.parse_args(argv)
    if args.worker_run_id:
        if (not args.execution_revision or not args.premanifest or not args.manifest_digest
                or not args.cohort_mode):
            parser.error(
                "worker mode requires --execution-revision, --premanifest, --manifest-digest, "
                "and --cohort-mode"
            )
        manifest = load_p0_manifest(args.manifest)
        if _manifest_digest(manifest) != args.manifest_digest:
            raise K11PilotContractError("worker manifest differs from the parent-validated snapshot")
        matching_rows = [row for row in manifest["runs"] if row["run_id"] == args.worker_run_id]
        if len(matching_rows) != 1:
            raise K11PilotContractError(
                f"worker run_id must identify exactly one manifest row: {args.worker_run_id}"
            )
        execution = RuntimeExecution.resolve(ROOT)
        premanifest_path = Path(args.premanifest).resolve()
        verify_eac_premanifest(
            premanifest_path,
            execution=execution,
            execution_revision=args.execution_revision,
        )
        run_dir = Path(args.output_root).resolve() / args.worker_run_id
        try:
            _run_single_row(
                matching_rows[0],
                run_dir,
                execution=execution,
                execution_revision=args.execution_revision,
                premanifest_path=premanifest_path,
                observation_horizon_seconds=manifest["observation_window"]["horizon_seconds"],
                manifest_digest=args.manifest_digest,
                cohort_mode=args.cohort_mode,
            )
        finally:
            cleanup = cleanup_process_group_descendants(
                termination_grace_seconds=3.0,
                kill_grace_seconds=3.0,
            )
            (run_dir / "worker_shutdown.json").write_text(
                json.dumps(cleanup, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        return 0
    if args.development_smoke_run_id:
        smoke = run_development_smoke(
            args.manifest,
            output_root=args.output_root,
            run_id=args.development_smoke_run_id,
        )
        print(json.dumps(smoke, ensure_ascii=True, indent=2, sort_keys=True))
        return 0 if smoke["smoke_passed"] is True else 2
    if not args.formal_p0:
        parser.error("select --development-smoke-run-id or explicitly select --formal-p0")
    aggregate = run_p0_manifest(args.manifest, output_root=args.output_root)
    print(json.dumps(aggregate, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if aggregate["p0_passed"] is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
