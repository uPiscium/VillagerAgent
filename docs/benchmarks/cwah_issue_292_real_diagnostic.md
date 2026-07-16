# C-WAH Issue #292 Real Post-Dual-DAG Diagnostic

<!-- benchmark-result: cwah-issue292-dual-dag-diagnostic-v2 -->

This retained bounded diagnostic is archived evidence. It is not a benchmark
performance claim.

The matrix used tasks `0,1,2`, seeds `0,1`, full-episode mode, 25 steps,
physical preference from step `0`, and navigation-loop threshold `12`:

```bash
nix develop --command python -m benchmarks.cwah.matrix \
  --env coela --tasks 0,1,2 --seeds 0,1 --full-episode \
  --max-steps 25 --prefer-physical-after-steps 0 \
  --model gemma4:e4b --base-port 6714 --port-stride 10 \
  --output-dir /tmp/opencode/cwah-issue292-real-matrix-20260715
```

Runtime identity:

- VillagerAgent commit: `36cfc8cb62eb68013c5d935c0513f5ac81f3a372`
- Ollama model: `gemma4:e4b`, digest `c6eb396dbd5992bbe3f5cdb947e8bbc0ee413d7c17e2beaae69f5d569cf982eb`
- CoELA commit: `3e12dea925d735eefce33da71806ae9da6fcaf3f`
- VirtualHome `wah` commit: `6773be207680d47985091b90568e2a12fd3d321f`
- Dataset SHA-256: `7780e1aebff70c840a1ca66ea27904c2dfe203b09b30f04aa6c001e594f432a0`
- Unity executable SHA-256: `19d9b4735933c6ff013a05831a69acd3a10269b2934b38e51ff641b0202e01b1`
- Unity data directory: `external/CoELA/executable/linux_exec.v2.3.0_Data`
- Unity data directory bytes (`du -sb`): `4858408881`
- Unity data tree SHA-256: `f63c17e80e0624c2f48398f12d33c8f1af1f9bee746431cd58b4b3444678b40b`

The first one-policy-step smoke failed while the Ollama scheduler could not
schedule the model. After the pinned model accepted inference again, a repeated
one-policy-step smoke completed before the matrix started. No raw server output
was retained.

Observed diagnostic aggregate:

- Runs: `6`; runtime failed runs: `0`; task successes: `0`
- Mean normalized progress: `0.5920745920745921`; mean steps: `25.0`
- Action mix: `walktowards=144`, `grab=6`
- Policy overrides: `150`, all `prefer_physical_after_steps`
- Failed-action records and result failures: `0`; failure-reason counts: none
- Navigation-loop suppressions: `8`

Every run repeatedly navigated, grabbed once, and resumed navigation until the
budget without attempting placement. All six source snapshots contained both
observation and action-candidate nodes. The public bundle excludes simulator
grounding, agent-goal payloads, prompts/reasoning, raw process output, hidden
evaluator state, credentials, debug fields, and personal absolute paths.

Agent-facing goal hints are intentional per-agent policy input. Regression tests
verify that `total_goal`, evaluator progress, and `full_graph` are absent from
the decision context. Compact evidence is in
`docs/benchmarks/evidence/cwah_issue_292/diagnostic_summary.json`; the contract
is `configs/cwah/diagnostics/issue_292_real_matrix.json`.

Immutable release `benchmark-cwah-issue292-dual-dag-diagnostic-v1` remains
superseded because it identifies the wrong Unity data directory and has a
publication-contract filename mismatch. The sole declared and registered record
is `benchmark-cwah-issue292-dual-dag-diagnostic-v2`:

- Archive: [cwah-issue292-dual-dag-diagnostic-v2.zip](https://github.com/upiscium/VillagerAgent/releases/download/benchmark-cwah-issue292-dual-dag-diagnostic-v2/cwah-issue292-dual-dag-diagnostic-v2.zip)
- Archive SHA-256: `7bdc6a69140630b988cb1b518cbff2c1b754b57c234c847a5435a1d83db4db79`
- Metadata SHA-256: `e84d693dd0c4ac298eeaccf1e97a1533f4a958db8b7184d5dc6660d87d49604e`
- Manifest SHA-256: `fd51b8eac520fc953fa228904682e2275a91b0257cec61e43c653856ca8af1f1`

The retired 2026-07-10 search-discovery record has matched task, seed, and step
settings but unavailable source bundles and runtime identities. It is not a
strict runtime-equivalent baseline, and no performance or no-change claim
follows from that historical context.
