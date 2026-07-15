# C-WAH Issue #248 Post-Grab Diagnostic

<!-- benchmark-result: cwah-issue248-post-grab-diagnostic-v3 -->

This is a bounded diagnostic comparison, not benchmark-performance evidence. Version 3 supersedes immutable releases v1 and v2 and is derived only from finalized matrix attempt `8ea8f3533ce14adbaf2bf88b9e9174a8` on tasks `0,1,2`, seeds `0,1`, and 25 steps. The matched source is immutable release `benchmark-cwah-issue292-dual-dag-diagnostic-v2`.

## Source And Identity

The source matrix provenance directly captures policy commit `1495175f41928fb53559a5434636a421e826a470` with `repository.dirty=false`. Remote verification shows `origin/issue-248-post-grab-placement` points exactly to that commit. GitHub created the immutable v3 release tag at `e56cbc9555b761c5930d2f98c551ef0b8ce01758` (`origin/main`), which is an ancestor of the policy commit rather than the policy snapshot itself; the archive provenance and remote issue branch, not the release tag target, identify the evaluated policy. The provenance directly captures the matrix settings, model and digest, CoELA commit and dirty status, dataset checksum, and Unity executable checksum recorded in `environment_identity.json`.

VirtualHome `wah` commit `6773be207680d47985091b90568e2a12fd3d321f` and Unity data-tree checksum `f63c17e80e0624c2f48398f12d33c8f1af1f9bee746431cd58b4b3444678b40b` are post-run supplemental verification. They were not fields captured by the original matrix provenance, and no unauditable timing claim is made. Their values agree with #292's checked-in diagnostic record, but this does not establish strict runtime equivalence, particularly because the matrix provenance records the CoELA worktree as dirty.

## Observed Diagnostic

The common report was generated from the existing v3 matrix with `python -m benchmarks.common.report`; real CoELA was not rerun.

- Runtime: 6/6 runs completed, with 0 runtime failures.
- Outcome: 0 task successes and mean normalized progress `0.5920745920745921`.
- Actions: 142 `walktowards`, 8 `grab`, and no placement actions.
- Failures: 0 failed-action records and 0 result failures.
- Navigation-loop suppressions: 4.

#292 v2 recorded 144 `walktowards`, 6 `grab`, no placement, 8 navigation-loop suppressions, 0 successes, and the same mean progress. These are descriptive bounded diagnostics only. They do not establish improvement, regression, or strict equivalence.

## Paired Comparison

The Issue #298 contract pairs all six episodes by `task_id` and `seed`. The paired normalized-progress effect is `0.0` with 95% interval `[0.0, 0.0]`; there are 6 matched numerical pairs and no excluded, failed, or missing metric observations. The report grants only `diagnostic`.

The candidate archive contains exact per-run manifests at `runs/task_<task>_seed_<seed>/artifact_manifest.json`. Each covers that run's sanitized `dual_dag_artifact.json`, including its size and SHA-256, and every candidate observation links the corresponding manifest.

The immutable #292 v2 archive has no per-run manifests and cannot be changed. Baseline observations therefore retain their completed metrics but set `run_manifest` to `null`; artifact links are not substituted for manifests. The report consequently marks `integration_validation` ineligible with reason `missing_run_manifest`. Performance-claim reasons additionally include `not_full_evaluation`, `outcome_not_prespecified`, `observed_effect_not_favorable`, and `uncertainty_interval_not_favorable`.

## Next Experiment

The next bounded experiment should test public agent-message coordination or explicit target handoff. It must not share hidden state, another agent's private observation, evaluator progress, full graph state, or simulator debug/grounding fields.

## Immutable Evidence

Release `benchmark-cwah-issue248-post-grab-diagnostic-v3` contains common reports, aggregate and structural evidence, exact candidate per-run manifests, environment identity, comparison artifacts, provenance, and manifest verification. Raw stdout, agent-goal payloads, hidden state, credentials, prompts/reasoning, absolute paths, transforms, grounding, and debug fields are excluded.

Immutable releases `benchmark-cwah-issue248-post-grab-diagnostic-v1` and `benchmark-cwah-issue248-post-grab-diagnostic-v2` remain available as historical evidence but are superseded and are not registered declarations for this result.

- Archive: [cwah-issue248-post-grab-diagnostic-v3.zip](https://github.com/upiscium/VillagerAgent/releases/download/benchmark-cwah-issue248-post-grab-diagnostic-v3/cwah-issue248-post-grab-diagnostic-v3.zip)
- Archive SHA-256: `058d7f0187267b098d4ca5563e540a778843addaf1373f0496b716d28bbae4ca`
- Metadata SHA-256: `f3efa6702f937028517bedcbaca339cb79477bac23b1acc0b02a20fe4defd82d`
- Manifest SHA-256: `c5c8212ab67baa3a9b2eccfa19f7347f67cb0bce8013035ff9e339e446a9753e`
