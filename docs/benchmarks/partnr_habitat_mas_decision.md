# PARTNR and Habitat-MAS Go/No-Go Decision

This document resolves issue #374. It surveys both large Habitat-based benchmark
stacks, sketches their Dual-DAG boundaries, and selects exactly one for the first
implementation. This is a source and design audit, not simulator evidence or a
performance result.

## Decision

**GO: PARTNR. DEFER: Habitat-MAS.**

PARTNR is the first implementation target because its long-horizon household tasks,
explicit proposition and temporal-constraint evaluator, partial-observation world
model, and maintained `val_mini` path directly test the next missing claim: planning
and belief tracking at scale. Habitat-MAS has high value for a later heterogeneous
embodiment claim, but its early-release stack adds a modified Habitat fork, separately
distributed robot/task data, Matterport3D access, and credential integration before a
reproducible smoke can run.

The machine-readable decision is
`configs/benchmark_selection/issue_374.json`. Neither benchmark is integrated by this
issue, and `performance_claim` is false.

## Audited Sources

The audit was performed on 2026-07-24 against these immutable source revisions:

| Benchmark | Official source | Audited revision | Code license |
| --- | --- | --- | --- |
| PARTNR | [facebookresearch/partnr-planner](https://github.com/facebookresearch/partnr-planner) | `ddfff19f4b6c098a31edea4d19e7b75db72433c2` | MIT |
| Habitat-MAS / EMOS | [SgtVincent/EMOS](https://github.com/SgtVincent/EMOS), branch `embodied_mas` | `e9501db45d634b087bf5d1a14228266685e8feeb` | MIT |

Code licensing does not grant scene-data rights. HSSD, OVMM, Matterport3D, model
weights, and other downloaded assets retain their own terms. Any implementation must
record the exact asset source and accepted terms without redistributing restricted
assets.

Primary references:

- [PARTNR README](https://github.com/facebookresearch/partnr-planner/blob/ddfff19f4b6c098a31edea4d19e7b75db72433c2/README.md)
- [PARTNR installation](https://github.com/facebookresearch/partnr-planner/blob/ddfff19f4b6c098a31edea4d19e7b75db72433c2/INSTALLATION.md)
- [PARTNR measures](https://github.com/facebookresearch/partnr-planner/blob/ddfff19f4b6c098a31edea4d19e7b75db72433c2/habitat_llm/agent/env/measures.py)
- [PARTNR evaluator](https://github.com/facebookresearch/partnr-planner/blob/ddfff19f4b6c098a31edea4d19e7b75db72433c2/habitat_llm/agent/env/evaluation/evaluation_functions.py)
- [EMOS project page](https://emos-project.github.io/)
- [Habitat-MAS package instructions](https://github.com/SgtVincent/EMOS/blob/e9501db45d634b087bf5d1a14228266685e8feeb/habitat-mas/README.md)
- [EMOS paper](https://arxiv.org/abs/2410.22662)

## PARTNR Setup Survey

### Runtime

- Use an isolated Conda/Mamba environment. Upstream pins Python `3.9.2` and CMake
  `3.14.0`; these dependencies must not be installed into this repository's runtime.
- Initialize recursive submodules, including the upstream Habitat-Lab and
  `transformers-CFG` revisions.
- Install PyTorch `2.4.1`, torchvision `0.19.1`, torchaudio `2.4.1`, and the matching
  CUDA build documented upstream. Install Habitat-Sim `0.3.3` with Bullet and the
  headless variant on non-GUI hosts.
- Install the vendored Habitat-Lab, Habitat Baselines, `transformers-CFG`, PARTNR
  requirements, and `partnr-planner` itself as editable packages in that isolated
  environment.
- LLM baselines add model weights or API credentials. The first gate avoids them by
  using `heuristic_full_obs`; neural skills and local Llama inference are later gates.

### Data

- Download Habitat rearrangement assets, Spot arm/humanoid assets, and Habitat 3
  episodes with the Habitat-Sim downloader.
- Fetch OVMM objects and the `partnr` HSSD scene branch through Git LFS.
- Fetch PARTNR episode splits and optional learned-skill checkpoints through Git LFS.
- Preserve upstream's `data/versioned_data` plus symlink layout. Verify the small
  `val_mini` split before any matrix run.
- The upstream CI fixture `hssd-partnr-ci` is suitable for installation tests but is
  not a substitute for benchmark episodes.

### Execution and Evaluation

- `habitat_llm.examples.verify_episodes` validates dataset loadability and step-zero
  state before rollouts.
- `habitat_llm.examples.planner_demo` supplies centralized, decentralized, single-agent,
  and heuristic baseline entry points.
- `EnvironmentInterface`, Perception, `WorldGraph`, Planner, Tool, Skill, and
  `EvaluationRunner` are the natural integration seams.
- `AutoEvalPropositionTracker` records proposition state over time.
  `TaskConstraintValidation` applies same/different argument, temporal DAG, and terminal
  constraints. `TaskPercentComplete` and `TaskStateSuccess` are the canonical progress
  and success metrics; `TaskEvaluationLog` supports failure analysis.
- Evaluation propositions, dependencies, constraints, and simulator predicate state
  are evaluator data. They must not enter agent-facing observations unless the official
  task interface independently reveals the same fact.

## Habitat-MAS Setup Survey

### Runtime

- Clone the official EMOS repository's `embodied_mas` branch. It is a modified
  Habitat-Lab fork rather than a package layered on current upstream Habitat-Lab.
- Use an isolated Python `3.9` and CMake `3.14` environment with Habitat-Sim `0.3.1`
  and Bullet. Headless server instructions require an NVIDIA driver and the headless
  Habitat-Sim build.
- Install the fork's Habitat-Lab and Habitat Baselines plus `habitat-mas`.
- Extra requirements include `networkx==3.2`, LangChain, OpenAI, `urchin`,
  `pygame==2.0.1`, `pybullet==3.0.4`, and `gym==0.25.2`.
- Upstream labels the repository an early release with cleanup and documentation still
  in progress. Its instructions ask users to place an API key in source code; a project
  integration must replace that with environment-based secret injection and must never
  persist the key in artifacts.

### Data

- Download Habitat 3/HSSD, humanoid, Spot arm, YCB, benchmark, and rearrangement assets.
- Matterport3D scenes are required for mobility and multi-floor rearrangement. Access
  requires accepting Matterport3D terms and using its separate downloader.
- Robot URDF/mesh/configuration files and Habitat-MAS episode data are distributed
  separately through a Google Drive folder and must be merged into the expected
  `data/robots` and `data/datasets` layout.
- The benchmark covers Fetch, Stretch, Spot, and DJI drone embodiments over perception,
  mobility, manipulation, and comprehensive rearrangement tasks.

### Execution and Evaluation

- Habitat Baselines configurations launch each task family. Agents operate through
  high-level actions such as navigation, pick, place, wait, and request messaging.
- `RobotResume` exposes mobility, sensor/viewpoint, and manipulation-workspace
  capabilities derived from URDF and kinematic helpers.
- The paper reports episode success, PDDL sub-goal success, token use, and simulator
  steps. These metrics need an artifact audit against the released code before they
  become claim-bearing fields.
- The simulator assumes ideal semantic scene reconstruction and simplifies low-level
  grasping. Results must therefore be described as benchmark-level capability reasoning,
  not general real-robot performance.

## Dual-DAG Adapter Sketches

Both adapters must implement the common `BenchmarkAdapter` contract and keep the
simulator/evaluator behind the same visibility boundary used by existing benchmarks.

### PARTNR

| Common interface | PARTNR mapping |
| --- | --- |
| `reset` | Construct `EnvironmentInterface`/evaluation runner for one declared episode; return official agent IDs, instruction, split, scene ID, and seed. |
| `capabilities` | Derive physical and information action types from each Agent's configured Tools and Skills. |
| `get_observation` | Convert only the actor's Perception/WorldGraph delta and official task instruction into `ObservedFact` records with actor-local visibility. |
| `get_public_observation` | Include only environment events or communication officially visible to both agents. Do not copy evaluator propositions. |
| `get_legal_actions` | Map grounded Tool/Skill invocations into `ActionCandidate` nodes with target entity, actor, known prerequisites, and uncertainty. |
| `decision_context` | Expose actor-visible facts, candidates, budget, and public events; validate with `validate_agent_facing()`. |
| `execute_action` | Dispatch one validated Tool/Skill through the evaluation runner and record success, error, simulator steps, and world-graph changes. |
| `execute_information_action` | Dispatch only officially supported query or communication actions and account for their step/token cost. |
| `task_progress` | Read `TaskPercentComplete` only after the step; never expose its underlying predicate state to the policy. |
| `final_metrics` | Record `TaskStateSuccess`, percent complete, steps, failure explanation, action validity, recovery, communication cost, and provenance. |

The Epistemic DAG represents local detections, reported claims, resolved tool feedback,
and public task facts. The Action Candidate DAG represents grounded tools/skills and
their object, room, receptacle, temporal, and actor dependencies. PARTNR's evaluation
proposition DAG is useful for post-hoc scoring but is not the agent's Epistemic DAG.

### Habitat-MAS

| Common interface | Habitat-MAS mapping |
| --- | --- |
| `reset` | Load one declared task/scene/robot-team episode from the modified Habitat fork. |
| `capabilities` | Convert each pinned Robot Resume into mobility, floor traversal, sensor height/type, arm workspace, and end-effector constraints. |
| `get_observation` | Convert actor-local scene text, detections, state, and action feedback into local facts; preserve sensor/viewpoint limits. |
| `get_public_observation` | Include the shared instruction and only messages actually exchanged during group discussion or `send_request`. |
| `get_legal_actions` | Map navigation, perception, pick/place, request, and wait calls into actor-specific candidates. |
| `decision_context` | Join visible facts with capability-grounded candidates without exposing PDDL goal predicates or hidden scene state. |
| `execute_action` | Dispatch through Habitat-MAS action/skill interfaces and support asynchronous per-robot completion. |
| `execute_information_action` | Represent discussion, request, sensing, and capability queries with token and simulator-step costs. |
| `task_progress` | Consume released PDDL sub-goal progress as evaluator-only feedback after execution. |
| `final_metrics` | Record success/sub-goal rate, token use, simulator steps, feasibility confusion, assignment corrections, and recovery. |

The key Action Candidate edges are `requires_capability`, `requires_reachability`,
`requires_viewpoint`, `requires_workspace`, `requires_object_state`, and
`assigned_to`. Capability claims should be grounded in pinned Robot Resume numeric
fields rather than free-form LLM summaries alone.

## Cost and Research Value

Scores are 1 (weak) through 5 (strong). Weighted totals are normalized to 100 and
express prioritization, not measured benchmark performance.

| Criterion | Weight | PARTNR | Habitat-MAS | Rationale |
| --- | ---: | ---: | ---: | --- |
| Complements current claims | 25 | 5 | 4 | PARTNR adds scale/proposition evaluation; Habitat-MAS deepens embodiment already probed by TDW-MAT. |
| Dual-DAG interface fit | 20 | 5 | 4 | PARTNR already separates world graph, tools, and proposition scoring. |
| Reproducible setup path | 20 | 3 | 2 | Both are large; PARTNR has maintained install/CI/`val_mini` paths while Habitat-MAS is an early release. |
| Evaluator maturity | 15 | 5 | 3 | PARTNR exposes source-level proposition, constraint, progress, success, and explanation measures. |
| Data accessibility | 10 | 4 | 2 | Habitat-MAS additionally requires MP3D approval and separately hosted benchmark data. |
| Implementation risk | 10 | 3 | 1 | Habitat-MAS requires a modified fork, heterogeneous asynchronous control, and credential cleanup. |
| **Weighted total** | **100** | **86** | **59** | **Select PARTNR only.** |

## First PARTNR Implementation Gate

Follow-up issue [#378](https://github.com/upiscium/VillagerAgent/issues/378)
tracks the selected implementation. It must remain smaller than a paper matrix:

1. Pin the audited PARTNR revision in an external source declaration and create a
   separate Python 3.9 environment. Do not modify the project environment.
2. Add a dependency-free fixture for observation, Tool/Skill, proposition-result, and
   visibility mappings before installing Habitat assets.
3. Implement preflight checks for source revision, imports, headless Habitat-Sim, data
   symlinks, `val_mini`, and output directory identity.
4. Verify one official `val_mini` episode at step zero, then run at most four episodes
   with `heuristic_full_obs` under finite episode and wall-clock limits.
5. Persist managed artifacts containing config, source/data identities, traces,
   `TaskPercentComplete`, `TaskStateSuccess`, and explicit failed/missing accounting.
6. Only after the oracle/heuristic gate passes, define matched original versus Dual-DAG
   planner conditions. A fixture or bounded smoke cannot support a scale or performance
   claim.

## Deferred Habitat-MAS Reconsideration Gate

Reconsider Habitat-MAS only after the PARTNR adapter and bounded official smoke are
stable, or when the next paper claim explicitly requires heterogeneous embodiments.
Before implementation, require all of the following:

- a pinned and locally reproducible robot/episode data package with recorded terms;
- a credential path that uses environment variables rather than source edits;
- a headless smoke that avoids unrestricted display and process lifetimes;
- audited PDDL success/sub-goal metrics in the released code;
- one task family selected for the first smoke rather than all four;
- explicit justification for the added claim beyond PARTNR and TDW-MAT.

Until those gates pass, Habitat-MAS is a **no-go for first implementation**, not a
rejection as a future benchmark.
