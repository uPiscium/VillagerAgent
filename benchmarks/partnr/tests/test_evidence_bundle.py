import json
from pathlib import Path

from benchmarks.common.run_artifacts import read_attempt_id, validate_run_attempt
from benchmarks.partnr.evidence_bundle import build_evidence_bundle


def test_evidence_bundle_is_managed_and_reports_official_accounting(tmp_path):
    output = tmp_path / "bundle"
    build_evidence_bundle(
        evidence_dir=Path("docs/benchmarks/evidence/partnr_issue_378"),
        output_dir=output,
    )

    validate_run_attempt(output, attempt_id=read_attempt_id(output))
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["fixture_smoke_passed"] is True
    assert summary["real_preflight_ready"] is True
    assert summary["step_zero_successful_episode_ids"] == ["0"]
    assert summary["bounded_successful_episode_ids"] == ["0", "1", "2", "3"]
    assert summary["bounded_failed_episode_ids"] == []
    assert summary["bounded_missing_episode_ids"] == []
    assert (output / "metrics.csv").read_text(encoding="utf-8").count("\n") == 5
