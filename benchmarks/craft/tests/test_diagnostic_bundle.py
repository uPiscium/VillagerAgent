import json

import pytest

from benchmarks.common.run_artifacts import read_attempt_id, validate_run_attempt
from benchmarks.craft.diagnostic_bundle import DiagnosticBundleError, build_bundle


def test_build_bundle_writes_managed_diagnostic_evidence(tmp_path):
    comparison = tmp_path / "comparison.json"
    comparison.write_text(json.dumps({
        "comparison_id": "comparison-1",
        "pairing": {"matched_pair_count": 1},
        "granted_claim": "diagnostic",
    }), encoding="utf-8")
    probe = tmp_path / "probe.json"
    probe.write_text(json.dumps({
        "probe_id": "probe-1",
        "classification": "diagnostic",
        "retrieval": {
            "retrieved_node_count": 1,
            "retrieval_used_in_top_action_count": 1,
            "retrieval_changed_top_action_count": 1,
        },
    }), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps(_config(comparison, probe)), encoding="utf-8")
    output = tmp_path / "bundle"

    build_bundle(config, output)

    manifest = validate_run_attempt(output, attempt_id=read_attempt_id(output))
    assert manifest["producer"] == "benchmarks.craft.diagnostic_bundle"
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["classification"] == "diagnostic"
    assert summary["performance_claim_eligible"] is False
    assert summary["comparison_report_count"] == 1
    assert summary["retrieval_probe_count"] == 1
    assert "retrieval_changed_top_action_count" in (output / "metrics.csv").read_text(encoding="utf-8")


def test_build_bundle_rejects_unsafe_destination(tmp_path):
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}", encoding="utf-8")
    config = _config(evidence, evidence)
    config["artifacts"][0]["destination"] = "../outside.json"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(DiagnosticBundleError, match="Unsafe"):
        build_bundle(config_path, tmp_path / "bundle")


def _config(comparison, probe):
    checksum = "a" * 64
    return {
        "schema_version": "1.0.0",
        "bundle_id": "test-diagnostic",
        "benchmark": "craft",
        "classification": "diagnostic",
        "artifacts": [
            {
                "kind": "comparison_report",
                "source": str(comparison),
                "destination": "analysis/comparison.json",
            },
            {
                "kind": "retrieval_probe_output",
                "source": str(probe),
                "destination": "retrieval/probe.json",
            },
        ],
        "source_releases": [{
            "condition": "v0",
            "archive_url": "https://example.test/archive.zip",
            "metadata_url": "https://example.test/archive.zip.metadata.json",
            "archive_sha256": checksum,
            "manifest_sha256": checksum,
        }],
    }
