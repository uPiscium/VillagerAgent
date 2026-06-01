# Phase 4: Full Dual-DAG Runtime

## Goal

Promote Phase 1-3 metadata structures into explicit graph objects with update, query, and serialization APIs.

This phase turns the metadata-first system into a true Dual-DAG runtime while keeping the implementation CRAFT-local.

## Prerequisites

- Epistemic metadata extraction exists.
- Action candidate metadata exists.
- Gated clarification exists and is config-controlled.

## Non-Goals

- Do not integrate Minecraft execution in this phase.
- Do not modify `external/CRAFT`.
- Do not expose hidden target state.

## Module Layout

Create package:

```text
benchmarks/craft/dual_dag/
  __init__.py
  epistemic.py
  epistemic_extractor.py
  action_candidates.py
  gating.py
  runtime.py
  serialization.py
```

## Runtime API

`runtime.py`:

```python
class DualDAGRuntime:
    def __init__(self, *, director_ids: list[str], config: dict):
        ...

    def reset(self) -> None:
        ...

    def update_private_observation(
        self,
        *,
        director_id: str,
        turn_index: int,
        private_view: CraftPrivateView,
    ) -> None:
        ...

    def update_public_state(
        self,
        *,
        turn_index: int,
        public_state: CraftPublicState,
    ) -> None:
        ...

    def add_reported_claim(
        self,
        *,
        director_id: str,
        turn_index: int,
        message: str,
    ) -> None:
        ...

    def build_action_candidates(
        self,
        *,
        turn_index: int,
        oracle_moves: list[dict] | None,
        parsed_action: dict | None,
    ) -> list[ActionCandidateNode]:
        ...

    def evaluate_gate(self, *, chosen_action: dict) -> dict:
        ...

    def snapshot(self) -> dict:
        ...
```

## Graph Representation

Use plain Python dictionaries/lists first. Avoid adding a graph library unless needed.

Internal state:

```python
self.epistemic_nodes: dict[str, EpistemicNode]
self.epistemic_edges: list[EpistemicEdge]
self.action_nodes: dict[str, ActionCandidateNode]
self.action_edges: list[dict]
```

Node IDs must be deterministic:

```text
observed:{director_id}:{turn_index}:{row}:{column}
public:builder_action:{turn_index}:{index}
claim:{director_id}:{turn_index}
hypothesis:{turn_index}:{hash}
action:{turn_index}:{index}
```

## Integration Points

### Director controller

Modify `VillagerCraftControllerAdapter`:

- Instantiate or receive `DualDAGRuntime` when enabled.
- Update private observation before prompt creation.
- Include DAG summary in metadata, not full hidden state.

### CRAFT env adapter

Modify `CraftEnvAdapter`:

- Create one runtime per structure/game.
- Add reported claims after Director outputs.
- Build action candidates before Builder action return.
- Evaluate gated clarification when enabled.
- Store `dual_dag_snapshot` in turn metadata.

### Result converter

Add artifacts:

```text
normalized/dual_dag_summary.json
normalized/dual_dag_nodes.jsonl
normalized/dual_dag_edges.jsonl
```

These files must not contain hidden target structure or raw private views from other Directors.

## Serialization

`serialization.py` should provide:

```python
def node_to_dict(node) -> dict: ...
def edge_to_dict(edge) -> dict: ...
def snapshot_to_dict(runtime: DualDAGRuntime) -> dict: ...
```

Serialization rules:

- Include provenance.
- Include node type and confidence.
- Do not include raw private views except the active Director's own metadata in that Director's metadata context.
- Do not include target structure or oracle moves.

## Tests

Add `benchmarks/craft/tests/test_dual_dag_runtime.py`.

Required tests:

- Runtime creates deterministic node IDs.
- Runtime reset clears nodes and edges.
- Public Builder actions become public facts.
- Reported claims are not resolved facts by default.
- Action candidates link to supporting claims.
- Snapshot rejects hidden keys.
- Serialized artifacts do not contain forbidden keys.

## Validation

Run:

```bash
.venv/bin/python -m pytest benchmarks/craft/tests
.venv/bin/python -m benchmarks.craft.run --config configs/craft/eval_qwen_ollama_dual_dag.yaml --structure 0 --turns 1
.venv/bin/python -m benchmarks.craft.experiment --config configs/craft/experiments/qwen_dual_dag_v1.yaml
```

Manual checks:

- `normalized/dual_dag_summary.json` exists.
- Node/edge counts match report metrics.
- No hidden target appears in DAG artifacts.

## Acceptance Criteria

- Dual-DAG runtime is explicit and test-covered.
- Metadata-first behavior from Phases 1-3 is preserved.
- Gating can query graph state instead of ad hoc turn metadata.
- CRAFT-local implementation is ready to be mapped to VillagerAgent Task DAG later.
