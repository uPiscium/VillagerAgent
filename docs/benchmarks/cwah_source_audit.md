# C-WAH Source Audit

This audit gates C-WAH implementation. Do not claim C-WAH support or add a C-WAH adapter until the selected upstream and its constraints are accepted.

## Recommendation

Use `UMass-Embodied-AGI/CoELA` as the candidate upstream for Communicative Watch-And-Help, specifically its `cwah/` subtree.

Status: conditional go for a symbolic-observation adapter.

Conditions before implementation:

- Accept the `cwah/LICENSE.md` terms: Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International.
- Add the upstream as a pinned submodule only after license acceptance.
- Treat the imported repo as external benchmark code and do not edit it directly.
- Use only `cwah/` symbolic APIs for the initial adapter; do not depend on visual observation paths.
- Document the required VirtualHome `wah` branch and simulator executable in `external/README.md` when the submodule is added.

## Candidate Sources

| Candidate | Repository | Commit checked | Result |
|---|---|---:|---|
| Communicative Watch-And-Help implementation | `https://github.com/UMass-Embodied-AGI/CoELA` | `3e12dea925d735eefce33da71806ae9da6fcaf3f` | Best match. Contains `cwah/` with communication, symbolic observation scripts, task dataset, and evaluation runners. |
| Original Watch-And-Help implementation | `https://github.com/xavierpuigf/watch_and_help` | `21f5ab7416111d22a2ebc8d0d2427c799d01398a` | Useful lineage and VirtualHome basis, but not sufficient as C-WAH because message/communication action support is not the benchmark focus. |
| Online Watch-And-Help / NOPA | `https://github.com/xavierpuigf/online_watch_and_help` | `92ef005e5e6d9085727b8c054d9aaa87586fe4de` | Not selected. README states dataset/model test support is coming soon, so it is not a usable benchmark source for this phase. |

## Selected Upstream

- Upstream: `UMass-Embodied-AGI/CoELA`
- URL: `https://github.com/UMass-Embodied-AGI/CoELA`
- Checked commit: `3e12dea925d735eefce33da71806ae9da6fcaf3f`
- Relevant subtree: `cwah/`
- Paper: Building Cooperative Embodied Agents Modularly with Large Language Models, ICLR 2024
- C-WAH description: Communicative Watch-And-Help extends Watch-And-Help by enabling agents to send messages to each other. Sending messages takes one timestep and has an upper message-length limit.
- License: `cwah/LICENSE.md`, Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International.
- License risk: acceptable only if this project's intended use remains non-commercial research and downstream sharing obligations are acceptable. GitHub metadata does not expose a top-level SPDX license for the repository, so the subtree license file must be recorded explicitly.

## Source Validation Checklist

| Requirement | Status | Evidence / notes |
|---|---|---|
| Multi-agent execution | Pass | `cwah/envs/unity_environment.py` initializes `num_agents`, returns per-agent observations, and executes `action_dict`; `cwah/testing_agents/test_symbolic_LLMs.py` creates two LLM agents. |
| Local or partial observation | Pass | `cwah/arguments.py` supports `--obs_type partial`; `UnityEnvironment.get_observation(agent_id, obs_type)` uses agent-specific visible nodes for partial observations. |
| Communication action or message channel | Pass | `cwah/arguments.py` has `--communication`; `UnityEnvironment.step()` treats `send_message` as an action, truncates messages, and stores `message_said`; observations include `messages`. |
| Symbolic state / observation access | Pass | Symbolic scripts exist, including `cwah/scripts/symbolic_obs_llm_llm.sh`; observation type supports `partial` and `full`; observations include graph `nodes` and `edges`. |
| Task success evaluator | Pass | `UnityEnvironment.reward()` uses `utils.check_progress()` and returns done/finished based on goal satisfaction; C-WAH metrics include Average Steps and Efficiency Improvement. |
| Reproducible episode reset | Partial pass | `UnityEnvironment.reset(task_id=...)` selects a deterministic task by ID; test scripts sort episode IDs. Some simulator behavior and random components still require explicit seed handling in the adapter. |
| Seed specified | Partial pass | `arguments.py` exposes `--seed`; test script sets Python random for `id_run`, but adapter must control all relevant RNGs and task IDs explicitly. |
| Action execution API | Pass | `UnityEnvironment.step(action_dict)` executes per-agent action strings; `get_action_space()` returns visible object IDs for non-image observation types. |
| License research-compatible | Conditional pass | CC BY-NC-SA 4.0 allows non-commercial use with attribution/share-alike constraints. This is not suitable for unrestricted commercial redistribution. |
| Upstream benchmark semantics available | Pass | README documents C-WAH tasks and metrics; scripts include symbolic and visual observation variants. |

## C-WAH Paper Setting Correspondence

The selected source corresponds to Communicative Watch-And-Help as used in CoELA rather than the original non-communicative WAH benchmark.

Confirmed correspondences:

- Household tasks in VirtualHome.
- Two-agent cooperative setting.
- Communication as an action with timestep cost.
- Partial and full symbolic observation modes.
- LLM and heuristic/planner baselines.
- Goal progress measured through ON/IN predicate satisfaction.

Differences and cautions:

- The repository is a broader CoELA codebase, not a standalone minimal C-WAH package.
- The `cwah/` subtree depends on a modified VirtualHome API branch and a specific simulator executable outside the repo.
- The initial adapter should use symbolic observation only. Visual observation paths require additional simulator/image dependencies and are out of scope.
- Some execution scripts are experiment runners, not clean library APIs; the adapter should wrap environment classes directly rather than shelling out to experiment scripts whenever possible.

## Adapter-Relevant API Surface

Likely adapter entry points:

- `cwah/envs/unity_environment.py::UnityEnvironment`
- `UnityEnvironment.reset(task_id=...)`
- `UnityEnvironment.get_observations()`
- `UnityEnvironment.get_observation(agent_id, obs_type)`
- `UnityEnvironment.get_action_space()`
- `UnityEnvironment.step(action_dict)`
- `UnityEnvironment.reward()`

Adapter-visible observation fields for symbolic mode:

- `nodes`
- `edges`
- `messages`
- `location`

Action forms to map initially:

- physical VirtualHome action strings such as `[walktowards]`, `[grab]`, `[open]`, `[putin]`, `[putback]`
- information action string `[send_message] ...`
- wait/no-op behavior when no legal action is selected

## Missing Features Or Required Wrappers

- No clean benchmark-neutral Python adapter exists upstream.
- No explicit source-provenance or per-agent visibility metadata exists upstream; VillagerAgent adapter must add it.
- No Dual-DAG mapping exists upstream; VillagerAgent adapter must map graph observations to epistemic nodes and legal actions to candidates.
- Seed reproducibility must be enforced in the VillagerAgent adapter by controlling task ID, configured seed, Python random, NumPy random, and simulator port/state where possible.
- Message-cost configuration must be provided by VillagerAgent. Upstream message cost is timestep cost plus message length truncation; token/cooldown/repeated-query penalties are not upstream concepts.
- External setup requires both `virtualhome` `wah` branch and simulator executable. These should be documented, not vendored into VillagerAgent.

## Benchmark Semantics Impact

Expected adapter impact if implemented correctly:

- No benchmark task definitions should be changed.
- No evaluator/progress logic should be changed.
- No oracle state should be exposed to agents.
- Messages should remain legal information actions that consume a step.
- The adapter may normalize observations and actions into VillagerAgent records, but should not change VirtualHome graph semantics.

Potential risk:

- If the adapter bypasses `UnityEnvironment.get_observation(agent_id, 'partial')` and reads `full_graph`, it would violate partial observability. The adapter must keep full graph/evaluator state runner-only.

## Go / No-Go Decision

Decision: conditional go for #173 after license acceptance.

Use name: `C-WAH symbolic adapter` is acceptable if the adapter targets `UMass-Embodied-AGI/CoELA` `cwah/` at the checked commit or a later pinned commit with equivalent APIs.

Do not use name: `C-WAH implemented` until the adapter can run at least the accepted symbolic smoke episodes and emit common trace/metrics.

If license constraints are not acceptable, use name: `WAH communication extension audit only` and do not add the submodule.

## Required Follow-Up For #173

- Add `external/CoELA` or another documented external path as a pinned submodule after license acceptance.
- Add `external/README.md` entry with upstream URL, commit, license, purpose, adapter APIs, patch status, and reproduction steps.
- Keep upstream code unmodified.
- Implement symbolic observation only.
- Add leakage checks proving one agent does not receive another agent's local observation directly.
- Add fixed-task/fixed-seed mock policy smoke tests before any LLM-policy smoke.
