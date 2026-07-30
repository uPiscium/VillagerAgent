from __future__ import annotations

from typing import Any

from env.judger_iteration import normalize_iteration_metadata


UNAVAILABLE = {
    "available": False,
    "reason": "not captured by the runtime",
}


def build_judged_iteration_trace(
    *,
    action_log: dict,
    agent_history: list | None,
    final_score: dict,
    agent_iteration_limit: int | None = None,
    agent_iteration_limit_source: str | None = None,
) -> dict:
    recorded_actions = [
        action
        for agent, actions in action_log.items()
        if agent != "_attempt_id" and isinstance(actions, list)
        for action in actions
        if isinstance(action, dict)
    ]
    valid_episodes = (
        [episode for episode in agent_history if isinstance(episode, dict)]
        if agent_history is not None
        else None
    )
    limit = (
        agent_iteration_limit
        if isinstance(agent_iteration_limit, int)
        and not isinstance(agent_iteration_limit, bool)
        and agent_iteration_limit > 0
        else None
    )
    agent_iteration = (
        {
            "available": True,
            "source": "Alice_history.json outer dict episodes",
            "limit": limit,
            "limit_source": agent_iteration_limit_source if limit is not None else None,
            "limit_available": limit is not None,
            "used": len(valid_episodes),
        }
        if valid_episodes is not None
        else {
            "available": False,
            "source": None,
            "limit": limit,
            "limit_source": agent_iteration_limit_source if limit is not None else None,
            "limit_available": limit is not None,
            "used": None,
            "reason": "not captured by runtime",
        }
    )
    entries = []
    recorded_index = 0
    if valid_episodes is not None:
        for outer_episode_index, episode in enumerate(valid_episodes, start=1):
            action_index = 0
            history_actions = [
                item
                for item in episode.get("action_list", [])
                if isinstance(item, dict)
            ]
            for item in history_actions:
                action = item.get("action") if isinstance(item.get("action"), dict) else {}
                tool = action.get("tool")
                feedback = item.get("feedback")
                trace_index = len(entries) + 1
                if tool == "_Exception":
                    entries.append({
                        "trace_index": trace_index,
                        "outer_episode_index": outer_episode_index,
                        "action_index_in_episode": None,
                        "model_response_type": "parse_error",
                        "model_response": action.get("log"),
                        "action": None,
                        "action_input": {},
                        "parse_error": str(
                            feedback or action.get("tool_input") or "invalid response"
                        ),
                        "pre_action_world_state": dict(UNAVAILABLE),
                        "post_action_world_state": dict(UNAVAILABLE),
                        "task_state_before": "unknown",
                        "task_state_after": "unknown",
                        "judger_progress_before": None,
                        "judger_progress_after": None,
                    })
                    continue

                action_index += 1
                recorded = (
                    recorded_actions[recorded_index]
                    if recorded_index < len(recorded_actions)
                    else {}
                )
                recorded_index += 1
                result = (
                    recorded.get("result")
                    if isinstance(recorded.get("result"), dict)
                    else feedback
                )
                result = result if isinstance(result, dict) else {}
                entries.append(_action_trace_entry(
                    trace_index=trace_index,
                    outer_episode_index=outer_episode_index,
                    action_index_in_episode=action_index,
                    action=tool or recorded.get("action"),
                    action_input=action.get("tool_input") or recorded.get("kwargs") or {},
                    model_response=action.get("log"),
                    result=result,
                ))

            if episode.get("final_answer"):
                entries.append({
                    "trace_index": len(entries) + 1,
                    "outer_episode_index": outer_episode_index,
                    "action_index_in_episode": None,
                    "model_response_type": "final_answer",
                    "model_response": episode["final_answer"],
                    "action": None,
                    "action_input": {},
                    "pre_action_world_state": dict(UNAVAILABLE),
                    "post_action_world_state": dict(UNAVAILABLE),
                    "task_state_before": "unknown",
                    "task_state_after": "unknown",
                    "judger_progress_before": None,
                    "judger_progress_after": final_score.get(
                        "progress",
                        final_score.get("score"),
                    ),
                })
    else:
        for action_index, recorded in enumerate(recorded_actions, start=1):
            result = recorded.get("result") if isinstance(recorded.get("result"), dict) else {}
            entries.append(_action_trace_entry(
                trace_index=len(entries) + 1,
                outer_episode_index=None,
                action_index_in_episode=action_index,
                action=recorded.get("action"),
                action_input=recorded.get("kwargs") or {},
                model_response=None,
                result=result,
            ))

    return {
        "schema_version": 1,
        "outer_episode_count": (
            len(valid_episodes) if valid_episodes is not None else None
        ),
        "malformed_outer_episode_count": (
            len(agent_history) - len(valid_episodes)
            if agent_history is not None
            else None
        ),
        "agent_iteration": agent_iteration,
        "entries": entries,
    }


def build_judged_terminal_diagnostics(
    *,
    summary: dict,
    launch_config: dict,
    trace: dict,
    runtime_snapshot: dict | None = None,
) -> dict:
    score = summary.get("final_score") if isinstance(summary.get("final_score"), dict) else {}
    iteration = score.get("iteration") if isinstance(score.get("iteration"), dict) else {}
    expected = score.get("expected_terminal_state")
    if not isinstance(expected, dict):
        evaluation = launch_config.get("evaluation_arg", {})
        expected = {"task_scenario": launch_config.get("task_scenario")}
        if launch_config.get("task_scenario") == "move":
            expected.update({
                "player_position": {
                    axis: evaluation.get(axis)
                    for axis in ("x", "y", "z")
                },
                "axis_tolerance": 1,
                "comparison": "strictly_less_than",
            })
        else:
            expected["evaluation_arg"] = evaluation
    entries = trace.get("entries", []) if isinstance(trace, dict) else []
    actual = score.get("actual_terminal_state")
    if not isinstance(actual, dict):
        actual = _last_observed_world_state(entries)

    root_cause = score.get("root_cause_category")
    legacy_success = any(
        item.get("tool_status") is True
        and item.get("tool_completion_semantics") == "legacy_status_only"
        for item in entries
    )
    if score.get("status") == "success":
        root_cause = None
    elif not score and summary.get("error"):
        root_cause = "runtime_internal_error"
    elif (
        not root_cause
        and launch_config.get("task_scenario") == "move"
        and score.get("end_reason") == "max iteration out"
        and actual.get("available") is not True
        and legacy_success
    ):
        root_cause = "completion_contract_mismatch"
    elif not root_cause:
        root_cause = "task_not_satisfied" if actual.get("available") else "world_state_not_observed"
    if root_cause == "completion_contract_mismatch" and actual.get("available") is not True:
        actual = {
            "available": False,
            "reason": "post-action position was not captured by the previous runtime",
        }

    last_action = next(
        (
            item
            for item in reversed(entries)
            if item.get("action") and item.get("tool_status") is True
        ),
        {},
    )
    admission = summary.get("artifact_admission", {})
    invalid = admission.get("invalid", []) if isinstance(admission, dict) else []
    judger_iteration = normalize_iteration_metadata(iteration)
    return {
        "schema_version": 2,
        "agent_iteration": trace.get("agent_iteration", {
            "available": False,
            "source": None,
            "limit": None,
            "used": None,
            "reason": "not captured by runtime",
        }),
        "judger_iteration": judger_iteration,
        "last_successful_action": last_action,
        "last_observed_world_state": actual,
        "expected_terminal_state": expected,
        "actual_terminal_state": actual,
        "score_status": score.get("status"),
        "end_reason": score.get("end_reason"),
        "progress": summary.get("progress"),
        "root_cause_category": root_cause,
        "root_cause_evidence": (
            {
                "tool_contract": "legacy Euclidean distance threshold",
                "judger_contract": "strict per-axis threshold",
                "final_position_reconstructable": False,
            }
            if root_cause == "completion_contract_mismatch"
            else {}
        ),
        "runtime_error": {
            "error_type": summary.get("error_type"),
            "message": summary.get("error"),
        },
        "runtime_task_dag_state": (
            runtime_snapshot.get("summary", {})
            if isinstance(runtime_snapshot, dict)
            else {}
        ),
        "child_protocol_state": summary.get("child_protocol", {}),
        "artifact_admission_invalid": invalid,
        "artifact_admission_causality": {
            "score": "judger terminal status was not success" if "score" in invalid else None,
            "runtime_task_dag": (
                "external judger failure was propagated to the runtime task"
                if "runtime_task_dag" in invalid else None
            ),
            "child_protocol": (
                "judged task failure made the runtime child exit with an error status"
                if "child_protocol" in invalid else None
            ),
        },
        "diagnostics_artifact": "judged_terminal_diagnostics.json",
    }


def _last_observed_world_state(trace: list[dict]) -> dict[str, Any]:
    for item in reversed(trace):
        state = item.get("post_action_world_state")
        if isinstance(state, dict) and state.get("available") is True:
            return state
    return dict(UNAVAILABLE)


def _action_trace_entry(
    *,
    trace_index: int,
    outer_episode_index: int | None,
    action_index_in_episode: int,
    action,
    action_input: dict,
    model_response,
    result: dict,
) -> dict:
    observed_position = result.get("observed_position")
    return {
        "trace_index": trace_index,
        "outer_episode_index": outer_episode_index,
        "action_index_in_episode": action_index_in_episode,
        "model_response_type": "structured_action",
        "model_response": model_response,
        "action": action,
        "action_input": action_input,
        "tool_status": result.get("status"),
        "tool_completion_policy": result.get("completion_policy"),
        "tool_completion_semantics": result.get(
            "completion_semantics",
            "legacy_status_only",
        ),
        "tool_result": result,
        "pre_action_world_state": dict(UNAVAILABLE),
        "post_action_world_state": (
            {"available": True, "player_position": observed_position}
            if isinstance(observed_position, dict)
            else dict(UNAVAILABLE)
        ),
        "task_state_before": "unknown",
        "task_state_after": "unknown",
        "judger_progress_before": None,
        "judger_progress_after": None,
    }
