"""K11 P0 instrumentation-validation runner.

P0 is a development pilot only.  This runner intentionally rejects anything
other than the eight-run Advisory/non-judged natural cohort described by the K11
protocol draft.  It never injects semantic changes or synchronization delays.
"""
from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path
from typing import Any, Mapping

from benchmarks.minecraft.k11_analysis import analyze_trace
from benchmarks.minecraft.k11_trace import (
    K11ProcessInstrumentation,
    K11TraceRecorder,
    validate_trace,
)
from env.runtime_paths import RuntimePaths
from start_with_config import run as run_villageragent


P0_MANIFEST_ID = "minecraft-k11-p0-manifest"
P0_MANIFEST_VERSION = 1
P0_EXPECTED_RUNS = 8
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


def _load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise K11PilotContractError("K11 P0 manifest must be a JSON object")
    return value


def load_p0_manifest(path: str | Path) -> dict[str, Any]:
    document = _load_json(path)
    if document.get("artifact_id") != P0_MANIFEST_ID or document.get("artifact_version") != P0_MANIFEST_VERSION:
        raise K11PilotContractError("K11 P0 manifest identity mismatch")
    if document.get("study_phase") != "K11-P0-instrumentation-validation":
        raise K11PilotContractError("K11 P0 study phase mismatch")
    if document.get("prevalence_inference_allowed") is not False:
        raise K11PilotContractError("P0 must explicitly forbid prevalence inference")
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
    dual = config.get("minecraft_dual_dag_config")
    if not isinstance(dual, Mapping):
        raise K11PilotContractError("K11 P0 requires minecraft_dual_dag_config")
    if dual.get("eac_mode") != "dual_dag_advisory":
        raise K11PilotContractError("K11 P0 primary cohort must use dual_dag_advisory")
    if dual.get("judged_execution") is not False or dual.get("production") is not False:
        raise K11PilotContractError("K11 P0 must preserve explicit non-judged/non-production EAC admission")
    if not dual.get("eac_premanifest") or not dual.get("eac_execution_revision"):
        raise K11PilotContractError("K11 P0 requires explicit EAC premanifest and execution revision")
    for key in (
        "api_model", "api_base", "task_idx", "agent_num", "dig_needed", "max_task_num",
        "task_goal", "host", "port", "task_name",
    ):
        if key not in config:
            raise K11PilotContractError(f"K11 P0 runtime is missing {key}")


def _runtime_kwargs(row: Mapping[str, Any], run_dir: Path) -> dict[str, Any]:
    config = dict(row["runtime"])
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
    config.setdefault("runtime_execution", None)
    return config


def run_p0_manifest(manifest_path: str | Path, *, output_root: str | Path) -> dict[str, Any]:
    manifest = load_p0_manifest(manifest_path)
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    summaries = []

    for row in manifest["runs"]:
        run_id = row["run_id"]
        run_dir = root / run_id
        if run_dir.exists() and any(run_dir.iterdir()):
            raise K11PilotContractError(f"K11 P0 run directory already contains data: {run_dir}")
        run_dir.mkdir(parents=True, exist_ok=True)
        trace = K11TraceRecorder(run_id)
        error = None
        error_type = None
        result = None
        try:
            with K11ProcessInstrumentation(trace):
                result = run_villageragent(**_runtime_kwargs(row, run_dir))
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
        validation = validate_trace(trace_artifact)
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
        (run_dir / "k11_analysis.json").write_text(
            json.dumps(analysis, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        summary = {
            "run_id": run_id,
            "runtime_error": error,
            "runtime_error_type": error_type,
            "trace_validation": validation,
            "offline_analysis_error": analysis.get("analysis_error"),
            "runtime_returned": result is not None,
        }
        (run_dir / "p0_validation.json").write_text(
            json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        summaries.append(summary)

    aggregate = {
        "artifact_id": "minecraft-k11-p0-validation",
        "artifact_version": 1,
        "study_phase": "K11-P0-instrumentation-validation",
        "prevalence_inference_allowed": False,
        "manifest": str(Path(manifest_path)),
        "run_count": len(summaries),
        "trace_valid_count": sum(item["trace_validation"]["valid"] is True for item in summaries),
        "offline_analysis_valid_count": sum(item["offline_analysis_error"] is None for item in summaries),
        "runtime_error_count": sum(item["runtime_error"] is not None for item in summaries),
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
    args = parser.parse_args(argv)
    aggregate = run_p0_manifest(args.manifest, output_root=args.output_root)
    print(json.dumps(aggregate, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if (
        aggregate["trace_valid_count"] == aggregate["run_count"]
        and aggregate["offline_analysis_valid_count"] == aggregate["run_count"]
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
