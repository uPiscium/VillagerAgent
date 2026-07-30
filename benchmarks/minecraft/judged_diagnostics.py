from __future__ import annotations

from typing import Any


UNAVAILABLE = {
    "available": False,
    "reason": "not captured by the runtime",
}


def build_judged_iteration_trace(
    *,
    action_log: dict,
    agent_history: list | None,
    final_score: dict,
) -> list[dict]:
    recorded_actions = [
        action
        for agent, actions in action_log.items()
        if agent != "_attempt_id" and isinstance(actions, list)
        for action in actions
        if isinstance(action, dict)
    ]
    history_actions = [
        item
        for episode in agent_history or []
        if isinstance(episode, dict)
        for item in episode.get("action_list", [])
        if isinstance(item, dict)
    ]
    trace = []
    recorded_index = 0
    for iteration, item in enumerate(history_actions, start=1):
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        tool = action.get("tool")
        feedback = item.get("feedback")
        if tool == "_Exception":
            trace.append({
                "iteration": iteration,
                "model_response_type": "parse_error",
                "model_response": action.get("log"),
                "action": None,
                "action_input": {},
                "parse_error": str(feedback or action.get("tool_input") or "invalid response"),
                "pre_action_world_state": dict(UNAVAILABLE),
                "post_action_world_state": dict(UNAVAILABLE),
                "task_state_before": "unknown",
                "task_state_after": "unknown",
                "judger_progress_before": None,
                "judger_progress_after": None,
            })
            continue

        recorded = recorded_actions[recorded_index] if recorded_index < len(recorded_actions) else {}
        recorded_index += 1
        result = recorded.get("result") if isinstance(recorded.get("result"), dict) else feedback
        result = result if isinstance(result, dict) else {}
        observed_position = result.get("observed_position")
        trace.append({
            "iteration": iteration,
            "model_response_type": "structured_action",
            "model_response": action.get("log"),
            "action": tool or recorded.get("action"),
            "action_input": action.get("tool_input") or recorded.get("kwargs") or {},
            "tool_status": result.get("status"),
            "tool_completion_semantics": result.get("completion_semantics", "legacy_status_only"),
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
        })

    if not history_actions:
        for iteration, recorded in enumerate(recorded_actions, start=1):
            result = recorded.get("result") if isinstance(recorded.get("result"), dict) else {}
            observed_position = result.get("observed_position")
            trace.append({
                "iteration": iteration,
                "model_response_type": "structured_action",
                "model_response": None,
                "action": recorded.get("action"),
                "action_input": recorded.get("kwargs") or {},
                "tool_status": result.get("status"),
                "tool_completion_semantics": result.get("completion_semantics", "legacy_status_only"),
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
            })

    if agent_history and isinstance(agent_history[-1], dict) and agent_history[-1].get("final_answer"):
        trace.append({
            "iteration": len(trace) + 1,
            "model_response_type": "final_answer",
            "model_response": agent_history[-1]["final_answer"],
            "action": None,
            "action_input": {},
            "pre_action_world_state": dict(UNAVAILABLE),
            "post_action_world_state": dict(UNAVAILABLE),
            "task_state_before": "unknown",
            "task_state_after": "unknown",
            "judger_progress_before": None,
            "judger_progress_after": final_score.get("progress", final_score.get("score")),
        })
    return trace


def build_judged_terminal_diagnostics(
    *,
    summary: dict,
    launch_config: dict,
    trace: list[dict],
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
    actual = score.get("actual_terminal_state")
    if not isinstance(actual, dict):
        actual = _last_observed_world_state(trace)

    root_cause = score.get("root_cause_category")
    if score.get("status") == "success":
        root_cause = None
    elif not score and summary.get("error"):
        root_cause = "runtime_internal_error"
    elif not root_cause:
        root_cause = "task_not_satisfied" if actual.get("available") else "world_state_not_observed"

    last_action = next(
        (
            item
            for item in reversed(trace)
            if item.get("action") and item.get("tool_status") is True
        ),
        {},
    )
    admission = summary.get("artifact_admission", {})
    invalid = admission.get("invalid", []) if isinstance(admission, dict) else []
    return {
        "schema_version": 1,
        "agent_iteration_limit": 7,
        "agent_iteration_limit_source": "Env.step default max_turn",
        "agent_iterations_used": len(trace),
        "judger_iteration_source": iteration.get("source"),
        "judger_iteration_limit": iteration.get("limit"),
        "judger_iterations_used": iteration.get("used"),
        "judger_terminal_observations": iteration.get("terminal_observations"),
        "last_successful_action": last_action,
        "last_observed_world_state": actual,
        "expected_terminal_state": expected,
        "actual_terminal_state": actual,
        "score_status": score.get("status"),
        "end_reason": score.get("end_reason"),
        "progress": summary.get("progress"),
        "root_cause_category": root_cause,
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
