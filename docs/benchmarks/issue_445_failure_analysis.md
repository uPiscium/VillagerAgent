# Issue 445 Failure Analysis

## Decision

The canonical movement target uses the `entity_feet` convention. Target Y `-60` is valid and reachable in both approved baselines. The failed position `(5.5, -59.0, 5.483812240562806)` is not a successful diagonal target observation: its entity feet remained exactly one block above the required feet Y.

The failure was caused by the legacy meta-judger rebuilding the runtime world after startup. The failed bundle records `clearing_arena`, `placing_structure`, and `building_arena_shell`. Both attempts to move to entity-feet coordinate `(5, -60, 5)` reached `(5.5, -59.0, 5.483812240562806)` and returned the non-air target-cell branch, proving that the generated arena occupied the target feet cell. The external judger then correctly rejected the strict Y boundary delta of `1.0`.

This is Case B from the position-contract investigation. Strict per-axis comparison, tolerance `1.0`, iteration limits, and canonical target coordinates remain unchanged.

## Failed Conditions

| Field | Value |
| --- | --- |
| Revision | `d283405e45959543dbd27060347cb4fdb5f977ac` |
| Attempt | `98de438d74d24cc696108d4a71b83a1b` |
| Task | legacy canary equivalent to `diagonal` |
| Seed | `0` |
| World ID | `meta-move-world-v1` |
| Approved matrix baseline | no |
| Target | `(5, -60, 5)` entity feet |
| Completion | strict per-axis, every delta `< 1.0` |
| Final entity feet | `(5.5, -59.0, 5.483812240562806)` |
| Absolute residual | `(0.5, 1.0, 0.4838122405628056)` |
| Signed remaining movement | `(-0.5, -1.0, -0.4838122405628056)` |
| Model | `gemma4:12b` |
| Model digest | `4eb23ef187e2c5462566d6a1d3bbbc2f1346d0b4327cbb66d58fffbcc9b2b05c` |
| Failed endpoint | `http://ollama.arc.upiscium.dev/v1` |
| Legacy snapshot SHA-256 | `8519378f5d71195ac67294acb318994ef660afdba92eada7289faa9be9f74673` |
| Agent iterations | `1/7` |
| External judger iterations | `1/1` |

The failed bundle does not publish a runtime composite digest, legacy snapshot tree hash, target block type, collision box, or surrounding block states. Those fields remain explicitly unavailable rather than inferred.

## Coordinate Semantics

Minecraft entity `Pos`, Mineflayer `bot.entity.position`, `/tp`, `GoalBlock`, and the movement tool's target are entity-feet coordinates in this benchmark.

For diagnostics only, flooring the failed observation gives block cell `(5, -59, 5)` and the full-block support coordinate `(5, -60, 5)`. That support coordinate must not be compared to an entity-feet target. Accepting the failed observation as support-block success would silently change the benchmark task and contradict the approved reachability evidence.

The approved baseline geometry deliberately has two standing levels:

- Initial and near feet Y `-59` stand on the raised full-block platform at block Y `-60`.
- Diagonal and long-distance feet Y `-60` stand on the lower full-block floor at block Y `-61`.

## Approved Baseline Impact

No baseline archive or world tree changes are required.

| Baseline | Archive SHA-256 | Tree SHA-256 | Reachability |
| --- | --- | --- | --- |
| `baseline_open` | `644707660d4fb073016830f2f89e78a1c348d7845d4964326c21a6076a137c1b` | `3e743096c7b0a5bde0784c126bf9a36f0b3bd38901c964d6d5a3118181ad3594` | 3/3 |
| `baseline_obstructed` | `da502d68911ec1a1cbed953df54d1baf7a0e277c4da50656a3e7f2b36fef5237` | `823758d8bb60a9ba7907e7cab2c8e036911ba564da3e5e83977e5ca517814d4b` | 3/3 |

The existing probes report Y delta `0.000` for the Y `-60` targets. A new convention attestation can bind `entity_feet` to this unchanged evidence without rewriting historical acquisition provenance or replacing the archives.

## Required Fix

1. Preserve restored approved worlds instead of invoking legacy arena generation.
2. Declare `entity_feet` in variants, matrix runs, runtime config, probe output, movement results, scores, and diagnostics.
3. Fail closed when matrix evidence omits or disagrees on the convention.
4. Preserve legacy non-matrix behavior when no explicit convention is supplied.
5. Emit both absolute axis deltas and signed remaining deltas so a one-block downward correction is explicit.
6. Regenerate variant hashes and the finalized premanifest after merge.

## Gate Policy

No real canary, five-run gate, or matrix run is valid on the PR branch. After merge, generate a fresh finalized premanifest for the exact main SHA and fixed Ollama endpoint `http://10.255.255.5:11434`, run exactly one diagonal canary, and restart the five-run gate from Run 1 with no retries.
