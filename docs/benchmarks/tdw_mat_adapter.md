# TDW-MAT Adapter

<!-- benchmark-result: tdw-mat-issue372-fixture-policy-smoke-v1 -->

This document records the source audit, diagnostic subset, adapter boundary, and execution gates for issue #372. The fixture-backed smoke validates the repository integration contract; it is not a TDW Unity performance run.

## Source Audit

- Official repository: <https://github.com/UMass-Embodied-AGI/CoELA>
- Local submodule: `external/CoELA`
- Audited commit: `3e12dea925d735eefce33da71806ae9da6fcaf3f`
- TDW-MAT license: MIT, `external/CoELA/tdw_mat/LICENSE`
- Environment: `external/CoELA/tdw_mat/tdw-gym/tdw_gym.py`
- Evaluator: `external/CoELA/tdw_mat/tdw-gym/challenge.py`
- Official test scenarios: `external/CoELA/tdw_mat/dataset/dataset_test/test_env.json`

The official test set contains 24 episodes: scenes `2a` and `5a`, layouts `0_0` through `2_1`, and `food`/`stuff` tasks. Every episode uses seed `2824`. Official task progress is `check_goal()` transported target count divided by total target count. The paper names this Transport Rate.

## Diagnostic Subset

`configs/tdw_mat/subset.json` selects four official scenario declarations:

| Episode | Scene | Layout | Task | Container setting |
| ---: | --- | --- | --- | --- |
| 0 | `5a` | `0_0` | `food` | enough |
| 1 | `5a` | `0_1` | `food` | rare |
| 12 | `5a` | `0_0` | `stuff` | enough |
| 13 | `5a` | `0_1` | `stuff` | rare |

This fixes scene geometry while covering both task classes and both container regimes. It is a diagnostic subset and cannot support a full-benchmark claim.

## Adapter Boundary

`TDWMATAdapter` implements the common `BenchmarkAdapter` contract against the official API shape:

- reset: `reset(seed=..., options={scene, layout, task})`
- observations: `visible_objects`, `held_objects`, `oppo_held_objects`, `messages`, `valid`, and `current_frames`
- actions: move, turn, grasp, put-in, drop, and message action types `0..6`
- progress: `check_goal()`

RGB, depth, segmentation masks, raw controller state, object-manager state, and evaluator state are never copied into `DecisionContext`. Visible objects and held-object relations become `observed_fact` nodes. Messages become public `reported_claim` nodes. Task targets become `resolved_fact` nodes. Physical and communication choices become Action Candidate DAG nodes; grasp candidates are marked `uncertain_feasibility` because object visibility does not prove the official two-meter reach precondition.

Sending a message is an `InformationActionSpec`. Physical actions and communication actions appear separately in the smoke trace.

## Metrics

- Transport Rate and task success from `check_goal()`
- physical and communication action counts
- communication rate among attempted actions
- goal-relevant communication rate: fraction of sent messages mentioning an unresolved target name
- physical, communication, and total execution frames, with mean frames per action class
- invalid physical action count
- false-feasible action rate: actions proposed as physically available that the environment returns with `valid=False`

Goal-relevant communication rate is a transparent textual proxy, not causal communication utility. A real experiment should additionally compare matched communication-on/off runs before claiming utility.

## Fixture Smoke

Run the dependency-free adapter contract smoke:

```bash
git submodule update --init external/CoELA
just tdw-mat-smoke
```

The report is written to `result/tdw_mat/fixture_smoke.json`. It uses the official `5a/0_0/food/2824` declaration and official observation/action field shapes, then executes message, grasp, and drop actions through an injected deterministic environment. The report sets `smoke_type=fixture_contract` and `performance_claim=false`.

Inspect real-runtime readiness without launching TDW:

```bash
just tdw-mat-real-preflight
```

This writes `result/tdw_mat/real_preflight.json`. The report checks the pinned source tree, importability of `gym` and `tdw`, and availability of `DISPLAY`. The asset cache is reported separately because upstream can download it during first launch.

## Real Simulator Gate

The official standalone instructions recommend Python 3.9. The project bridge requires a separate Python 3.10 environment because the common benchmark package uses Python 3.10 typing features. It also requires TDW `1.11.23.5`, a Unity build/X server, and transport asset bundles. Upstream's pinned dependencies conflict with the project environment, including different NumPy, OpenAI, matplotlib, torch, and tenacity versions. Do not install them into the project `.venv`.

For the bridge, adapt the upstream setup using an isolated Python 3.10 environment:

```bash
cd external/CoELA/tdw_mat
conda create -n tdw_mat_bridge python=3.10
conda activate tdw_mat_bridge
pip install -e .
python demo/demo_scene.py
```

The upstream Python 3.9 command remains appropriate for standalone CoELA reproduction but cannot import this repository's common adapter package.

Once `just tdw-mat-real-preflight` reports `ready=true`, run the bounded bridge smoke from the project root while the separate environment is active:

```bash
just tdw-mat-real-smoke
```

`CoELATDWMATEnvironment` lazily imports upstream `tdw_gym.TDW`, runs upstream reset and step calls under the source working directory required by its relative dataset paths, and restores the process working directory after every call. The bounded smoke resets official episode `5a/0_0/food/2824`, creates the initial Dual-DAG projection, executes one communication action, records frame cost and progress, then closes the simulator. It is marked `real_simulator_one_step` and `performance_claim=false`.

## Issue 372 fixture evidence

The fixed subset contains the four declared episodes `0`, `1`, `12`, and `13`. The
comparison executes each episode under `baseline`, `communication_disabled`,
`current_communication`, and `value_of_information`, for 16 matched fixture episodes.

| Condition | Success | Transport | Steps | Communications | Communication utility | Precision | Recall | False feasible | False infeasible | Recovery | Info-to-progress latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 1.0 | 1.0 | 4.0 | 0.0 | 0.0 | 0.5 | 0.5 | 0.5 | 0.5 | 1.0 | n/a |
| communication disabled | 1.0 | 1.0 | 4.0 | 0.0 | 0.0 | 0.5 | 0.5 | 0.5 | 0.5 | 1.0 | n/a |
| current communication | 1.0 | 1.0 | 5.0 | 1.0 | 1.0 | 0.5 | 0.5 | 0.5 | 0.5 | 1.0 | 4.0 |
| value of information | 1.0 | 1.0 | 4.0 | 0.0 | 0.0 | 0.5 | 0.5 | 0.5 | 0.5 | 1.0 | n/a |

Current communication produced no transport gain against communication-disabled and
added one mean fixture step. Its utility proxy is 1.0 because the communicated fact was
later used, but that diagnostic does not establish task benefit. The VOI condition did
not communicate because its deterministic expected progress gain did not exceed the
frame cost. These fixture results validate the adapter, policy boundaries, metric
plumbing, and end-to-end comparison only; `performance_claim` is false.

Tracked evidence:

- `docs/benchmarks/evidence/tdw_mat_issue_372/fixture_smoke.json`
- `docs/benchmarks/evidence/tdw_mat_issue_372/fixture_comparison.json`
- `docs/benchmarks/evidence/tdw_mat_issue_372/real_preflight.json`

Immutable evidence release:

- Release: `benchmark-tdw-mat-issue372-fixture-policy-smoke-v1`
- URL: https://github.com/upiscium/VillagerAgent/releases/tag/benchmark-tdw-mat-issue372-fixture-policy-smoke-v1
- Archive SHA-256: `1ec03b46fe663557382eb4fe17ef4c6edecc9fd790b8fc3f1e337ef16cb6d48f`
- Manifest SHA-256: `a66598162f6d3c9bda4a8b7d94aed8d251c7b45072a65c53a0575c027894cb96`

The real preflight found the CoELA source tree but did not start Unity because the
isolated runtime lacks the Python `gym` package, the Python `tdw` package, and `DISPLAY`.
This bounded blocker does not invalidate fixture acceptance and no embodied simulator
claim is made. A real bounded episode remains optional follow-up evidence after those
external runtime requirements are supplied.

## Limitations

The fixture does not exercise Unity physics, navigation, perception, rendering, or
simulator timing. It verifies only the boundary that translates official TDW-MAT-shaped
observations into Dual-DAG facts and action candidates, translates a validated candidate
back into an official action dictionary, and records the requested diagnostics.
