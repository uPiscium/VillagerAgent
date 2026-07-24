import json
from pathlib import Path

from benchmarks.common.run_artifacts import read_attempt_id, validate_run_attempt
from benchmarks.tdw_mat.evidence_bundle import build_evidence_bundle


def test_evidence_bundle_is_managed_and_reports_real_blocker(tmp_path):
    output = tmp_path / "bundle"
    build_evidence_bundle(
        evidence_dir=Path("docs/benchmarks/evidence/tdw_mat_issue_372"), output_dir=output
    )

    validate_run_attempt(output, attempt_id=read_attempt_id(output))
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["fixture_smoke_passed"] is True
    assert summary["comparison_episode_count"] == 16
    assert summary["real_preflight_ready"] is False
    assert summary["real_preflight_missing"] == [
        "python_gym_package", "python_tdw_package", "display"
    ]
    assert (output / "metrics.csv").read_text(encoding="utf-8").count("\n") == 5
