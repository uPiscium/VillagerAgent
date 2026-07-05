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

## Executability-Aware Scheduling

The policy also attaches lightweight precondition hints to C-WAH physical actions using only agent-local symbolic observations:

- `precondition_status`: `executable_now`, `setup_required`, or `blocked`
- `precondition_reason`: short reason such as `actor_close_to_object` or `needs_walktowards_target`
- `setup_action_id`: legal setup action to try first when available

Current rules are intentionally conservative:

- `walktowards` is executable now and acts as the setup action.
- `grab`, `open`, and `close` require a local `CLOSE` relation between the actor and object.
- `putin` and `putback` require the object to be held and a local `CLOSE` relation to the target receptacle/surface.
- When an LLM selects a setup-required action, the runner can override it to the ranked setup action before execution.

These hints do not use evaluator progress, full graph state, simulator debug state, or private observations from other agents.

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

## 2026-07-05 Executability-Aware Comparison

Comparison artifact directory: `/tmp/opencode/cwah-executability-real-20260705`.

Configuration matched the previous bounded comparisons:

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
- Action mix: `grab=62`, `walktowards=41`, `putin=32`, `putback=14`, `open=1`
- Failed action records: `64`
- Failed action mix: `grab=34`, `putin=28`, `putback=1`, `open=1`

This run confirms that local precondition hints shift behavior toward setup/navigation before some interactions, but task success and normalized progress did not improve. The remaining failures indicate that `CLOSE`-based local preconditions are not sufficient; future work should model hand occupancy, target capacity/surface affordances, and whether VirtualHome requires additional navigation/alignment before placement.
