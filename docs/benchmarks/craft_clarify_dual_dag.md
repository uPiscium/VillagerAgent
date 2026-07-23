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

## Current Recommendation

Issue #291 found no favorable adjusted performance interval for V1 or V4 over
V0, no natural retrieval activation, and two neutral V4 Clarify outcomes. Until
the post-lifecycle current/budgeted/V4 matrix is complete, keep current Clarify
disabled for paper-facing use and treat V4 as opt-in/provisional. Do not use the
historical pre-budget throughput-fix result as evidence for the new budgeted
condition.

## Verification

```bash
python -m benchmarks.craft.validate_comparison_configs \
  --baseline configs/craft/eval_gemma4_12b_ollama.yaml \
  --treatment configs/craft/eval_gemma4_12b_ollama_dual_dag_clarify_throughput_fix.yaml \
  --skip-runtime-asset-validation
pytest -q benchmarks/craft/tests
```
