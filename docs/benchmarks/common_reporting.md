# Common Benchmark Reporting

`benchmarks.common.report` converts benchmark-specific normalized artifacts into a minimal shared CSV/JSON schema.

Supported inputs:

- C-WAH matrix output directories containing `matrix_summary.json`
- C-WAH normalized run directories containing `summary.json`
- CRAFT run directories containing `normalized/summary.json` and `normalized/metrics.csv`
- Minecraft/Villager Agent run directories containing top-level `summary.json` and `metrics.json`

Example for a C-WAH matrix run:

```bash
python -m benchmarks.common.report /tmp/opencode/cwah-real-matrix-20260703 \
  --output result/benchmark_common_report.csv \
  --json-output result/benchmark_common_report.json
```

The shared schema includes benchmark name, run status, task/seed where available, episode counts, success/progress, step counts, failed-run counts, and action mix. CRAFT-specific report files and artifact schemas are unchanged.

For Minecraft/Villager Agent inputs, common reports read the normalized `summary.json`, `metrics.json`, and optional `action_log.json` produced by `benchmarks.minecraft.experiment`. They map task metadata, task completion rate, progress, action counts, failed action counts, and runtime errors into the shared fields. `talkTo` actions are counted as communication actions; other Minecraft tool actions are counted as physical actions.

For C-WAH inputs, common reports also include policy diagnostics when available:

- `policy_override_count` and `policy_override_rate`
- `failed_action_record_count`
- `result_failure_count`
- `failed_action_counts`
- `policy_override_reason_counts`

The JSON report aggregate sums these diagnostics across runs.
