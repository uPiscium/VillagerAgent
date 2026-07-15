from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0.0"
MIN_PERFORMANCE_PAIRS = 5
MIN_BOOTSTRAP_SAMPLES = 10_000
REQUIRED_CONFIDENCE_LEVEL = 0.95

BENCHMARK_CONTRACTS = {
    "craft": {
        "primary_metric": "final_progress",
        "pairing_keys": ("structure_id", "seed"),
        "comparison_unit": "structure",
    },
    "cwah": {
        "primary_metric": "normalized_progress",
        "pairing_keys": ("task_id", "seed"),
        "comparison_unit": "task episode",
    },
    "minecraft": {
        "primary_metric": "task_completion_rate",
        "pairing_keys": ("task_id", "seed", "world_id"),
        "comparison_unit": "judged task run",
    },
}

CLAIM_LEVELS = ("diagnostic", "integration_validation", "performance_claim")
EXECUTION_SCOPES = ("smoke", "connectivity", "bounded", "full")
OBSERVATION_STATUSES = ("completed", "failed", "missing")


class ComparisonContractError(ValueError):
    """Raised when a paired-comparison contract is invalid."""


def analyze_comparison(payload: dict[str, Any]) -> dict[str, Any]:
    validated = _validate_payload(payload)
    benchmark = validated["benchmark"]
    conditions = validated["conditions"]
    pairing_keys = validated["pairing_keys"]
    observations = validated["observations"]
    analysis = validated["analysis"]
    evidence = validated["evidence"]

    by_condition: dict[str, dict[tuple[Any, ...], dict[str, Any]]] = {
        conditions["baseline"]: {},
        conditions["candidate"]: {},
    }
    for observation in observations:
        key = tuple(observation["pairing"][name] for name in pairing_keys)
        condition_rows = by_condition[observation["condition"]]
        if key in condition_rows:
            raise ComparisonContractError(
                f"Duplicate observation for condition {observation['condition']!r} and pairing key {key!r}."
            )
        condition_rows[key] = observation

    all_keys = sorted(
        set(by_condition[conditions["baseline"]]) | set(by_condition[conditions["candidate"]]),
        key=_stable_key,
    )
    paired_differences: list[float] = []
    matched_keys: list[dict[str, Any]] = []
    excluded_pairs: list[dict[str, Any]] = []
    for key in all_keys:
        baseline = by_condition[conditions["baseline"]].get(key)
        candidate = by_condition[conditions["candidate"]].get(key)
        reason = _pair_exclusion_reason(baseline, candidate)
        pairing = dict(zip(pairing_keys, key))
        if reason is not None:
            excluded_pairs.append({
                "pairing": pairing,
                "reason": reason,
                "baseline_status": _effective_status(baseline),
                "candidate_status": _effective_status(candidate),
            })
            continue
        baseline_value = float(baseline["metric_value"])
        candidate_value = float(candidate["metric_value"])
        paired_differences.append(candidate_value - baseline_value)
        matched_keys.append(pairing)

    multiplicity = evidence["multiple_comparisons"]
    nominal_confidence = analysis["confidence_level"]
    adjusted_confidence = _adjusted_confidence_level(
        nominal_confidence,
        method=multiplicity["method"],
        family_size=multiplicity["family_size"],
    )
    estimate = _mean(paired_differences) if paired_differences else None
    interval = _bootstrap_interval(
        paired_differences,
        matched_keys=matched_keys,
        pairing_keys=pairing_keys,
        seed=analysis["seed"],
        samples=analysis["bootstrap_samples"],
        confidence_level=adjusted_confidence,
    )
    counts = {
        condition: _condition_counts(list(rows.values()), expected=len(all_keys))
        for condition, rows in by_condition.items()
    }
    gates = _claim_gates(
        observations=observations,
        matched_keys=matched_keys,
        excluded_pairs=excluded_pairs,
        estimate=estimate,
        interval=interval,
        metric=validated["metric"],
        evidence=evidence,
        pairing_keys=pairing_keys,
    )
    requested_index = CLAIM_LEVELS.index(evidence["requested_claim"])
    granted_claim = next(
        (
            label
            for label in reversed(CLAIM_LEVELS[: requested_index + 1])
            if gates[label]["eligible"]
        ),
        None,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "comparison_id": validated["comparison_id"],
        "benchmark": benchmark,
        "comparison_unit": BENCHMARK_CONTRACTS[benchmark]["comparison_unit"],
        "conditions": conditions,
        "metric": validated["metric"],
        "pairing_keys": list(pairing_keys),
        "sample_counts": counts,
        "pairing": {
            "matched_pair_count": len(paired_differences),
            "matched_keys": matched_keys,
            "excluded_pair_count": len(excluded_pairs),
            "excluded_pairs": excluded_pairs,
        },
        "effect": {
            "estimand": "mean_paired_difference",
            "contrast": "candidate_minus_baseline",
            "estimate": estimate,
            "confidence_interval": (
                {
                    "method": "paired_two_way_cluster_percentile_bootstrap",
                    "cluster_factors": ["comparison_unit", "seed"],
                    "lower": interval[0],
                    "upper": interval[1],
                    "nominal_confidence_level": nominal_confidence,
                    "adjusted_confidence_level": adjusted_confidence,
                    "bootstrap_samples": analysis["bootstrap_samples"],
                    "analysis_seed": analysis["seed"],
                }
                if interval is not None
                else None
            ),
        },
        "multiple_comparisons": multiplicity,
        "evidence": {
            "execution_scope": evidence["execution_scope"],
            "prespecified": evidence["prespecified"],
        },
        "run_manifests": {
            condition: sorted({
                str(row["run_manifest"])
                for row in rows.values()
                if row.get("run_manifest")
            })
            for condition, rows in by_condition.items()
        },
        "claim_gates": gates,
        "requested_claim": evidence["requested_claim"],
        "granted_claim": granted_claim,
    }


def write_analysis_report(payload: dict[str, Any], output_path: Path) -> dict[str, Any]:
    report = analyze_comparison(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze a versioned paired benchmark comparison contract.")
    parser.add_argument("input", help="Paired comparison input JSON")
    parser.add_argument("--output", required=True, help="Machine-readable analysis report JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ComparisonContractError("Comparison input must be a JSON object.")
    write_analysis_report(payload, Path(args.output))
    print(f"Wrote paired benchmark analysis: {args.output}")
    return 0


def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ComparisonContractError(f"schema_version must be {SCHEMA_VERSION!r}.")
    comparison_id = payload.get("comparison_id")
    if not isinstance(comparison_id, str) or not comparison_id.strip():
        raise ComparisonContractError("comparison_id must be a non-empty string.")
    benchmark = payload.get("benchmark")
    if benchmark not in BENCHMARK_CONTRACTS:
        raise ComparisonContractError(f"benchmark must be one of {sorted(BENCHMARK_CONTRACTS)}.")

    conditions = payload.get("conditions")
    if not isinstance(conditions, dict):
        raise ComparisonContractError("conditions must be an object.")
    baseline = conditions.get("baseline")
    candidate = conditions.get("candidate")
    if not isinstance(baseline, str) or not baseline or not isinstance(candidate, str) or not candidate:
        raise ComparisonContractError("conditions.baseline and conditions.candidate must be non-empty strings.")
    if baseline == candidate:
        raise ComparisonContractError("Baseline and candidate conditions must differ.")

    metric = payload.get("metric")
    if not isinstance(metric, dict):
        raise ComparisonContractError("metric must be an object.")
    metric_name = metric.get("name")
    metric_role = metric.get("role")
    if not isinstance(metric_name, str) or not metric_name:
        raise ComparisonContractError("metric.name must be a non-empty string.")
    if metric_role not in ("primary", "exploratory"):
        raise ComparisonContractError("metric.role must be 'primary' or 'exploratory'.")
    if not isinstance(metric.get("higher_is_better"), bool):
        raise ComparisonContractError("metric.higher_is_better must be boolean.")
    expected_primary = BENCHMARK_CONTRACTS[benchmark]["primary_metric"]
    if metric_role == "primary" and metric_name != expected_primary:
        raise ComparisonContractError(
            f"Primary metric for {benchmark} must be {expected_primary!r}, not {metric_name!r}."
        )
    if metric_role == "primary" and metric.get("higher_is_better") is not True:
        raise ComparisonContractError(f"Primary metric for {benchmark} must use higher_is_better=true.")

    pairing_keys = payload.get("pairing_keys")
    expected_keys = BENCHMARK_CONTRACTS[benchmark]["pairing_keys"]
    if pairing_keys != list(expected_keys):
        raise ComparisonContractError(
            f"pairing_keys for {benchmark} must be {list(expected_keys)!r}."
        )

    observations = payload.get("observations")
    if not isinstance(observations, list) or not observations:
        raise ComparisonContractError("observations must be a non-empty array.")
    for index, observation in enumerate(observations):
        _validate_observation(
            observation,
            index=index,
            conditions=(baseline, candidate),
            pairing_keys=expected_keys,
        )

    analysis = payload.get("analysis")
    if not isinstance(analysis, dict):
        raise ComparisonContractError("analysis must be an object.")
    if not isinstance(analysis.get("seed"), int) or isinstance(analysis.get("seed"), bool):
        raise ComparisonContractError("analysis.seed must be an integer.")
    samples = analysis.get("bootstrap_samples")
    if (
        not isinstance(samples, int)
        or isinstance(samples, bool)
        or samples < MIN_BOOTSTRAP_SAMPLES
    ):
        raise ComparisonContractError(
            f"analysis.bootstrap_samples must be at least {MIN_BOOTSTRAP_SAMPLES}."
        )
    confidence = analysis.get("confidence_level")
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not math.isfinite(float(confidence))
        or float(confidence) != REQUIRED_CONFIDENCE_LEVEL
    ):
        raise ComparisonContractError(
            f"analysis.confidence_level must be {REQUIRED_CONFIDENCE_LEVEL}."
        )

    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        raise ComparisonContractError("evidence must be an object.")
    if evidence.get("requested_claim") not in CLAIM_LEVELS:
        raise ComparisonContractError(f"evidence.requested_claim must be one of {list(CLAIM_LEVELS)!r}.")
    if evidence.get("execution_scope") not in EXECUTION_SCOPES:
        raise ComparisonContractError(f"evidence.execution_scope must be one of {list(EXECUTION_SCOPES)!r}.")
    if not isinstance(evidence.get("prespecified"), bool):
        raise ComparisonContractError("evidence.prespecified must be boolean.")
    multiple = evidence.get("multiple_comparisons")
    if not isinstance(multiple, dict):
        raise ComparisonContractError("evidence.multiple_comparisons must be an object.")
    method = multiple.get("method")
    family_size = multiple.get("family_size")
    if method not in ("none", "bonferroni"):
        raise ComparisonContractError("multiple_comparisons.method must be 'none' or 'bonferroni'.")
    if not isinstance(family_size, int) or isinstance(family_size, bool) or family_size < 1:
        raise ComparisonContractError("multiple_comparisons.family_size must be a positive integer.")
    if method == "none" and family_size != 1:
        raise ComparisonContractError("Multiple comparisons require method='bonferroni'.")
    family_id = multiple.get("family_id")
    if not isinstance(family_id, str) or not family_id:
        raise ComparisonContractError("multiple_comparisons.family_id must be a non-empty string.")

    return {
        "comparison_id": comparison_id,
        "benchmark": benchmark,
        "conditions": {"baseline": baseline, "candidate": candidate},
        "metric": {
            "name": metric_name,
            "role": metric_role,
            "higher_is_better": metric["higher_is_better"],
        },
        "pairing_keys": expected_keys,
        "observations": observations,
        "analysis": {
            "seed": analysis["seed"],
            "bootstrap_samples": samples,
            "confidence_level": float(confidence),
        },
        "evidence": {
            "requested_claim": evidence["requested_claim"],
            "execution_scope": evidence["execution_scope"],
            "prespecified": evidence["prespecified"],
            "multiple_comparisons": {
                "family_id": family_id,
                "family_size": family_size,
                "method": method,
            },
        },
    }


def _validate_observation(
    observation: Any,
    *,
    index: int,
    conditions: tuple[str, str],
    pairing_keys: tuple[str, ...],
) -> None:
    prefix = f"observations[{index}]"
    if not isinstance(observation, dict):
        raise ComparisonContractError(f"{prefix} must be an object.")
    if observation.get("condition") not in conditions:
        raise ComparisonContractError(f"{prefix}.condition must name the baseline or candidate condition.")
    if observation.get("status") not in OBSERVATION_STATUSES:
        raise ComparisonContractError(f"{prefix}.status must be one of {list(OBSERVATION_STATUSES)!r}.")
    pairing = observation.get("pairing")
    if not isinstance(pairing, dict) or set(pairing) != set(pairing_keys):
        raise ComparisonContractError(f"{prefix}.pairing must contain exactly {list(pairing_keys)!r}.")
    for key in pairing_keys:
        if type(pairing[key]) not in (int, str) or pairing[key] == "":
            raise ComparisonContractError(
                f"{prefix}.pairing.{key} must be a non-empty string or integer; "
                "booleans and floating-point values are not valid identifiers."
            )
    value = observation.get("metric_value")
    if value is not None:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
            raise ComparisonContractError(f"{prefix}.metric_value must be a finite number or null.")
    if observation["status"] != "completed" and value is not None:
        raise ComparisonContractError(f"{prefix}.metric_value must be null for failed or missing observations.")
    manifest = observation.get("run_manifest")
    if manifest is not None and (not isinstance(manifest, str) or not manifest):
        raise ComparisonContractError(f"{prefix}.run_manifest must be a non-empty string or null.")


def _condition_counts(observations: list[dict[str, Any]], *, expected: int) -> dict[str, int]:
    return {
        "expected": expected,
        "observed": len(observations),
        "completed": sum(1 for row in observations if row["status"] == "completed"),
        "metric_available": sum(
            1 for row in observations if row["status"] == "completed" and row.get("metric_value") is not None
        ),
        "failed": sum(1 for row in observations if row["status"] == "failed"),
        "missing": sum(
            1
            for row in observations
            if row["status"] == "missing"
            or (row["status"] == "completed" and row.get("metric_value") is None)
        ) + expected - len(observations),
    }


def _pair_exclusion_reason(
    baseline: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
) -> str | None:
    if baseline is None:
        return "missing_baseline_observation"
    if candidate is None:
        return "missing_candidate_observation"
    if baseline["status"] == "failed" or candidate["status"] == "failed":
        return "failed_run"
    if (
        baseline["status"] == "missing"
        or candidate["status"] == "missing"
        or baseline.get("metric_value") is None
        or candidate.get("metric_value") is None
    ):
        return "missing_metric"
    return None


def _effective_status(observation: dict[str, Any] | None) -> str:
    if observation is None:
        return "absent"
    if observation["status"] == "completed" and observation.get("metric_value") is None:
        return "missing_metric"
    return str(observation["status"])


def _bootstrap_interval(
    differences: list[float],
    *,
    matched_keys: list[dict[str, Any]],
    pairing_keys: tuple[str, ...],
    seed: int,
    samples: int,
    confidence_level: float,
) -> tuple[float, float] | None:
    if not differences:
        return None
    generator = random.Random(seed)
    unit_keys = tuple(key for key in pairing_keys if key != "seed")
    cells = {
        (tuple(key[name] for name in unit_keys), key["seed"]): difference
        for key, difference in zip(matched_keys, differences)
    }
    units = sorted({unit for unit, _ in cells}, key=_stable_key)
    seeds = sorted({seed_value for _, seed_value in cells}, key=lambda value: _stable_key((value,)))
    estimates = []
    while len(estimates) < samples:
        sampled_units = [units[generator.randrange(len(units))] for _ in units]
        sampled_seeds = [seeds[generator.randrange(len(seeds))] for _ in seeds]
        replicate = [
            cells[(unit, seed_value)]
            for unit in sampled_units
            for seed_value in sampled_seeds
            if (unit, seed_value) in cells
        ]
        if replicate:
            estimates.append(_mean(replicate))
    estimates.sort()
    alpha = 1.0 - confidence_level
    return (_percentile(estimates, alpha / 2.0), _percentile(estimates, 1.0 - alpha / 2.0))


def _percentile(values: list[float], probability: float) -> float:
    position = probability * (len(values) - 1)
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return values[lower_index]
    fraction = position - lower_index
    return values[lower_index] + fraction * (values[upper_index] - values[lower_index])


def _adjusted_confidence_level(confidence: float, *, method: str, family_size: int) -> float:
    if method == "none":
        return confidence
    return 1.0 - ((1.0 - confidence) / family_size)


def _claim_gates(
    *,
    observations: list[dict[str, Any]],
    matched_keys: list[dict[str, Any]],
    excluded_pairs: list[dict[str, Any]],
    estimate: float | None,
    interval: tuple[float, float] | None,
    metric: dict[str, Any],
    evidence: dict[str, Any],
    pairing_keys: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    diagnostic_reasons = [] if observations else ["no_observations"]
    integration_reasons = []
    if evidence["execution_scope"] in ("smoke", "connectivity"):
        integration_reasons.append("smoke_or_connectivity_only")
    if not matched_keys:
        integration_reasons.append("no_complete_matched_pairs")
    if any(not row.get("run_manifest") for row in observations if row["status"] != "missing"):
        integration_reasons.append("missing_run_manifest")

    performance_reasons = list(integration_reasons)
    if evidence["execution_scope"] != "full":
        performance_reasons.append("not_full_evaluation")
    if metric["role"] != "primary":
        performance_reasons.append("exploratory_metric")
    if not evidence["prespecified"]:
        performance_reasons.append("outcome_not_prespecified")
    if len(matched_keys) < MIN_PERFORMANCE_PAIRS:
        performance_reasons.append("insufficient_matched_pairs")
    distinct_seeds = {key["seed"] for key in matched_keys}
    if len(distinct_seeds) < 2:
        performance_reasons.append("insufficient_seed_replication")
    non_seed_keys = tuple(key for key in pairing_keys if key != "seed")
    distinct_units = {tuple(key[name] for name in non_seed_keys) for key in matched_keys}
    if len(distinct_units) < 2:
        performance_reasons.append("insufficient_comparison_units")
    if len(matched_keys) != len(distinct_units) * len(distinct_seeds):
        performance_reasons.append("incomplete_crossed_matrix")
    if excluded_pairs:
        performance_reasons.append("failed_missing_or_unmatched_observations")
    if estimate is None or not _effect_is_favorable(estimate, higher_is_better=metric["higher_is_better"]):
        performance_reasons.append("observed_effect_not_favorable")
    if interval is None or not _interval_is_favorable(interval, higher_is_better=metric["higher_is_better"]):
        performance_reasons.append("uncertainty_interval_not_favorable")

    return {
        "diagnostic": {
            "eligible": not diagnostic_reasons,
            "reasons": diagnostic_reasons,
            "minimum_matched_pairs": 0,
        },
        "integration_validation": {
            "eligible": not integration_reasons,
            "reasons": integration_reasons,
            "minimum_matched_pairs": 1,
        },
        "performance_claim": {
            "eligible": not performance_reasons,
            "reasons": list(dict.fromkeys(performance_reasons)),
            "minimum_matched_pairs": MIN_PERFORMANCE_PAIRS,
            "minimum_distinct_seeds": 2,
            "minimum_distinct_comparison_units": 2,
        },
    }


def _interval_is_favorable(interval: tuple[float, float], *, higher_is_better: bool) -> bool:
    return interval[0] > 0.0 if higher_is_better else interval[1] < 0.0


def _effect_is_favorable(estimate: float, *, higher_is_better: bool) -> bool:
    return estimate > 0.0 if higher_is_better else estimate < 0.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _stable_key(value: tuple[Any, ...]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    raise SystemExit(main())
