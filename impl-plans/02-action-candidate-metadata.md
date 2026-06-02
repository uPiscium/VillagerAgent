# Phase 2: Action Candidate Metadata

## Goal

Add structured metadata to Builder candidate actions showing which Director claims support or conflict with each action.

This phase still avoids behavior changes. It records action grounding quality so later gated clarification can use measured confidence instead of prompt-only heuristics.

## Prerequisite

Phase 1 must provide turn-level epistemic metadata:

- observed facts
- reported claims
- public facts
- provenance

## Non-Goals

- Do not change which candidate the Builder chooses.
- Do not force `CLARIFY` yet.
- Do not require full graph objects.

## New Data Contracts

Create `benchmarks/craft/dual_dag/action_candidates.py`.

```python
@dataclass
class ActionCandidateNode:
    node_id: str
    action_type: str
    action: dict
    state: str
    confidence: float
    supported_by: list[str]
    conflicts_with: list[str]
    required_evidence: list[str]
    metadata: dict
```

Allowed `action_type` values:

- `place_block`
- `remove_block`
- `clarify`
- `wait_for_evidence`

Allowed `state` values:

- `blocked`
- `candidate`
- `executable`
- `executed`
- `invalidated`

## Implementation Steps

### 1. Convert oracle moves to candidates

In `benchmarks/craft/craft_env_adapter.py`, `_builder_action` already gets `oracle_moves`.

Add helper:

```python
def _action_candidates_from_oracle_moves(
    *,
    oracle_moves: list[dict] | None,
    reported_claims: dict[str, dict],
    turn_index: int,
) -> list[ActionCandidateNode]:
    ...
```

For each oracle move:

- Create one `ActionCandidateNode`.
- Compute `supported_by` and `conflicts_with` with simple lexical matching.
- Set `confidence` using a deterministic scoring rule.

Initial scoring rule:

```text
support_score = number of claims mentioning same color/block or relative position
conflict_score = number of claims mentioning incompatible color/block for same visible area
confidence = clamp(0.5 + 0.15 * support_score - 0.2 * conflict_score, 0.0, 1.0)
```

Keep lexical matching simple in this phase.

### 2. Attach candidates to Builder action metadata

When Builder returns parsed action, attach:

```json
"_action_candidate_metadata": {
  "candidate_count": 1,
  "chosen_candidate_id": "action:turn:1:0",
  "chosen_confidence": 0.8,
  "claim_support_count": 2,
  "claim_conflict_count": 0,
  "candidates": [...]
}
```

If the Builder output matches an oracle candidate, mark that candidate as chosen.

If fallback is used, still attach candidate metadata and set:

```json
"chosen_by": "oracle_fallback"
```

If no oracle moves exist, create one candidate from the parsed action with lower confidence and no physical-validity guarantee.

### 3. Extend result conversion

Modify `benchmarks/craft/result_converter.py` to aggregate:

- `mean_action_confidence`
- `claim_support_count`
- `claim_conflict_count`
- `candidate_count`
- `fallback_chosen_by_oracle_count`

Modify `benchmarks/craft/report.py` to include:

- `mean_action_confidence`
- `mean_claim_support_count`
- `mean_claim_conflict_count`
- `mean_candidate_count`

### 4. Store raw candidates

In `turns.jsonl`, keep full per-turn candidate metadata.

In `metrics.csv`, only store aggregate per-game values.

## Tests

Add `benchmarks/craft/tests/test_action_candidate_metadata.py`.

Required tests:

- Oracle moves become candidate nodes.
- Candidate IDs are deterministic.
- Chosen candidate is detected when Builder parsed action matches oracle move.
- Fallback action preserves candidate metadata.
- Confidence is bounded between 0.0 and 1.0.
- Report aggregates candidate/support/conflict counts.

## Validation

Run:

```bash
.venv/bin/python -m pytest benchmarks/craft/tests
.venv/bin/python -m benchmarks.craft.run --config configs/craft/eval_qwen_ollama.yaml --structure 0 --turns 1
.venv/bin/python -m benchmarks.craft.report --runs craft_eval_qwen_ollama --output result/craft/action_candidate_smoke.csv
```

Manual checks:

- `turns.jsonl` contains `_action_candidate_metadata` inside `builder_action`.
- `comparison` report includes action confidence fields.
- Existing fallback metrics still work.

## Acceptance Criteria

- Metadata is available for every Builder turn.
- No Builder behavior change.
- Existing qwen batch still runs.
- No partial-information leakage.
