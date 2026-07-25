from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

from benchmarks.common.run_artifacts import (
    finalize_run_directory,
    prepare_run_directory,
    validate_run_attempt,
)
from benchmarks.experiment_provenance import finalize_provenance, write_provenance


BUNDLE_ID = "partnr-issue378-bounded-oracle-smoke-v1"


def build_evidence_bundle(*, evidence_dir: Path, output_dir: Path, overwrite: bool = False) -> Path:
    attempt_id = prepare_run_directory(
        output_dir, producer="benchmarks.partnr.evidence", overwrite=overwrite
    )
    fixture = _read_json(evidence_dir / "fixture_smoke.json")
    preflight = _read_json(evidence_dir / "real_preflight.json")
    gates = _read_json(evidence_dir / "official_gates.json")
    for name in ("fixture_smoke.json", "real_preflight.json", "official_gates.json"):
        shutil.copy2(evidence_dir / name, output_dir / name)

    resolved = {
        "schema_version": 1,
        "benchmark": "partnr",
        "bundle_id": BUNDLE_ID,
        "classification": "integration_validation",
        "performance_claim": False,
        "attempt_id": attempt_id,
    }
    _write_json(output_dir / "config.resolved.json", resolved)
    bounded = gates["bounded_smoke"]
    _write_json(
        output_dir / "summary.json",
        {
            **resolved,
            "status": "completed",
            "fixture_smoke_passed": bool(fixture["metrics"]["task_success"]),
            "real_preflight_ready": bool(preflight["ready"]),
            "step_zero_successful_episode_ids": gates["step_zero"]["successful_episode_ids"],
            "bounded_successful_episode_ids": bounded["successful_episode_ids"],
            "bounded_failed_episode_ids": bounded["failed_episode_ids"],
            "bounded_missing_episode_ids": bounded["missing_episode_ids"],
            "limitations": gates["limitations"],
        },
    )
    with (output_dir / "metrics.csv").open("w", encoding="utf-8", newline="") as handle:
        fields = [
            "episode_id",
            "success",
            "task_percent_complete",
            "task_state_success",
            "runtime",
            "sim_step_count",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(bounded["records"])
    write_provenance(
        output_dir,
        benchmark="partnr",
        command=[sys.executable, "-m", "benchmarks.partnr.evidence_bundle"],
        resolved_config=resolved,
        environment_notes="fixture=true; official_step_zero=true; bounded_oracle_episodes=4; issue=378",
    )
    finalize_provenance(output_dir, status="success")
    finalize_run_directory(
        output_dir,
        attempt_id=attempt_id,
        producer="benchmarks.partnr.evidence",
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
    parser = argparse.ArgumentParser(description="Build managed PARTNR Issue 378 evidence.")
    parser.add_argument("--evidence-dir", default="docs/benchmarks/evidence/partnr_issue_378")
    parser.add_argument("--output", required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    output = build_evidence_bundle(
        evidence_dir=Path(args.evidence_dir),
        output_dir=Path(args.output),
        overwrite=args.overwrite,
    )
    print(json.dumps({"output": str(output), "passed": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
