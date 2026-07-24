# CRAFT Clarify and Dual-DAG Evaluation

This report tracks issue #370. The paper-facing condition is oracle-assisted
CRAFT with `oracle_n=5`, 20 turns, no Builder tool use, and matched model and
sampling settings.

## Comparison Conditions

The final comparison includes:

- VillagerAgent baseline (V0)
- Dual-DAG with Clarify disabled (V1)
- Dual-DAG with current Clarify
- Dual-DAG with budgeted Clarify
- Dual-DAG with value-of-information Clarify (V4)

V0 and V1 are retained from the complete Issue #291 matrix because neither can
execute Clarify, so response ingestion cannot affect their execution path. The
full upstream integration/failure evidence is also retained separately from the
Issue #291 immutable v5 archive and is never substituted into comparable rows.
Current, budgeted, and V4 are rerun after the lifecycle implementation because
their Clarify responses can update candidate state.

`configs/craft/experiments/gemma4_12b_clarify_policy_official_remaining.yaml`
declares the remaining 3 conditions x 3 seeds x 20 structures as 180 independent
structure checkpoints. The budgeted condition limits Clarify to two events per
episode, applies a one-turn cooldown, prevents duplicate questions, and
preserves at least two turns after a Clarify action. Config parity validation
rejects mismatched `oracle_n`, turns, model, and temperature.

## Diagnostic Contract

Each Clarify event produces `normalized/clarification_trace.jsonl` and a
classified row in `normalized/clarification_outcomes.jsonl`. The trace records:

- turn and remaining action budget
- actual, valid, and blocked oracle candidate counts
- pre/post candidate actions, states, scores, top candidate, and top-two margin
- target Director, canonical question key, expected evidence, and related candidates
- response ingestion and public claim/fact identifiers
- actual candidate unlocks, invalidations, and hypothesis/evidence resolutions
- next physical action, latency, and progress delta

Outcome classification is deterministic and emits `beneficial`, `neutral`,
`harmful`, or `failed`. The required aggregate metrics include final progress,
progress@20, normalized progress AUC, physical/place/remove/clarify/wait counts,
progress per turn and physical action, same-action rate, unlock rate, positive
action latency, Builder fallback, gate reasons, and blocked oracle candidates.

## Lifecycle Gate

The diagnostic-only lifecycle probe uses the official `oracle_n=5`, raises the
confidence threshold to force at most one Clarify, and runs for three turns so
the following physical action remains observable:

```bash
python -m benchmarks.craft.run \
  --config configs/craft/eval_gemma4_12b_ollama_dual_dag_clarify_lifecycle_probe.yaml \
  --structure 0 --turns 3 --seed 3 --oracle-n 5 \
  --run-name-suffix _issue370_lifecycle_gate --overwrite
```

The retained gate produced one Clarify, ingested one target-Director response,
created one response-supported ResolvedFact, and executed a physical follow-up
at latency one. It recorded five valid oracle candidates, two remaining turns,
no candidate unlock, no repeated clarification, no Builder fallback, and no
invalid action. The outcome was beneficial for epistemic resolution; this
diagnostic classification is not performance evidence. Artifact and leakage
validation passed.

## Sensitivity Contract

`configs/craft/experiments/gemma4_12b_clarify_policy_sensitivity.yaml` retains
the declared `oracle_n in {1,3,5}` candidate-count sensitivity and 20/30-turn
horizon sensitivity. A 30-turn run reports progress@20. Sensitivity runs do not
replace the official 20-turn result.

## Bounded Matrix Result

The checkpointed execution completed all three post-lifecycle conditions for
structures 0 through 9 and seeds 1, 3, and 5. These 30 matched pairs per
condition form the bounded analysis set. Structure 10 seed 1 completed for all
three conditions but is supplementary because the rest of that matched
structure did not complete.

The four comparisons against V0 use a prespecified Bonferroni family and a
98.75% two-way cluster bootstrap interval:

| Condition | Mean paired difference | Adjusted interval | Granted claim |
|---|---:|---:|---|
| V1 Clarify disabled | +0.00642 | [0.00000, +0.01764] | diagnostic |
| Current Clarify | +0.00081 | [-0.01280, +0.01550] | diagnostic |
| Budgeted Clarify | +0.02047 | [-0.00163, +0.05445] | diagnostic |
| V4 value of information | +0.00695 | [-0.01130, +0.03119] | diagnostic |

No adjusted interval establishes a favorable performance effect. The bounded
post-lifecycle diagnostics are especially unfavorable for current Clarify:

| Condition | Runs | Physical actions | Clarify | Beneficial | Neutral | Failed | Builder fallback |
|---|---:|---:|---:|---:|---:|---:|---:|
| Current Clarify | 30 | 414 | 186 | 1 | 2 | 183 | 0 |
| Budgeted Clarify | 30 | 558 | 42 | 2 | 39 | 1 | 21 |
| V4 value of information | 30 | 600 | 0 | 0 | 0 | 0 | 31 |

All 90 analysis runs passed artifact, leakage, clean-provenance, and invalid
action checks. Detailed inputs, reports, and aggregate diagnostics are under
`docs/benchmarks/evidence/craft_issue_370/`.

## Prespecified Stop

The declared matrix contained 180 checkpoints. Execution stopped with 93
completed checkpoints, one failed checkpoint, and 86 unstarted checkpoints.
Current Clarify at structure 10 seed 3 exceeded the 1800-second runtime limit
twice. No orphan process remained, and no downstream condition was started
after either timeout. The user selected the prespecified 30-minute limit over a
longer retry. The three completed structure 10 seed 1 runs are excluded from the
matched analysis rather than mixed into an unbalanced comparison.

This stop makes the result bounded and prevents a performance claim. It does
not invalidate the lifecycle integration result or the policy diagnostics.
`docs/benchmarks/evidence/craft_issue_370/remaining_matrix_status.json` records
the exact accounting and attempt identifiers.

## Policy Recommendation

Keep current Clarify disabled. It consumed 31% of the bounded turn budget and
98.4% of its outcomes failed. Keep budgeted Clarify disabled by default: its
throughput control worked, but 92.9% of its outcomes were neutral and its
adjusted interval crossed zero. Keep V4 opt-in and provisional; it preserved
physical throughput but did not establish a favorable adjusted effect. Do not
use the historical pre-budget throughput-fix result as evidence for the new
budgeted condition.

## Verification

```bash
python -m benchmarks.craft.validate_comparison_configs \
  --baseline configs/craft/eval_gemma4_12b_ollama.yaml \
  --treatment configs/craft/eval_gemma4_12b_ollama_dual_dag_clarify_throughput_fix.yaml \
  --skip-runtime-asset-validation
pytest -q benchmarks/craft/tests
```
