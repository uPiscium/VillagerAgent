# Dual-DAG Implementation Plans

This directory breaks `proposal.md` into implementation-sized plans for the CRAFT integration.

The plans are ordered so each PR can deliver a measurable artifact without requiring the full Dual-DAG system at once.

## Plan Order

1. `01-epistemic-metadata.md`
   - Add structured epistemic metadata extracted from Director observations and public messages.
   - No behavior change required.

2. `02-action-candidate-metadata.md`
   - Attach claim support/conflict/confidence metadata to Builder candidate actions.
   - Extend normalized outputs and reports.

3. `03-gated-clarification.md`
   - Use epistemic/action metadata to choose `CLARIFY` when uncertainty is too costly.
   - Add ablation configs and metrics.

4. `04-dual-dag-runtime.md`
   - Promote metadata structures into explicit graph objects.
   - Prepare the bridge to VillagerAgent Task DAG and later Minecraft runtime.

## Shared Constraints

- Do not expose `target_structure` to Directors.
- Do not expose oracle moves to Directors.
- Do not expose another Director's raw private view to a Director.
- Treat other Directors' utterances as `ReportedClaim`, not as world truth.
- Preserve provenance for every extracted fact, claim, hypothesis, and action support edge.
- Keep initial implementation CRAFT-local under `benchmarks/craft/`.
- Prefer metadata-first changes before behavior-changing gates.

## Validation Baseline

Each phase should keep these commands passing unless explicitly documented otherwise:

```bash
.venv/bin/python -m pytest benchmarks/craft/tests
.venv/bin/python -m benchmarks.craft.run --config configs/craft/eval_qwen_ollama.yaml --structure 0 --turns 1
.venv/bin/python -m benchmarks.craft.run --config configs/craft/single_director_qwen_ollama.yaml --structure 0 --turns 1
```

Behavior-changing phases should additionally run:

```bash
.venv/bin/python -m benchmarks.craft.experiment --config configs/craft/experiments/qwen_batch_v1.yaml
```
