# C-WAH Goal-Aware Policy Notes

This note records the first goal-aware policy improvement after the 2026-07-04 bounded baseline. The change is still an integration/policy-maturation step, not a benchmark-performance claim.

## Policy Change

The C-WAH adapter now extracts sanitized per-agent task goal hints from CoELA `goal_spec` or `task_goal` after reset. Each hint contains only:

- relation, such as `on` or `inside`
- goal object class
- target id and target class when available
- required count

These hints are added to agent-facing observations as `task_goal` records. They do not include evaluator progress, full graph state, simulator debug fields, or private observations from other agents.

Legal physical actions now carry goal-relevance metadata:

- `goal_object_match`
- `goal_target_match`
- `goal_relation_matches`

The fallback physical-action selector prioritizes:

1. placement actions that match the goal relation and target
2. grabbing visible goal objects
3. walking toward visible goal objects or targets
4. previous non-goal physical fallback order

The LLM prompt also includes `task_goals` and candidate action goal-match fields through the existing observation and action-intent summaries.

## 2026-07-04 Comparison

Comparison artifact directory: `/tmp/opencode/cwah-goal-policy-real-20260704`.

Configuration matched the baseline in `docs/benchmarks/cwah_real_baseline.md`:

- Tasks: `0,1,2`
- Seeds: `0,1`
- Step budget: `25`
- Full episode mode: enabled
- Physical-action preference: from step `0`

Observed common-report aggregate:

- Runs: `6`
- Runtime failed runs: `0`
- Task successes: `0`
- Success rate: `0.0`
- Mean normalized progress: `0.5920745920745921`
- Mean steps: `25.0`
- Physical actions: `150`
- Communication actions: `0`
- Action mix: `grab=98`, `walktowards=39`, `putin=12`, `putback=1`

The bounded comparison did not improve success or normalized progress relative to the baseline. It did change behavior from pure `grab`/`putin` fallback toward goal-relevance-aware navigation and placement attempts. Further improvement should focus on action preconditions and object search/path sequencing rather than claiming benchmark gains from this step.
