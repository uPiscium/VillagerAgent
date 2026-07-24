from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

from benchmarks.common.run_artifacts import finalize_run_directory, prepare_run_directory, validate_run_attempt
from benchmarks.experiment_provenance import finalize_provenance, write_provenance


def build_evidence_bundle(*, evidence_dir: Path, output_dir: Path, overwrite: bool = False) -> Path:
    attempt_id = prepare_run_directory(
        output_dir, producer="benchmarks.tdw_mat.evidence", overwrite=overwrite
    )
    comparison = _read_json(evidence_dir / "fixture_comparison.json")
    smoke = _read_json(evidence_dir / "fixture_smoke.json")
    preflight = _read_json(evidence_dir / "real_preflight.json")
    for name in ("fixture_comparison.json", "fixture_smoke.json", "real_preflight.json"):
        shutil.copy2(evidence_dir / name, output_dir / name)

    resolved = {
        "schema_version": 1,
        "benchmark": "tdw_mat",
        "bundle_id": "tdw-mat-issue372-fixture-policy-smoke-v1",
        "classification": "integration_validation",
        "performance_claim": False,
        "attempt_id": attempt_id,
    }
    _write_json(output_dir / "config.resolved.json", resolved)
    _write_json(
        output_dir / "summary.json",
        {
            **resolved,
            "status": "completed",
            "fixture_smoke_passed": bool(smoke["metrics"]["task_success"]),
            "comparison_episode_count": len(comparison["episodes"]),
            "real_preflight_ready": bool(preflight["ready"]),
            "real_preflight_missing": preflight["missing"],
            "limitations": comparison["limitations"],
        },
    )
    fields = [
        "condition", "episodes", "task_success_rate", "mean_transport_rate",
        "mean_episode_steps", "mean_communication_count", "mean_communication_utility",
        "feasibility_prediction_precision", "feasibility_prediction_recall",
        "false_feasible_action_rate", "false_infeasible_action_rate",
        "recovery_after_failure_rate", "mean_action_throughput",
        "mean_information_action_to_progress_latency",
        "mean_transport_rate_delta_vs_disabled", "mean_step_delta_vs_disabled",
    ]
    with (output_dir / "metrics.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for condition, metrics in comparison["condition_summary"].items():
            writer.writerow({"condition": condition, **metrics})
    write_provenance(
        output_dir,
        benchmark="tdw_mat",
        command=[sys.executable, "-m", "benchmarks.tdw_mat.evidence_bundle"],
        resolved_config=resolved,
        environment_notes="fixture_policy_smoke=true; real_simulator=false; issue=372",
    )
    finalize_provenance(output_dir, status="success")
    finalize_run_directory(
        output_dir,
        attempt_id=attempt_id,
        producer="benchmarks.tdw_mat.evidence",
        status="completed",
        stamp_nested=False,
    )
    validate_run_attempt(output_dir, attempt_id=attempt_id)
    return output_dir


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build managed TDW-MAT Issue 372 evidence.")
    parser.add_argument("--evidence-dir", default="docs/benchmarks/evidence/tdw_mat_issue_372")
    parser.add_argument("--output", required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    output = build_evidence_bundle(
        evidence_dir=Path(args.evidence_dir), output_dir=Path(args.output), overwrite=args.overwrite
    )
    print(json.dumps({"output": str(output), "passed": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
