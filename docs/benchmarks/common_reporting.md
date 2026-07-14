# Common Benchmark Reporting

`benchmarks.common.report` converts benchmark-specific normalized artifacts into a minimal shared CSV/JSON schema.

Supported inputs:

- C-WAH matrix output directories containing `matrix_summary.json`
- C-WAH normalized run directories containing `summary.json`
- CRAFT run directories containing `normalized/summary.json` and `normalized/metrics.csv`
- Minecraft/Villager Agent run directories containing top-level `summary.json` and `metrics.json`
- Minecraft/Villager Agent matrix directories containing `matrix_summary.json` with `benchmark == "minecraft"`

Example for a C-WAH matrix run:

```bash
python -m benchmarks.common.report /tmp/opencode/cwah-real-matrix-20260703 \
  --output result/benchmark_common_report.csv \
  --json-output result/benchmark_common_report.json
```

Common report schema version 2 includes benchmark name, run and attempt identifiers, run status, task/seed where available, evaluation-unit counts, success/progress, step counts, failed-run counts, metric availability, and action mix. CRAFT-specific report files and artifact schemas are unchanged.

Evaluation units and success have benchmark-specific definitions:

- C-WAH uses one environment episode per row.
- CRAFT uses one evaluated game/structure as an episode.
- Minecraft uses one benchmark run as an episode. A run succeeds only when its non-empty runtime task set is complete. `task_count`, `completed_task_count`, and `task_completion_rate` preserve task-level completion separately.

`mean_progress` and `mean_steps` are aggregated only within the same benchmark and are weighted by each row's episode count. Missing values remain `null` instead of being converted to zero. `progress_available`, `steps_available`, and `action_log_available` distinguish unavailable data from a measured zero; aggregate availability counts expose partial inputs. For mixed-benchmark JSON reports, incompatible outcome metrics are `null` at the top level and separate aggregates are emitted under `aggregate.by_benchmark`.

CRAFT `mean_steps` is derived from observed normalized turn records per game, not the configured turn budget. Action availability requires observed turns or populated action metrics. Minecraft action availability is recorded by the producer; the existence of an empty placeholder `action_log.json` does not imply a measured zero-action run.

When an input belongs to a managed attempt, common reporting validates its artifact manifest and checksums before reading summaries. Legacy artifacts without attempt metadata remain readable, but incomplete managed attempts are rejected.

For Minecraft/Villager Agent inputs, common reports read the normalized `summary.json`, `metrics.json`, and optional `action_log.json` produced by `benchmarks.minecraft.experiment`. They also accept matrix summaries from `benchmarks.minecraft.matrix`. They map run success, task completion rate, progress, action counts, failed action counts, and runtime errors into separate shared fields. `talkTo` actions are counted as communication actions; other Minecraft tool actions are counted as physical actions. If `action_log.json` is absent, action and action-derived step fields are unavailable rather than zero.

For C-WAH inputs, common reports also include policy diagnostics when available:

- `policy_override_count` and `policy_override_rate`
- `failed_action_record_count`
- `result_failure_count`
- `failed_action_counts`
- `policy_override_reason_counts`

The JSON report aggregate sums these diagnostics across runs.
