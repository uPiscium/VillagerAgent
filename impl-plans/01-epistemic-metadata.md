# Phase 1: Epistemic Metadata

## Goal

Add structured epistemic metadata for CRAFT Director turns without changing Builder behavior.

This phase externalizes what each Director can observe, what it claims publicly, and what remains uncertain. It is intentionally metadata-only so it can be validated for leakage and usefulness before it affects action selection.

## Non-Goals

- Do not implement graph traversal yet.
- Do not change Builder action choice.
- Do not add cross-Director private view sharing.
- Do not convert `ReportedClaim` directly into `ResolvedFact`.

## New Data Contracts

Add these dataclasses to `benchmarks/craft/craft_protocol.py` or a new `benchmarks/craft/dual_dag/epistemic.py` module.

```python
@dataclass
class Provenance:
    source: str
    director_id: str | None
    turn_index: int
    visibility: str

@dataclass
class EpistemicNode:
    node_id: str
    node_type: str
    content: dict
    confidence: float
    provenance: Provenance

@dataclass
class EpistemicEdge:
    source_id: str
    target_id: str
    edge_type: str
    metadata: dict

@dataclass
class EpistemicTurnMetadata:
    observed_facts: list[EpistemicNode]
    public_facts: list[EpistemicNode]
    reported_claims: list[EpistemicNode]
    hypotheses: list[EpistemicNode]
    edges: list[EpistemicEdge]
```

Allowed `node_type` values:

- `observed_fact`
- `public_fact`
- `reported_claim`
- `hypothesis`
- `resolved_fact`

Allowed `edge_type` values:

- `supports`
- `conflicts_with`
- `derived_from`
- `resolved_by`
- `requires_confirmation_from`

## Implementation Steps

### 1. Add extraction module

Create `benchmarks/craft/dual_dag/epistemic_extractor.py`.

Functions:

```python
def observed_facts_from_private_view(
    *,
    director_id: str,
    turn_index: int,
    private_view: CraftPrivateView,
) -> list[EpistemicNode]:
    ...

def public_facts_from_state(
    *,
    turn_index: int,
    public_state: CraftPublicState,
) -> list[EpistemicNode]:
    ...

def reported_claim_from_message(
    *,
    director_id: str,
    turn_index: int,
    message: str,
) -> EpistemicNode:
    ...
```

Initial extractor should be deterministic and simple:

- For private view rows, create one `observed_fact` per visible cell.
- Use content fields: `row`, `column`, `relative_vertical`, `relative_horizontal`, `color`, `size`, `size_label`.
- For Builder actions, create one `public_fact` per action.
- For Director message, create one `reported_claim` containing raw message and optional parsed keywords.

Do not use LLM extraction in Phase 1.

### 2. Attach metadata to Director outputs

Modify `benchmarks/craft/villager/controller_adapter.py`.

For each active Director turn:

- Build observed facts from that Director's private view.
- Build public facts from public state.
- Store them under `metadata["epistemic"]`.

Suggested metadata shape:

```json
{
  "epistemic": {
    "observed_facts": [...],
    "public_facts": [...],
    "hypotheses": [],
    "edges": []
  }
}
```

For inactive Directors in single-director ablation, set:

```json
{
  "epistemic": {
    "observed_facts": [],
    "public_facts": [],
    "hypotheses": [],
    "edges": []
  }
}
```

### 3. Attach reported claims after Director messages

Modify `benchmarks/craft/craft_env_adapter.py` around the turn assembly.

After `messages` is built, create `reported_claim` nodes from each public message and store them in turn-level metadata:

```json
"epistemic_claims": {
  "D1": {...},
  "D2": {...},
  "D3": {...}
}
```

Do not feed another Director's reported claims back into private prompts in this phase. They are only public turn metadata.

### 4. Normalize and report counts

Modify `benchmarks/craft/result_converter.py`.

Add summary/runtime fields:

- `observed_fact_count`
- `reported_claim_count`
- `hypothesis_count`

Add metrics CSV fields with the same names.

Modify `benchmarks/craft/report.py` to include these columns.

## Tests

Add `benchmarks/craft/tests/test_epistemic_metadata.py`.

Required tests:

- Extracts one observed fact per private-view visible cell.
- Observed facts include `director_id`, `turn_index`, row, column, color, size.
- Reported claim stores source Director and raw message.
- Extraction does not include `target_structure`, `oracle_moves`, `all_private_views`, `hidden_spans`, or `hidden_labels`.
- Single-director inactive D2/D3 have empty epistemic metadata.
- Normalized summary/report include epistemic counts.

## Validation

Run:

```bash
.venv/bin/python -m pytest benchmarks/craft/tests
.venv/bin/python -m benchmarks.craft.run --config configs/craft/eval_qwen_ollama.yaml --structure 0 --turns 1
.venv/bin/python -m benchmarks.craft.report --runs craft_eval_qwen_ollama --output result/craft/epistemic_smoke.csv
```

Manual checks:

- `result/craft/craft_eval_qwen_ollama/normalized/turns.jsonl` contains epistemic metadata.
- No forbidden hidden payload appears in saved prompt files.
- `leakage_passed` remains true.

## Acceptance Criteria

- Tests pass.
- CRAFT qwen smoke run succeeds.
- Epistemic metadata is present in raw/normalized turn artifacts.
- No behavior change in Builder fallback policy.
- No partial-information leakage.
