# Common Benchmark Reporting

`benchmarks.common.report` converts benchmark-specific normalized artifacts into a minimal shared CSV/JSON schema.

Supported inputs:

- C-WAH matrix output directories containing `matrix_summary.json`
- C-WAH normalized run directories containing `summary.json`
- CRAFT run directories containing `normalized/summary.json` and `normalized/metrics.csv`

Example for a C-WAH matrix run:

```bash
python -m benchmarks.common.report /tmp/opencode/cwah-real-matrix-20260703 \
  --output result/benchmark_common_report.csv \
  --json-output result/benchmark_common_report.json
```

The shared schema includes benchmark name, run status, task/seed where available, episode counts, success/progress, step counts, failed-run counts, and action mix. CRAFT-specific report files and artifact schemas are unchanged.
