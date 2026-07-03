# External Benchmarks

External benchmark repositories are managed as git submodules and pinned by commit. Do not edit benchmark source trees directly from VillagerAgent changes.

## CRAFT

- Upstream repository: `https://github.com/csu-signal/CRAFT`
- Local path: `external/CRAFT`
- Pinned commit: see submodule entry in git index
- License: see upstream repository
- Purpose: CRAFT benchmark environment and datasets for the existing CRAFT integration.
- Adapter API dependencies: existing CRAFT runner/dataset APIs used by `benchmarks/craft/`.
- Upstream modifications: none in-tree.
- Reproduction: `git submodule update --init --recursive external/CRAFT`

## CoELA / Communicative Watch-And-Help

- Upstream repository: `https://github.com/UMass-Embodied-AGI/CoELA.git`
- Local path: `external/CoELA`
- Pinned commit: `3e12dea925d735eefce33da71806ae9da6fcaf3f`
- Relevant subtree: `external/CoELA/cwah/`
- License: `external/CoELA/cwah/LICENSE.md`, Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International.
- License note: use is conditional on non-commercial research use with attribution/share-alike obligations.
- Purpose: source benchmark implementation for Communicative Watch-And-Help symbolic observation experiments.
- Adapter API dependencies:
  - `cwah/envs/unity_environment.py::UnityEnvironment`
  - `UnityEnvironment.reset(task_id=...)`
  - `UnityEnvironment.get_observations()`
  - `UnityEnvironment.get_observation(agent_id, obs_type)`
  - `UnityEnvironment.get_action_space()`
  - `UnityEnvironment.step(action_dict)`
  - `UnityEnvironment.reward()`
- Upstream modifications: none in-tree. Any compatibility changes must be handled in VillagerAgent adapters or documented patch files under `external/patches/CoELA/`.
- External runtime dependencies:
  - VirtualHome API from `https://github.com/xavierpuigf/virtualhome.git`, branch `wah`.
  - CoELA-provided VirtualHome executable `linux_exec.v2.3.0.x86_64`.
- Reproduction:
  - `git submodule update --init --recursive external/CoELA`
  - Follow `external/CoELA/cwah/README.md` for VirtualHome and executable setup.
- Verification status:
  - Mock C-WAH adapter with real LLM call: `python -m benchmarks.cwah.llm_smoke --env mock --max-policy-steps 2 --output /tmp/opencode/cwah-llm-smoke-mock.json` succeeded with `task_success=true` and `normalized_progress=1.0`.
  - CoELA dataset present: `external/CoELA/cwah/dataset/test_env_set_help.pik`.
  - CoELA executable installed locally and ignored by git: `external/CoELA/executable/linux_exec.v2.3.0.x86_64`.
  - Real CoELA smoke with LLM call: `nix develop --command python -m benchmarks.cwah.llm_smoke --env coela --max-policy-steps 1 --output /tmp/opencode/cwah-llm-smoke-coela.json` succeeded with `passed=true`.
