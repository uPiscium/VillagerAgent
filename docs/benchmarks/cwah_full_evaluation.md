# C-WAH Full-Episode Evaluation

This note records the reproducible command shape for running the C-WAH symbolic adapter beyond the default smoke-step cap.

## Preconditions

- `external/CoELA` is initialized at the pinned commit recorded in `external/README.md`.
- VirtualHome API `wah` branch is available beside `external/CoELA/cwah` as `external/CoELA/virtualhome`.
- The CoELA-provided executable is available as `external/CoELA/executable/linux_exec.v2.3.0.x86_64` and is executable.
- Runtime assets remain local-only and ignored by git.

See `docs/benchmarks/coela_runtime_setup.md` for license constraints and setup commands.

## Command

Run from the repository root:

```bash
nix develop --command python -m benchmarks.cwah.llm_smoke \
  --env coela \
  --full-episode \
  --task-id 0 \
  --seed 0 \
  --max-steps 250 \
  --output /tmp/opencode/cwah-full-coela-task0-seed0.json
```

Use a smaller `--max-steps` value for bounded validation runs. With `--full-episode`, the runner stops when CoELA reports terminal or when the configured step budget is exhausted. For multi-task baseline runs that also produce common reports, use `docs/benchmarks/cwah_real_baseline.md`.

## Artifact Fields

The output JSON contains:

- `run_config`: environment, task id, seed, model, step budget, and whether full-episode mode was used.
- `events`: episode start, per-agent policy decisions, step results, and completion event.
- `metrics`: `task_success`, `normalized_progress`, and `episode_steps`.

Matrix runs also write `matrix_summary.json` and `matrix_metrics.csv`. Use `python -m benchmarks.common.report <matrix-output-dir> --output <report.csv> --json-output <report.json>` to convert them into the shared benchmark report schema documented in `docs/benchmarks/common_reporting.md`.

The policy used by `benchmarks.cwah.llm_smoke` is intentionally simple and intended for integration validation. It does not claim task-solving performance.
