# Paired Benchmark Comparisons

`benchmarks.common.analysis` defines paired comparison contract version `1.0.0`. It is separate from common report schema v2: common reports describe runs, while comparison reports estimate one named outcome for two conditions. This prevents CRAFT, C-WAH, and Minecraft outcomes, or primary and exploratory outcomes, from being pooled into one estimate.

## Benchmark Contracts

| Benchmark | Prespecified primary metric | Comparison unit | Required pairing keys |
| --- | --- | --- | --- |
| CRAFT | `final_progress` | One evaluated structure | `structure_id`, `seed` |
| C-WAH | `normalized_progress` | One task episode | `task_id`, `seed` |
| Minecraft | `task_completion_rate` | One judged task run | `task_id`, `seed`, `world_id` |

CRAFT analysis must use per-structure `final_progress` rows from `normalized/metrics.csv`, not run-level `mean_final_progress`, so structures remain matched. C-WAH uses each task/seed episode. Minecraft additionally matches the reset world or world snapshot identifier because the same task and seed in different worlds are not interchangeable.

The fixed primary metric is the only metric with `role: "primary"`. Other metrics may be analyzed in separate contracts with `role: "exploratory"`; exploratory results cannot pass the performance-claim gate. A contract contains only one metric, so unlike outcomes cannot be mixed.

## Input Contract

Run:

```bash
python -m benchmarks.common.analysis comparison.json --output comparison-report.json
```

Minimal input shape:

```json
{
  "schema_version": "1.0.0",
  "comparison_id": "dual-dag-vs-baseline",
  "benchmark": "cwah",
  "conditions": {"baseline": "baseline", "candidate": "dual-dag"},
  "metric": {
    "name": "normalized_progress",
    "role": "primary",
    "higher_is_better": true
  },
  "pairing_keys": ["task_id", "seed"],
  "observations": [
    {
      "condition": "baseline",
      "pairing": {"task_id": 0, "seed": 0},
      "status": "completed",
      "metric_value": 0.25,
      "run_manifest": "result/baseline/task_0_seed_0/artifact_manifest.json"
    },
    {
      "condition": "dual-dag",
      "pairing": {"task_id": 0, "seed": 0},
      "status": "completed",
      "metric_value": 0.5,
      "run_manifest": "result/dual-dag/task_0_seed_0/artifact_manifest.json"
    }
  ],
  "analysis": {
    "seed": 20260715,
    "bootstrap_samples": 10000,
    "confidence_level": 0.95
  },
  "evidence": {
    "requested_claim": "integration_validation",
    "execution_scope": "bounded",
    "prespecified": true,
    "multiple_comparisons": {
      "family_id": "primary",
      "family_size": 1,
      "method": "none"
    }
  }
}
```

Observation `status` is `completed`, `failed`, or `missing`. Failed and missing observations have `metric_value: null`. A completed observation may also have a null metric when the run completed but the outcome was unavailable. Missing counterparts, failed runs, and unavailable metrics are excluded from the numeric paired estimate but remain explicit in `sample_counts` and `pairing.excluded_pairs`. Per-condition counts distinguish expected pairing keys from observed records and count an absent counterpart as missing. Any such exclusion blocks a performance claim, so failures cannot be silently removed from a favorable comparison. Pairing identifiers must be non-empty strings or integers. Booleans and floating-point identifiers, including non-finite values, are rejected so JSON values that compare equal in Python cannot create false pairs.

Each non-missing observation should link its exact `artifact_manifest.json`. Missing manifest links block integration validation and performance claims. A deliberately expected but absent observation may have a null manifest and is still reported as missing.

## Effect And Uncertainty

The effect is the arithmetic mean of within-pair `candidate - baseline` differences. The confidence interval is a two-way cluster percentile bootstrap for the crossed comparison-unit x seed design. Each replicate independently samples the observed non-seed comparison units and seeds with replacement, then averages their available intersections. This preserves dependence among observations that share a task, structure/world unit, or seed instead of treating every cell as independent. A performance claim additionally requires the complete comparison-unit x seed cross; incomplete matrices may still be reported but cannot pass that gate. `analysis.seed` initializes an isolated deterministic random generator, making identical inputs and settings reproducible regardless of observation order.

Contract version `1.0.0` fixes the nominal confidence level at `0.95` and requires at least 10,000 bootstrap replicates. Lower replicate counts or caller-selected confidence levels are rejected rather than allowed to weaken a performance gate. The point estimate and the multiplicity-adjusted interval must both be wholly in the declared favorable direction.

For one prespecified comparison, use `method: "none"` and `family_size: 1`. When several policies or metrics form one comparison family, give every separate metric contract the same `family_id`, set the total `family_size`, and use `method: "bonferroni"`. The report then uses confidence level `1 - (1 - nominal confidence) / family_size`. Undeclared multiple comparisons are rejected.

## Claim Gates

The report evaluates all three labels and emits the highest eligible label at or below `requested_claim` as `granted_claim`.

- `diagnostic`: at least one recorded observation. Smoke and connectivity checks cannot exceed this label.
- `integration_validation`: at least one complete matched pair from a bounded or full execution, with manifest links for all non-missing observations.
- `performance_claim`: all integration requirements; `execution_scope: "full"`; a prespecified primary metric; at least five complete pairs spanning at least two seeds and two non-seed comparison units; a complete comparison-unit x seed matrix; no failed, missing, or unmatched observations; and both the point estimate and multiplicity-adjusted interval wholly in the declared favorable direction.

Five pairs is a minimum reporting gate, not a claim that five pairs provide adequate power for every effect. Evaluation plans should use more replications when expected effects are small or variability is high.

Issue #291 CRAFT replication should create one observation per structure and seed and use this contract before promoting the V4 delta. Issue #292 remains diagnostic-only because its specified bounded C-WAH matrix is explicitly not benchmark-performance evidence; it may still emit a comparison report with `requested_claim: "diagnostic"`.
