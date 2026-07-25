# PARTNR Adapter and Bounded Oracle Smoke

<!-- benchmark-result: partnr-issue378-bounded-oracle-smoke-v1 -->

This document records the implementation and bounded evidence for issue #378. It integrates the PARTNR observation, Tool/Skill, and evaluator boundaries with the common Dual-DAG schema. All evidence in this issue has `performance_claim=false`.

## Source and Scope

- Official source: <https://github.com/facebookresearch/partnr-planner>
- Source commit: `ddfff19f4b6c098a31edea4d19e7b75db72433c2`
- Dataset: official `val_mini`, SHA-256 `62d9aded9bb315ccdc22b3540f92a83c0c0ca593da798cf16bdba6ce5ecc57e1`
- HSSD source: <https://huggingface.co/datasets/hssd/hssd-hab>, branch `partnr`, commit `bdc2c6c1e2e96e5ab690e9a8555ebf1a16d39632`
- Runtime: isolated Python 3.9 with Habitat-Sim 0.3.3 headless and Bullet
- Official gate: first episode step-zero, then only the first four `val_mini` episodes
- Process limit: 1800 seconds for each official process

The source, runtime, and restricted datasets remain external. HSSD and other Habitat assets are not redistributed by the tracked evidence or managed bundle.

## Adapter Boundary

`PARTNRAdapter` accepts only actor-local entity observations, resolved Tool feedback, the public instruction, and Tool/Skill candidates. These values become common `ObservationRecord`, `ActionSpec`, `InformationActionSpec`, and `DecisionContext` values.

The Epistemic DAG contains observed entities, public instructions, resolved Tool feedback, and officially shared events. The Action Candidate DAG contains grounded Tool/Skill calls and their known requirements. Unknown reachability or receptacle state remains uncertain rather than being promoted to a known precondition.

Evaluator propositions, dependencies, constraints, `full_world_graph`, and private agent views are not copied into an agent-facing context. Canonical `TaskPercentComplete` and `TaskStateSuccess` values are consumed after execution for metrics only. `evaluator_snapshot()` remains a separate post-hoc interface.

## Commands

Run the dependency-free fixture:

```bash
just partnr-smoke
```

Use an isolated upstream-compatible Python and external source/data layout for official gates:

```bash
PARTNR_PYTHON=/path/to/isolated/python just partnr-real-preflight
PARTNR_PYTHON=/path/to/isolated/python just partnr-step-zero
PARTNR_PYTHON=/path/to/isolated/python just partnr-bounded-smoke
```

The preflight verifies the pinned source, Python 3.9 imports, submodules, dataset schema, output identity, an actual headless Habitat-Sim context, and materialization of all stage, rigid, opening, and articulated assets referenced by the bounded scenes. It fails closed before an official process starts.

## Fixture Result

The deterministic fixture executes `FindObjectTool`, `Navigate`, a failed `Pick`, a successful recovery `Pick`, and `Place`. It reaches fixture task success with one failed action and one recovered failure. This validates adapter plumbing and visibility boundaries without exercising Habitat-Sim.

## Official Gates

The step-zero verifier initialized episode `0` successfully. Its initial `TaskPercentComplete` and `TaskStateSuccess` were both 0.0, as expected before policy execution.

The bounded upstream `heuristic_full_obs` run completed episodes `0`, `1`, `2`, and `3` with no failed or missing records:

| Episode | TaskPercentComplete | TaskStateSuccess | Simulator steps | Runtime seconds |
| --- | ---: | ---: | ---: | ---: |
| 0 | 1.0 | 1.0 | 742 | 19.161 |
| 1 | 1.0 | 1.0 | 540 | 13.710 |
| 2 | 1.0 | 1.0 | 220 | 5.657 |
| 3 | 1.0 | 1.0 | 2293 | 60.149 |

This is the upstream scripted full-observation oracle, not a result for the repository's partial-observation decision policy. The four episodes are an integration gate and do not support benchmark-scale or comparative claims.

## Evidence

- `docs/benchmarks/evidence/partnr_issue_378/fixture_smoke.json`
- `docs/benchmarks/evidence/partnr_issue_378/real_preflight.json`
- `docs/benchmarks/evidence/partnr_issue_378/official_gates.json`

Build the managed evidence directory with:

```bash
python -m benchmarks.partnr.evidence_bundle --output result/partnr/issue_378_evidence
```

The managed bundle contains sanitized source/data identities, normalized episode metrics, explicit failed/missing accounting, provenance, and run-completion manifests. It excludes raw stdout/stderr, planner logs, restricted assets, credentials, private reasoning, and hidden evaluator state.

Immutable evidence release:

- Release: `benchmark-partnr-issue378-bounded-oracle-smoke-v1`
- URL: <https://github.com/upiscium/VillagerAgent/releases/tag/benchmark-partnr-issue378-bounded-oracle-smoke-v1>
- Archive SHA-256: `f2d1a2837f963b73f11d2008e91ab80b33ca578e29f227cdf65993ecd6555010`
- Manifest SHA-256: `f94a07d3d39c8119f091e5e6fd9cbe0f5b160acbd3213cff02ed2f0b8225e1a7`

## Limitations

The dependency-free fixture does not exercise simulator physics or perception. The official smoke covers only four prespecified episodes from one scene and uses full observability. No original-versus-Dual-DAG policy comparison, scale claim, statistical inference, or superiority claim is made.
