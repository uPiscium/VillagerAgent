import json

from benchmarks.common.publish_bundle import validate_public_bundle
from benchmarks.common.run_artifacts import finalize_run_directory, prepare_run_directory
from benchmarks.craft import comparison_bundle
from benchmarks.experiment_provenance import finalize_provenance, write_provenance


def test_build_bundle_sanitizes_and_accounts_for_source_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(comparison_bundle, "repo_root", lambda: tmp_path)
    source = tmp_path / "source"
    source_attempt = prepare_run_directory(source, producer="benchmarks.craft.run")
    (source / "config.resolved.json").write_text("{}", encoding="utf-8")
    (source / "summary.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    (source / "metrics.csv").write_text("metric,value\nprogress,1\n", encoding="utf-8")
    write_provenance(source, benchmark="craft", command=["test"], resolved_config={})
    finalize_provenance(source, status="success")
    finalize_run_directory(
        source,
        attempt_id=source_attempt,
        producer="benchmarks.craft.run",
        status="completed",
    )
    failed_source = tmp_path / "failed-source"
    failed_attempt = prepare_run_directory(failed_source, producer="benchmarks.craft.run")
    (failed_source / "config.resolved.json").write_text("{}", encoding="utf-8")
    (failed_source / "summary.json").write_text(json.dumps({"status": "failed"}), encoding="utf-8")
    (failed_source / "metrics.csv").write_text("metric,value\n", encoding="utf-8")
    write_provenance(failed_source, benchmark="craft", command=["test"], resolved_config={})
    finalize_provenance(failed_source, status="failure")
    finalize_run_directory(
        failed_source,
        attempt_id=failed_attempt,
        producer="benchmarks.craft.run",
        status="failed",
    )

    source_manifest = source / "artifact_manifest.json"
    comparison_input = tmp_path / "input.json"
    comparison_input.write_text(json.dumps({
        "observations": [{"run_manifest": str(source_manifest)}],
    }), encoding="utf-8")
    comparison_report = tmp_path / "report.json"
    comparison_report.write_text(json.dumps({
        "comparison_id": "comparison-1",
        "pairing": {"matched_pair_count": 1, "excluded_pair_count": 0},
        "effect": {"estimate": 0.0, "confidence_interval": {"lower": -1.0, "upper": 1.0}},
        "granted_claim": "integration_validation",
        "claim_gates": {"performance_claim": {"eligible": False}},
    }), encoding="utf-8")
    diagnostics = tmp_path / "diagnostics.json"
    diagnostics.write_text(json.dumps({
        "run_manifest": "result/craft/source/artifact_manifest.json",
    }), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "schema_version": "1.0.0",
        "bundle_id": "comparison-bundle",
        "benchmark": "craft",
        "classification": "integration_validation",
        "additional_run_manifests": [str(failed_source / "artifact_manifest.json")],
        "artifacts": [
            {
                "kind": "comparison_input",
                "source": str(comparison_input),
                "destination": "input.json",
            },
            {
                "kind": "comparison_report",
                "source": str(comparison_report),
                "destination": "report.json",
            },
            {
                "kind": "diagnostics",
                "source": str(diagnostics),
                "destination": "diagnostics.json",
            },
        ],
    }), encoding="utf-8")

    output = tmp_path / "bundle"
    comparison_bundle.build_bundle(config, output)

    validation = validate_public_bundle(output)
    assert validation.run_statuses == {"completed": 1, "failed": 1}
    assert (output / "runs/source/publication_source.json").exists()
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["source_run_count"] == 2
    assert summary["source_run_statuses"] == {"completed": 1, "failed": 1}
    assert summary["performance_claim_eligible"] is False
    provenance = json.loads((output / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["environment_notes"] == (
        "sanitized_derivative=true; bundle_id=comparison-bundle; full_comparison=true"
    )
    assert str(tmp_path) not in (output / "command.txt").read_text(encoding="utf-8")
    assert str(tmp_path) not in (output / "provenance.json").read_text(encoding="utf-8")
    public_input = json.loads((output / "evidence/input.json").read_text(encoding="utf-8"))
    assert public_input["observations"][0]["run_manifest"] == "runs/source/artifact_manifest.json"
    public_diagnostics = json.loads((output / "evidence/diagnostics.json").read_text(encoding="utf-8"))
    assert public_diagnostics["run_manifest"] == "runs/source/artifact_manifest.json"


def test_public_manifest_references_are_self_contained():
    payload = {
        "run_manifest": "result/craft/run-1/artifact_manifest.json",
        "unrelated": "result/craft/run-1/summary.json",
    }

    rewritten = comparison_bundle._public_manifest_references(payload)

    assert rewritten["run_manifest"] == "runs/run-1/artifact_manifest.json"
    assert rewritten["unrelated"] == payload["unrelated"]
