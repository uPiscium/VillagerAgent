# C-WAH Goal-Aware Policy Notes

<!-- historical-result: cwah-goal-policy-diagnostics -->

The pre-publication-policy results in this note are permanently retired historical diagnostics. Recovery was exhausted on 2026-07-15 with no source bundle or backup available. Their aggregate values are context only and must not support paper, benchmark, or performance claims. Any comparative interpretation or recommendation attached to those values is preserved as contemporaneous commentary, not current evidence. The separately declared Issue #292 result remains archived evidence.

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

## Held-Object Scheduling

The policy also carries hand-state metadata on physical actions:

- `hand_state`: `empty` or `holding`
- `held_object_id` and `held_object_name`
- `held_objects`: compact list of locally observed held objects

Held-object scheduling rules:

- If the actor is already holding an object, unrelated `grab` actions are marked `blocked` with `blocked_by_holding_object`.
- Placement actions for the held object remain available and are prioritized when they match the goal object/target/relation.
- If a placement target is not locally close, placement points to `walktowards:<target>` as setup.
- If a placement target is close but closed/openable, placement points to `open:<target>` as setup.

This is still agent-local: held state is inferred from local `HOLDS_*` relations, and target readiness is inferred from local object states/properties and local `CLOSE` relations.

## Failure-Aware Scheduling

The runner also keeps episode-local failed action history for physical actions:

- failed action ids, such as `putback:agent_0:20:30`
- failed action signatures derived from agent-facing action parameters: `action_type:object_id:target_id`

When the fallback scheduler chooses a physical action, it filters actions matching either the failed id or failed signature. The LLM prompt also receives compact recent failed ids and signatures. This history is not persisted across runs and does not use evaluator progress, full graph state, simulator debug fields, or private observations from other agents.

For failed `open` actions, the runner also tracks episode-local target ids. The fallback scheduler filters repeated `open` attempts for the same target, the LLM prompt receives compact recent failed open target ids, and normalized artifacts record `open_failure_record_count` plus `open_failure_reason_counts`. This remains agent-local and uses only the attempted action parameters and the returned execution result.

## Failure-Message Diagnostics

C-WAH normalized artifacts, matrix outputs, and common reports also classify execution failure messages into `failure_reason_counts`. Current categories include:

- `not_found_source_object`
- `not_found_object`
- `already_open`
- `script_impossible`
- `general_execution_failure`
- `execution_failed`
- `debugger_abort`
- `unknown`

The classifier uses recorded step errors and CoELA/VirtualHome process output when available. These diagnostics are report-facing only; they are not added to agent contexts and do not expose evaluator progress, full graph state, simulator debug fields, or private observations from other agents.

## Navigation-Loop Suppression

The runner also tracks episode-local navigation signatures for `walktowards` actions. A navigation signature is derived from agent-facing action parameters as `walktowards:object_id:`. After the same signature is selected at least `navigation_loop_threshold` times, the fallback scheduler suppresses that signature for the rest of the episode.

The default threshold is conservative (`12`) to avoid replacing navigation loops with excessive invalid interactions. Suppressed navigation counts are written to normalized diagnostics and common reports as `navigation_loop_count`. This remains agent-local and does not use evaluator progress, full graph state, simulator debug fields, or private observations from other agents.

## Search And Object Discovery

Navigation actions carry agent-facing search metadata derived from local symbolic observations and sanitized task goals:

- `missing_goal_object`: whether a goal object class is not currently visible to the acting agent
- `missing_goal_target`: whether a goal target id/class is not currently visible to the acting agent
- `search_priority`: `search_goal_object_room`, `search_goal_object_receptacle`, `search_goal_target_room`, `search_goal_target_receptacle`, or `none`
- `search_reason`: currently `goal_object_not_visible`, `goal_target_not_visible`, or empty

The fallback scheduler prefers room search targets before receptacle/surface search targets, and both before irrelevant visible-object interactions. This remains symbolic and agent-local: it uses only visible rooms/objects/properties and sanitized task hints, not evaluator progress, full graph state, simulator debug fields, or private observations from other agents.

## Post-Grab Goal Transition

When the physical-action policy sees a held goal object and a legal placement candidate whose object, target, and `on`/`inside` relation all match the task goal, it now prioritizes that concrete transition ahead of generic search or navigation:

- An `executable_now` matching placement is selected directly.
- A `setup_required` matching placement selects its exact legal `walktowards` or `open` setup action when that setup is executable now.
- Mismatched-relation and fallback-receptacle placements are not eligible.
- Failed or blocked placements and setup actions remain suppressed, including failed-open target suppression.
- If the concrete goal target is not locally visible or in the action space, no target action is synthesized. Navigation toward the already-held object is blocked so legal target-search actions can proceed instead.

The selected transition is always one of the current legal action candidates. Overrides are recorded with reason `post_grab_goal_transition`, which is included in the existing policy-override reason diagnostics and common reports.

When a goal supplies a concrete target id, placement, navigation, and open metadata match that id exactly. Target-class matching is used only when the goal has no target id, preventing same-class object instances from being treated as the concrete target.

## Placement Target Suitability

Placement actions now carry agent-facing suitability metadata derived from local target properties and goal-relation matches:

- `placement_relation`: `inside` for `putin`, `on` for `putback`
- `placement_relation_compatibility`: `goal_relation_match`, `goal_relation_mismatch`, or `goal_relation_unknown`
- `target_affordance`: `container`, `surface`, `recipient`, `placeable`, or `unknown`
- `placement_suitability`: `goal_relation_match`, `compatible_container`, `compatible_surface`, or `fallback_receptacle`
- `container_suitability`: `container_open`, `container_closed_needs_open`, `container_unknown`, or `container_likely_unsuitable` for `putin` targets

The fallback scheduler de-prioritizes `fallback_receptacle` placements when better physical alternatives exist. It also prefers placements whose action relation matches the goal relation (`putback` for `on`, `putin` for `inside`) and de-prioritizes opposite-relation placements. For `putin`, it also de-prioritizes containers that locally look unsuitable, including an `on` goal relation for the same object/target. This is intentionally conservative: it uses only visible object properties/states and sanitized goal hints, and it does not inspect evaluator progress, full graph state, simulator debug fields, or private observations from other agents.

## 2026-07-04 Comparison

Comparison artifact directory: `/tmp/opencode/cwah-goal-policy-real-20260704`.

Configuration matched the baseline in `docs/benchmarks/cwah_real_baseline.md`:

- Tasks: `0,1,2`
- Seeds: `0,1`
- Step budget: `25`
- Full episode mode: enabled
- Physical-action preference: from step `0`

Retired historical aggregate copied into this note:

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

Retired historical aggregate copied into this note:

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

## 2026-07-05 Held-Object Scheduling Comparison

Comparison artifact directory: `/tmp/opencode/cwah-held-state-real-20260705-open-fix`.

Configuration matched the previous bounded comparisons:

- Tasks: `0,1,2`
- Seeds: `0,1`
- Step budget: `25`
- Full episode mode: enabled
- Physical-action preference: from step `0`

Retired historical aggregate copied into this note:

- Runs: `6`
- Runtime failed runs: `0`
- Task successes: `0`
- Success rate: `0.0`
- Mean normalized progress: `0.5920745920745921`
- Mean steps: `25.0`
- Physical actions: `150`
- Communication actions: `0`
- Action mix: `grab=33`, `open=10`, `putback=42`, `putin=4`, `walktowards=61`
- Failed action records: `29`
- Failed action mix: `open=7`, `putback=17`, `putin=4`, `walktowards=1`

Held-object scheduling reduced repeated extra grabs while carrying an object and restored the bounded real matrix to zero runtime failures after separating `putin` targets from `putback` targets and suppressing `open` actions for already-open objects. It still does not improve task success; remaining failed actions show that target suitability and VirtualHome placement constraints need more precise modeling.

## 2026-07-08 Failure-Aware Comparison

Comparison artifact directory: `/tmp/opencode/cwah-failure-aware-real-20260708`.

Configuration matched the previous bounded comparisons:

- Tasks: `0,1,2`
- Seeds: `0,1`
- Step budget: `25`
- Full episode mode: enabled
- Physical-action preference: from step `0`

Retired historical aggregate copied into this note:

- Runs: `6`
- Runtime failed runs: `0`
- Task successes: `0`
- Success rate: `0.0`
- Mean normalized progress: `0.5920745920745921`
- Mean steps: `25.0`
- Physical actions: `150`
- Communication actions: `0`
- Action mix: `grab=29`, `open=8`, `putback=24`, `putin=6`, `walktowards=83`
- Failed action records: `21`
- Failed action mix: `grab=3`, `open=6`, `putback=9`, `putin=3`

Failure-aware scheduling reduced repeated invalid placement/open attempts relative to the held-object comparison, but it did not change task success or normalized progress. The action mix shifted toward navigation after failed interactions were suppressed, which indicates the next improvement should focus on search/path-loop handling and target suitability rather than claiming benchmark gains from this change.

## 2026-07-09 Navigation-Loop Comparison

Comparison artifact directory: `/tmp/opencode/cwah-navigation-loop-real-20260709-final`.

Configuration matched the previous bounded comparisons, with `navigation_loop_threshold=12`:

- Tasks: `0,1,2`
- Seeds: `0,1`
- Step budget: `25`
- Full episode mode: enabled
- Physical-action preference: from step `0`

Retired historical aggregate copied into this note:

- Runs: `6`
- Runtime failed runs: `0`
- Task successes: `0`
- Success rate: `0.0`
- Mean normalized progress: `0.5920745920745921`
- Mean steps: `25.0`
- Physical actions: `150`
- Communication actions: `0`
- Action mix: `grab=25`, `open=9`, `putback=37`, `putin=4`, `walktowards=75`
- Failed action records: `31`
- Navigation loop suppressions: `3`
- Failed action mix: `open=6`, `putback=18`, `putin=4`, `walktowards=3`

Navigation-loop suppression modestly reduced repeated navigation relative to the failure-aware comparison (`walktowards=83` to `75`) while preserving zero runtime failures, but it did not improve success or normalized progress and increased failed action records. The result should be treated as diagnostic hardening rather than a behavioral improvement claim; the next policy step should improve target suitability before replacing repeated navigation with placement/open attempts.

## 2026-07-10 Placement Target Suitability Comparison

Comparison artifact directory: `/tmp/opencode/cwah-target-suitability-real-20260710`.

Configuration matched the previous bounded comparisons:

- Tasks: `0,1,2`
- Seeds: `0,1`
- Step budget: `25`
- Full episode mode: enabled
- Physical-action preference: from step `0`
- Navigation-loop threshold: `12`

Retired historical aggregate copied into this note:

- Runs: `6`
- Runtime failed runs: `0`
- Task successes: `0`
- Success rate: `0.0`
- Mean normalized progress: `0.5920745920745921`
- Mean steps: `25.0`
- Physical actions: `150`
- Communication actions: `0`
- Action mix: `grab=38`, `open=9`, `putback=39`, `putin=10`, `walktowards=54`
- Failed action records: `27`
- Navigation loop suppressions: `2`
- Failed action mix: `grab=1`, `open=6`, `putback=9`, `putin=10`, `walktowards=1`

Placement target suitability reduced failed action records relative to the navigation-loop comparison (`31` to `27`) and reduced repeated navigation (`walktowards=75` to `54`) while preserving zero runtime failures. It still did not improve task success or normalized progress, and `putin` failures increased, so this remains policy hardening rather than a benchmark-performance claim. The next step should distinguish container suitability more precisely rather than only de-prioritizing fallback surface/receptacle targets.

## 2026-07-10 Failure-Message Diagnostics Comparison

Comparison artifact directory: `/tmp/opencode/cwah-failure-diagnostics-real-20260710-fix`.

Configuration matched the previous bounded comparisons:

- Tasks: `0,1,2`
- Seeds: `0,1`
- Step budget: `25`
- Full episode mode: enabled
- Physical-action preference: from step `0`
- Navigation-loop threshold: `12`

Retired historical aggregate copied into this note:

- Runs: `6`
- Runtime failed runs: `0`
- Task successes: `0`
- Success rate: `0.0`
- Mean normalized progress: `0.5920745920745921`
- Mean steps: `25.0`
- Physical actions: `150`
- Communication actions: `0`
- Action mix: `grab=34`, `open=11`, `putback=28`, `putin=8`, `walktowards=69`
- Failed action records: `27`
- Navigation loop suppressions: `2`
- Failed action mix: `grab=1`, `open=9`, `putback=3`, `putin=7`, `walktowards=7`
- Failure reason counts: `script_impossible=23`, `not_found_object=4`

This run validates diagnostics propagation rather than a policy change. The new failure reason counts show most observed failures remain generic VirtualHome script impossibility, with a smaller number of missing-object failures. The next policy work should use these reason counts to target container suitability and repeated-open handling.

## 2026-07-10 Container-Suitability Comparison

Comparison artifact directory: `/tmp/opencode/cwah-container-suitability-real-20260710`.

Configuration matched the previous bounded comparisons:

- Tasks: `0,1,2`
- Seeds: `0,1`
- Step budget: `25`
- Full episode mode: enabled
- Physical-action preference: from step `0`
- Navigation-loop threshold: `12`

Retired historical aggregate copied into this note:

- Runs: `6`
- Runtime failed runs: `0`
- Task successes: `0`
- Success rate: `0.0`
- Mean normalized progress: `0.5920745920745921`
- Mean steps: `25.0`
- Physical actions: `150`
- Communication actions: `0`
- Action mix: `close=1`, `grab=27`, `open=16`, `putback=18`, `putin=8`, `walktowards=80`
- Failed action records: `18`
- Navigation loop suppressions: `2`
- Failed action mix: `grab=1`, `open=8`, `putback=3`, `putin=6`
- Failure reason counts: `script_impossible=14`, `not_found_object=4`

Container suitability reduced failed action records relative to the failure-diagnostics comparison (`27` to `18`) while preserving zero runtime failures. It did not improve task success or normalized progress, so this remains policy hardening rather than a benchmark-performance claim. The next policy work should focus on repeated-open suppression and relation-aware placement selection.

## 2026-07-10 Repeated-Open Suppression Comparison

Comparison artifact directory: `/tmp/opencode/cwah-repeated-open-real-20260710`.

Configuration matched the previous bounded comparisons:

- Tasks: `0,1,2`
- Seeds: `0,1`
- Step budget: `25`
- Full episode mode: enabled
- Physical-action preference: from step `0`
- Navigation-loop threshold: `12`

Retired historical aggregate copied into this note:

- Runs: `6`
- Runtime failed runs: `0`
- Task successes: `0`
- Success rate: `0.0`
- Mean normalized progress: `0.5920745920745921`
- Mean steps: `25.0`
- Physical actions: `150`
- Communication actions: `0`
- Action mix: `close=1`, `grab=25`, `open=21`, `putback=24`, `putin=9`, `walktowards=70`
- Failed action records: `38`
- Open failure records: `16`
- Navigation loop suppressions: `3`
- Failed action mix: `grab=1`, `open=16`, `putback=9`, `putin=8`, `walktowards=4`
- Failure reason counts: `script_impossible=33`, `not_found_object=4`, `not_found_source_object=1`
- Open failure reason counts: `execution_failed=16`

Repeated-open suppression adds explicit tracking and report diagnostics for failed open targets, but this bounded run did not improve task success, normalized progress, or failed-action counts relative to container suitability. Treat this as observability and suppression plumbing only; the higher open failure count suggests the next policy iteration should improve open-target readiness before making benchmark-performance claims.

## 2026-07-10 Relation-Aware Placement Comparison

Comparison artifact directory: `/tmp/opencode/cwah-relation-aware-real-20260710`.

Configuration matched the previous bounded comparisons:

- Tasks: `0,1,2`
- Seeds: `0,1`
- Step budget: `25`
- Full episode mode: enabled
- Physical-action preference: from step `0`
- Navigation-loop threshold: `12`

Retired historical aggregate copied into this note:

- Runs: `6`
- Runtime failed runs: `0`
- Task successes: `0`
- Success rate: `0.0`
- Mean normalized progress: `0.5920745920745921`
- Mean steps: `25.0`
- Physical actions: `150`
- Communication actions: `0`
- Action mix: `close=1`, `grab=33`, `open=20`, `putback=29`, `putin=13`, `walktowards=54`
- Failed action records: `42`
- Open failure records: `16`
- Navigation loop suppressions: `2`
- Failed action mix: `grab=1`, `open=16`, `putback=7`, `putin=11`, `walktowards=7`
- Failure reason counts: `script_impossible=37`, `not_found_object=5`
- Open failure reason counts: `execution_failed=16`

Relation-aware placement adds explicit action-goal relation compatibility metadata and ranking for `on` versus `inside` placement choices. This bounded run did not improve task success, normalized progress, or failed-action count, so it remains policy hardening rather than a benchmark-performance claim. The next policy work should address object discovery/search and open-target readiness.

## 2026-07-10 Search-Discovery Comparison

Comparison artifact directory: `/tmp/opencode/cwah-search-discovery-real-20260710`.

Configuration matched the previous bounded comparisons:

- Tasks: `0,1,2`
- Seeds: `0,1`
- Step budget: `25`
- Full episode mode: enabled
- Physical-action preference: from step `0`
- Navigation-loop threshold: `12`

Retired historical aggregate copied into this note:

- Runs: `6`
- Runtime failed runs: `0`
- Task successes: `0`
- Success rate: `0.0`
- Mean normalized progress: `0.5920745920745921`
- Mean steps: `25.0`
- Physical actions: `150`
- Communication actions: `0`
- Action mix: `grab=6`, `walktowards=144`
- Failed action records: `0`
- Open failure records: `0`
- Navigation loop suppressions: `8`
- Failure reason counts: none

Search-discovery ranking removed bounded-run execution failures by preferring exploration/navigation when goal objects or targets were not visible, but it did not improve task success or normalized progress. It also over-shifted behavior toward navigation, so this remains policy hardening and diagnostics signal rather than a benchmark-performance claim. Future work should convert successful search/navigation into concrete object interactions instead of continuing to navigate.

The archived post-Dual-DAG evaluation is documented separately in
`docs/benchmarks/cwah_issue_292_real_diagnostic.md` so this retired historical
note cannot act as an archive declaration for future results.
