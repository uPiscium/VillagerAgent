# Phase 3: Gated Clarification

## Goal

Use epistemic and action-candidate metadata to choose `CLARIFY` instead of placing/removing blocks when ambiguity is too costly.

This is the first behavior-changing Dual-DAG phase.

## Prerequisites

- Phase 1: Epistemic metadata exists.
- Phase 2: Action candidate support/conflict/confidence metadata exists.

## Non-Goals

- Do not implement full graph traversal.
- Do not replace Builder with VillagerAgent.
- Do not require multi-turn directed Q/A between Directors yet.

## Config Additions

Add optional config section:

```yaml
dual_dag:
  enabled: true
  gated_clarification:
    enabled: true
    min_action_confidence: 0.65
    max_conflict_count: 0
    clarify_on_large_block_span_uncertainty: true
    clarification_cost: 0.15
    mistake_cost_weight: 1.0
```

Default behavior must remain unchanged when `dual_dag.enabled` is false or absent.

Add configs:

- `configs/craft/eval_qwen_ollama_dual_dag.yaml`
- `configs/craft/single_director_qwen_ollama_dual_dag.yaml`
- `configs/craft/experiments/qwen_dual_dag_v1.yaml`

## Gating Rule

Initial deterministic rule:

```text
clarify_if = chosen_confidence < min_action_confidence
          or claim_conflict_count > max_conflict_count
          or required span/coordinate/layer is unresolved
```

Cost-aware rule for metadata:

```text
risk_score = (1 - chosen_confidence) * estimated_mistake_cost
clarify_if = risk_score > clarification_cost
```

Use deterministic rule for actual behavior in Phase 3. Store risk score as metadata for later tuning.

## Implementation Steps

### 1. Add gating helper

Create `benchmarks/craft/dual_dag/gating.py`.

```python
def should_clarify(
    *,
    candidate_metadata: dict,
    config: dict,
) -> tuple[bool, dict]:
    ...
```

Return:

```json
{
  "should_clarify": true,
  "reason": "low_action_confidence",
  "chosen_confidence": 0.42,
  "risk_score": 0.58,
  "thresholds": {...}
}
```

### 2. Apply gate in Builder action selection

Modify `CraftEnvAdapter._builder_action` after parsed/fallback candidate metadata is attached, before returning the action.

If gate fires:

```json
{
  "action": "clarify",
  "clarification": "The candidate action is ambiguous. Please clarify ...",
  "_gated_clarification": {...},
  "_action_candidate_metadata": {...}
}
```

Clarification text should not mention hidden target or oracle internals.

### 3. Add metrics

Extend result conversion/report:

- `clarification_count`
- `gated_clarification_count`
- `gated_clarification_rate`
- `mean_risk_score`
- `low_confidence_gate_count`
- `conflict_gate_count`

### 4. Add ablation configs

Compare:

- current qwen eval
- qwen eval with gated clarification
- single-director with gated clarification
- official comparable baseline

Manifest:

```yaml
experiment:
  name: craft_qwen_dual_dag_v1
  runs:
    - configs/craft/eval_qwen_ollama.yaml
    - configs/craft/eval_qwen_ollama_dual_dag.yaml
    - configs/craft/single_director_qwen_ollama_dual_dag.yaml
    - configs/craft/official_baseline.yaml
```

## Tests

Add `benchmarks/craft/tests/test_gated_clarification.py`.

Required tests:

- Gate does not fire when `dual_dag.enabled=false`.
- Gate fires when confidence is below threshold.
- Gate fires when conflict count is too high.
- Clarify action preserves candidate metadata.
- Clarify action does not include forbidden hidden state.
- Report counts gated clarifications.

## Validation

Run:

```bash
.venv/bin/python -m pytest benchmarks/craft/tests
.venv/bin/python -m benchmarks.craft.run --config configs/craft/eval_qwen_ollama_dual_dag.yaml --structure 0 --turns 1
.venv/bin/python -m benchmarks.craft.experiment --config configs/craft/experiments/qwen_dual_dag_v1.yaml
```

Manual checks:

- Gated config can produce `CLARIFY` when uncertainty is high.
- Non-gated config behavior remains unchanged.
- Progress, fallback rate, and clarification rate are visible in report.

## Acceptance Criteria

- Gating is config-controlled.
- Behavior-changing gate is test-covered.
- Reports show whether gains/losses come from clarification rather than hidden oracle fallback.
- No partial-information leakage.
