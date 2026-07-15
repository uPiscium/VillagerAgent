import json

import pytest

from benchmarks.common import analysis as analysis_module
from benchmarks.common.analysis import ComparisonContractError, analyze_comparison, main


def test_paired_bootstrap_is_deterministic_and_order_independent():
    payload = _payload("cwah", values=[(0, 0, 0.1, 0.4), (1, 0, 0.2, 0.3), (0, 1, 0.3, 0.7)])

    first = analyze_comparison(payload)
    payload["observations"].reverse()
    second = analyze_comparison(payload)

    assert first["effect"] == second["effect"]
    assert first["effect"]["estimate"] == pytest.approx(0.26666666666666666)
    interval = first["effect"]["confidence_interval"]
    assert interval["method"] == "paired_two_way_cluster_percentile_bootstrap"
    assert interval["cluster_factors"] == ["comparison_unit", "seed"]
    assert interval["analysis_seed"] == 17
    assert interval["lower"] == pytest.approx(0.1)
    assert interval["upper"] == pytest.approx(0.4)
    assert first["pairing"]["matched_pair_count"] == 3


def test_failed_missing_and_unmatched_runs_are_reported_and_gate_performance():
    payload = _payload("cwah", values=[(0, 0, 0.1, 0.4)])
    payload["evidence"].update({"requested_claim": "performance_claim", "execution_scope": "full"})
    payload["observations"].extend([
        _observation("old", {"task_id": 1, "seed": 0}, None, status="failed"),
        _observation("new", {"task_id": 1, "seed": 0}, 0.8),
        _observation("old", {"task_id": 2, "seed": 0}, None, status="missing", manifest=None),
        _observation("new", {"task_id": 2, "seed": 0}, None),
        _observation("new", {"task_id": 3, "seed": 0}, 0.9),
    ])

    report = analyze_comparison(payload)

    assert report["sample_counts"]["old"] == {
        "expected": 4,
        "observed": 3,
        "completed": 1,
        "metric_available": 1,
        "failed": 1,
        "missing": 2,
    }
    assert report["sample_counts"]["new"]["missing"] == 1
    assert [item["reason"] for item in report["pairing"]["excluded_pairs"]] == [
        "failed_run",
        "missing_metric",
        "missing_baseline_observation",
    ]
    gate = report["claim_gates"]["performance_claim"]
    assert gate["eligible"] is False
    assert "failed_missing_or_unmatched_observations" in gate["reasons"]
    assert report["granted_claim"] == "integration_validation"


def test_performance_gate_accepts_replicated_prespecified_primary_comparison():
    payload = _payload(
        "cwah",
        values=[
            (0, 0, 0.1, 0.5),
            (1, 0, 0.2, 0.6),
            (2, 0, 0.1, 0.4),
            (0, 1, 0.2, 0.7),
            (1, 1, 0.1, 0.6),
            (2, 1, 0.3, 0.7),
        ],
    )
    payload["evidence"].update({"requested_claim": "performance_claim", "execution_scope": "full"})

    report = analyze_comparison(payload)

    assert report["claim_gates"]["performance_claim"]["eligible"] is True
    assert report["granted_claim"] == "performance_claim"
    assert report["effect"]["confidence_interval"]["lower"] > 0


def test_performance_gate_rejects_incomplete_crossed_matrix():
    payload = _payload(
        "cwah",
        values=[
            (0, 0, 0.0, 1.0),
            (1, 0, 0.0, 1.0),
            (2, 0, 0.0, 1.0),
            (0, 1, 0.0, 1.0),
            (1, 1, 0.0, 1.0),
        ],
    )
    payload["evidence"].update({"requested_claim": "performance_claim", "execution_scope": "full"})

    report = analyze_comparison(payload)

    assert "incomplete_crossed_matrix" in report["claim_gates"]["performance_claim"]["reasons"]
    assert report["granted_claim"] == "integration_validation"


def test_performance_gate_requires_favorable_observed_effect(monkeypatch):
    payload = _payload(
        "cwah",
        values=[(unit, seed, 0.0, -1.0) for seed in (0, 1) for unit in range(3)],
    )
    payload["evidence"].update({"requested_claim": "performance_claim", "execution_scope": "full"})
    monkeypatch.setattr(analysis_module, "_bootstrap_interval", lambda *args, **kwargs: (0.1, 0.2))

    report = analyze_comparison(payload)

    assert report["effect"]["estimate"] == -1.0
    assert "observed_effect_not_favorable" in report["claim_gates"]["performance_claim"]["reasons"]
    assert report["granted_claim"] == "integration_validation"


@pytest.mark.parametrize("scope", ["smoke", "connectivity"])
def test_smoke_and_connectivity_are_diagnostic_only(scope):
    payload = _payload("cwah", values=[(0, 0, 0.1, 0.5)])
    payload["evidence"].update({"requested_claim": "performance_claim", "execution_scope": scope})

    report = analyze_comparison(payload)

    assert report["granted_claim"] == "diagnostic"
    assert report["claim_gates"]["integration_validation"]["eligible"] is False
    assert "smoke_or_connectivity_only" in report["claim_gates"]["performance_claim"]["reasons"]


def test_exploratory_metric_cannot_support_performance_claim():
    payload = _payload("cwah", values=[(index, seed, 5, 2) for seed in (0, 1) for index in range(3)])
    payload["metric"] = {"name": "episode_steps", "role": "exploratory", "higher_is_better": False}
    payload["evidence"].update({"requested_claim": "performance_claim", "execution_scope": "full"})

    report = analyze_comparison(payload)

    assert report["metric"]["role"] == "exploratory"
    assert "exploratory_metric" in report["claim_gates"]["performance_claim"]["reasons"]
    assert report["granted_claim"] == "integration_validation"


def test_bonferroni_adjusts_bootstrap_interval_for_comparison_family():
    payload = _payload("cwah", values=[(index, seed, 0.0, float(index + seed + 1)) for seed in (0, 1) for index in range(3)])
    payload["evidence"]["multiple_comparisons"] = {
        "family_id": "policy-sweep",
        "family_size": 4,
        "method": "bonferroni",
    }

    report = analyze_comparison(payload)

    interval = report["effect"]["confidence_interval"]
    assert interval["nominal_confidence_level"] == 0.95
    assert interval["adjusted_confidence_level"] == pytest.approx(0.9875)
    assert report["multiple_comparisons"]["family_id"] == "policy-sweep"


def test_rejects_undeclared_multiple_comparisons():
    payload = _payload("cwah", values=[(0, 0, 0.0, 1.0)])
    payload["evidence"]["multiple_comparisons"]["family_size"] = 2

    with pytest.raises(ComparisonContractError, match="require method='bonferroni'"):
        analyze_comparison(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("bootstrap_samples", 1, "at least 10000"),
        ("bootstrap_samples", 9999, "at least 10000"),
        ("confidence_level", 0.01, "must be 0.95"),
        ("confidence_level", 0.99, "must be 0.95"),
    ],
)
def test_rejects_weak_or_caller_selected_uncertainty_settings(field, value, message):
    payload = _payload("cwah", values=[(0, 0, 0.0, 1.0)])
    payload["analysis"][field] = value

    with pytest.raises(ComparisonContractError, match=message):
        analyze_comparison(payload)


@pytest.mark.parametrize(
    "invalid_id",
    [True, False, 1.0, float("nan"), float("inf"), float("-inf"), ""],
)
def test_rejects_pairing_identifiers_that_can_collide_or_are_non_finite(invalid_id):
    payload = _payload("cwah", values=[(0, 0, 0.0, 1.0)])
    payload["observations"][0]["pairing"]["task_id"] = invalid_id

    with pytest.raises(ComparisonContractError, match="non-empty string or integer"):
        analyze_comparison(payload)


def test_string_and_integer_pairing_identifiers_remain_distinct():
    payload = _payload("cwah", values=[])
    payload["observations"] = [
        _observation("old", {"task_id": 1, "seed": 0}, 0.0),
        _observation("new", {"task_id": "1", "seed": 0}, 1.0),
    ]

    report = analyze_comparison(payload)

    assert report["pairing"]["matched_pair_count"] == 0
    assert report["pairing"]["excluded_pair_count"] == 2


@pytest.mark.parametrize(
    ("benchmark", "wrong_metric"),
    [("craft", "normalized_progress"), ("cwah", "final_progress"), ("minecraft", "final_progress")],
)
def test_primary_metrics_cannot_be_mixed_across_benchmarks(benchmark, wrong_metric):
    payload = _payload(benchmark, values=[])
    payload["observations"] = [
        _observation("old", _pairing(benchmark, 0, 0), 0.1),
        _observation("new", _pairing(benchmark, 0, 0), 0.2),
    ]
    payload["metric"]["name"] = wrong_metric

    with pytest.raises(ComparisonContractError, match=f"Primary metric for {benchmark}"):
        analyze_comparison(payload)


def test_primary_metric_direction_is_fixed():
    payload = _payload("minecraft", values=[(0, 0, 0.0, 1.0)])
    payload["metric"]["higher_is_better"] = False

    with pytest.raises(ComparisonContractError, match="higher_is_better=true"):
        analyze_comparison(payload)


def test_cli_writes_versioned_machine_readable_report(tmp_path, capsys):
    payload = _payload("craft", values=[])
    payload["observations"] = [
        _observation("old", {"structure_id": 4, "seed": 2}, 0.2),
        _observation("new", {"structure_id": 4, "seed": 2}, 0.4),
    ]
    input_path = tmp_path / "comparison.json"
    output_path = tmp_path / "report.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    assert main([str(input_path), "--output", str(output_path)]) == 0

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == "1.0.0"
    assert report["metric"]["name"] == "final_progress"
    assert report["run_manifests"]["old"] == ["manifests/old-4-2.json"]
    assert "Wrote paired benchmark analysis" in capsys.readouterr().out


def _payload(benchmark, *, values):
    contracts = {
        "craft": ("final_progress", ["structure_id", "seed"]),
        "cwah": ("normalized_progress", ["task_id", "seed"]),
        "minecraft": ("task_completion_rate", ["task_id", "seed", "world_id"]),
    }
    metric, pairing_keys = contracts[benchmark]
    observations = []
    for unit, seed, baseline, candidate in values:
        pairing = _pairing(benchmark, unit, seed)
        observations.extend([
            _observation("old", pairing, baseline),
            _observation("new", pairing, candidate),
        ])
    return {
        "schema_version": "1.0.0",
        "comparison_id": "candidate-vs-baseline",
        "benchmark": benchmark,
        "conditions": {"baseline": "old", "candidate": "new"},
        "metric": {"name": metric, "role": "primary", "higher_is_better": True},
        "pairing_keys": pairing_keys,
        "observations": observations,
        "analysis": {"seed": 17, "bootstrap_samples": 10000, "confidence_level": 0.95},
        "evidence": {
            "requested_claim": "integration_validation",
            "execution_scope": "bounded",
            "prespecified": True,
            "multiple_comparisons": {"family_id": "primary", "family_size": 1, "method": "none"},
        },
    }


def _pairing(benchmark, unit, seed):
    if benchmark == "craft":
        return {"structure_id": unit, "seed": seed}
    if benchmark == "cwah":
        return {"task_id": unit, "seed": seed}
    return {"task_id": unit, "seed": seed, "world_id": "world-a"}


def _observation(condition, pairing, value, *, status="completed", manifest="default"):
    unit = pairing.get("structure_id", pairing.get("task_id"))
    seed = pairing["seed"]
    return {
        "condition": condition,
        "pairing": dict(pairing),
        "status": status,
        "metric_value": value,
        "run_manifest": f"manifests/{condition}-{unit}-{seed}.json" if manifest else None,
    }
