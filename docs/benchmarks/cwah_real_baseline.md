# C-WAH Real Evaluation Baseline

<!-- benchmark-result: cwah-bounded-baseline-diagnostic -->

This is an explicitly legacy pre-publication-policy diagnostic record. Its source bundles are unavailable, so it does not satisfy Issue #297 publication requirements and cannot support paper or performance claims.

This workflow records a bounded real CoELA baseline for the current C-WAH policy. It is intended to measure the current implementation state before further policy improvements. It is not a benchmark-performance claim.

## Preconditions

- Follow `docs/benchmarks/coela_runtime_setup.md`.
- Keep CoELA/VirtualHome runtime assets local-only and out of git.
- Run through `nix develop` so CoELA dependencies are available.

## Fast Mock Validation

Run this before a real baseline to check artifact/report generation:

```bash
python -m benchmarks.cwah.baseline \
  --env mock \
  --tasks 0,1 \
  --seeds 0,1 \
  --full-episode \
  --max-steps 4 \
  --prefer-physical-after-steps 0 \
  --output-dir /tmp/opencode/cwah-baseline-mock
```

Expected outputs:

- `/tmp/opencode/cwah-baseline-mock/matrix_summary.json`
- `/tmp/opencode/cwah-baseline-mock/matrix_metrics.csv`
- `/tmp/opencode/cwah-baseline-mock/common_report/common_report.csv`
- `/tmp/opencode/cwah-baseline-mock/common_report/common_report.json`
- `/tmp/opencode/cwah-baseline-mock/common_report/baseline_manifest.json`

## Bounded Real Baseline

Use a small bounded matrix first:

```bash
nix develop --command python -m benchmarks.cwah.baseline \
  --env coela \
  --tasks 0,1,2 \
  --seeds 0,1 \
  --full-episode \
  --max-steps 25 \
  --prefer-physical-after-steps 0 \
  --coela-cwah-path /tmp/opencode/VillagerAgent-cwah-173/external/CoELA/cwah \
  --executable-file /tmp/opencode/VillagerAgent-cwah-173/external/CoELA/executable/linux_exec.v2.3.0.x86_64 \
  --base-port 6514 \
  --port-stride 10 \
  --output-dir /tmp/opencode/cwah-real-baseline-YYYYMMDD
```

The generated common report summarizes success/progress, step counts, failed runs, and action mix. The manifest records the exact matrix command and marks `performance_claim` as `false`.

## 2026-07-04 Bounded Baseline

Local run artifact directory: `/tmp/opencode/cwah-real-baseline-20260704`.

Configuration:

- Tasks: `0,1,2`
- Seeds: `0,1`
- Step budget: `25`
- Full episode mode: enabled
- Physical-action preference: from step `0`

Observed common-report aggregate:

- Runs: `6`
- Runtime failed runs: `0`
- Task successes: `0`
- Success rate: `0.0`
- Mean normalized progress: `0.5920745920745921`
- Mean steps: `25.0`
- Physical actions: `150`
- Communication actions: `0`
- Action mix: `grab=130`, `putin=20`

Per-task progress was `0.6153846153846154` for tasks `0` and `1`, and `0.5454545454545454` for task `2`. This confirms the current policy executes non-`walktowards` physical actions in real CoELA but does not solve the bounded tasks.

## Interpretation

Use this baseline to compare future policy changes against the same task/seed/step budget. Do not report it as benchmark performance unless the run set, policy, runtime assets, and failure analysis are all documented and reviewed.

The first goal-aware policy comparison is recorded in `docs/benchmarks/cwah_goal_policy.md`.
