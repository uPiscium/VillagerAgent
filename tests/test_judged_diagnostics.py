from benchmarks.minecraft.judged_diagnostics import (
    build_judged_iteration_trace,
    build_judged_terminal_diagnostics,
)


def test_iteration_trace_distinguishes_tool_success_from_task_success():
    trace = build_judged_iteration_trace(
        action_log={
            "Alice": [{
                "action": "navigateTo",
                "kwargs": {"x": 5, "y": -60, "z": 5},
                "result": {
                    "status": True,
                    "completion_semantics": "all_axis_deltas_strictly_below_tolerance",
                    "observed_position": {"x": 5.0, "y": -60.0, "z": 5.0},
                },
            }],
        },
        agent_history=[{
            "action_list": [{
                "action": {
                    "tool": "navigateTo",
                    "tool_input": {"x": 5, "y": -60, "z": 5},
                },
                "feedback": {"status": True},
            }],
            "final_answer": "arrived",
        }],
        final_score={"status": "failure", "progress": 0},
        agent_iteration_limit=11,
        agent_iteration_limit_source="test runtime max_turn",
    )

    assert trace["agent_iteration"] == {
        "available": True,
        "source": "Alice_history.json outer dict episodes",
        "limit": 11,
        "limit_source": "test runtime max_turn",
        "limit_available": True,
        "used": 1,
    }
    assert trace["entries"][0]["tool_status"] is True
    assert trace["entries"][0]["outer_episode_index"] == 1
    assert trace["entries"][0]["action_index_in_episode"] == 1
    assert trace["entries"][0]["post_action_world_state"]["available"] is True
    assert trace["entries"][1]["model_response_type"] == "final_answer"
    assert trace["entries"][1]["judger_progress_after"] == 0


def test_terminal_diagnostics_identifies_external_judger_iteration_source():
    diagnostics = build_judged_terminal_diagnostics(
        summary={
            "final_score": {
                "status": "failure",
                "progress": 0,
                "end_reason": "max iteration out",
                "iteration": {
                    "source": "Alice_history.json outer episode count",
                    "owner": "external_meta_judger",
                    "limit": 1,
                    "used": 1,
                    "terminal_observations": 3,
                },
                "root_cause_category": "task_not_satisfied",
            },
            "progress": 0,
            "error": "judged task failed",
            "error_type": "JudgedTaskFailure",
            "artifact_admission": {
                "passed": False,
                "invalid": ["score", "runtime_task_dag", "child_protocol"],
            },
        },
        launch_config={
            "task_scenario": "move",
            "evaluation_arg": {"x": 5, "y": -60, "z": 5},
        },
        trace={
            "agent_iteration": {
                "available": True,
                "source": "Alice_history.json outer dict episodes",
                "limit": 9,
                "used": 2,
            },
            "entries": [],
        },
        runtime_snapshot={"summary": {"terminal_state": "failure"}},
    )

    assert diagnostics["schema_version"] == 2
    assert diagnostics["agent_iteration"]["limit"] == 9
    assert diagnostics["agent_iteration"]["used"] == 2
    assert diagnostics["judger_iteration"]["source"] == "Alice_history.json outer episode count"
    assert diagnostics["judger_iteration"]["limit"] == 1
    assert diagnostics["judger_iteration"]["usage_available"] is True
    assert diagnostics["judger_iteration"]["used"] == 1
    assert diagnostics["root_cause_category"] == "task_not_satisfied"
    assert diagnostics["runtime_task_dag_state"]["terminal_state"] == "failure"
    assert diagnostics["artifact_admission_causality"] == {
        "score": "judger terminal status was not success",
        "runtime_task_dag": "external judger failure was propagated to the runtime task",
        "child_protocol": "judged task failure made the runtime child exit with an error status",
    }


def test_issue_439_legacy_actions_expose_missing_world_state_evidence():
    history = [{
        "action_list": [
            {
                "action": {"tool": "_Exception", "tool_input": "invalid response"},
                "feedback": "Invalid or incomplete response",
            },
            {"action": {"tool": "navigateTo", "tool_input": {"y": -59}}},
            {"action": {"tool": "navigateTo", "tool_input": {"y": -60}}},
        ],
        "final_answer": "arrived",
    }]
    trace = build_judged_iteration_trace(
        action_log={
            "Alice": [
                {"action": "navigateTo", "result": {"status": True}},
                {"action": "navigateTo", "result": {"status": True}},
            ],
        },
        agent_history=history,
        final_score={"status": "failure", "end_reason": "max iteration out"},
    )
    diagnostics = build_judged_terminal_diagnostics(
        summary={
            "final_score": {"status": "failure", "end_reason": "max iteration out"},
            "artifact_admission": {"invalid": ["score", "runtime_task_dag", "child_protocol"]},
        },
        launch_config={
            "task_scenario": "move",
            "evaluation_arg": {"x": 5, "y": -60, "z": 5},
        },
        trace=trace,
        runtime_snapshot={"summary": {"terminal_state": "failure"}},
    )

    assert trace["agent_iteration"]["used"] == 1
    assert len(trace["entries"]) == 4
    assert [item["model_response_type"] for item in trace["entries"]] == [
        "parse_error",
        "structured_action",
        "structured_action",
        "final_answer",
    ]
    assert trace["entries"][0]["action_index_in_episode"] is None
    assert trace["entries"][1]["action_index_in_episode"] == 1
    assert trace["entries"][2]["action_index_in_episode"] == 2
    assert trace["entries"][2]["tool_completion_semantics"] == "legacy_status_only"
    assert trace["entries"][2]["post_action_world_state"]["available"] is False
    assert diagnostics["root_cause_category"] == "completion_contract_mismatch"
    assert diagnostics["root_cause_evidence"]["final_position_reconstructable"] is False
    assert diagnostics["actual_terminal_state"]["reason"] == (
        "post-action position was not captured by the previous runtime"
    )
    assert diagnostics["judger_iteration"]["available"] is False
    assert diagnostics["judger_iteration"]["usage_available"] is False
    assert diagnostics["judger_iteration"]["usage_unavailable_reason"]["code"] == (
        "iteration_not_measured"
    )


def test_judger_usage_is_not_inferred_from_agent_iteration():
    diagnostics = build_judged_terminal_diagnostics(
        summary={
            "final_score": {
                "status": "success",
                "iteration": {
                    "source": "Alice_history.json outer episode count",
                    "limit": 1,
                    "used": None,
                    "terminal_observations": 0,
                    "usage_unavailable_reason": {
                        "code": "history_not_observable_at_terminal_evaluation",
                        "message": "history unavailable at terminal evaluation",
                    },
                },
            },
            "progress": 100,
        },
        launch_config={"task_scenario": "move", "evaluation_arg": {}},
        trace={
            "agent_iteration": {
                "available": True,
                "source": "Alice_history.json outer dict episodes",
                "limit": 7,
                "used": 1,
            },
            "entries": [],
        },
    )

    assert diagnostics["agent_iteration"]["used"] == 1
    assert diagnostics["judger_iteration"]["used"] is None
    assert diagnostics["judger_iteration"]["usage_available"] is False


def test_multiple_history_episodes_are_distinct_from_trace_entries():
    trace = build_judged_iteration_trace(
        action_log={
            "Alice": [
                {"action": "navigateTo", "result": {"status": True}},
                {"action": "navigateTo", "result": {"status": True}},
            ],
        },
        agent_history=[
            {"action_list": [{"action": {"tool": "navigateTo"}}]},
            {"action_list": [{"action": {"tool": "navigateTo"}}]},
        ],
        final_score={},
    )

    assert trace["outer_episode_count"] == 2
    assert trace["agent_iteration"]["used"] == 2
    assert [entry["outer_episode_index"] for entry in trace["entries"]] == [1, 2]


def test_one_episode_can_contain_parse_retry_and_multiple_actions():
    trace = build_judged_iteration_trace(
        action_log={
            "Alice": [
                {"action": "navigateTo", "result": {"status": True}},
                {"action": "navigateTo", "result": {"status": True}},
            ],
        },
        agent_history=[{
            "action_list": [
                {"action": {"tool": "_Exception"}, "feedback": "invalid"},
                {"action": {"tool": "navigateTo"}},
                {"action": {"tool": "navigateTo"}},
            ],
        }],
        final_score={},
    )

    assert trace["agent_iteration"]["used"] == 1
    assert len(trace["entries"]) == 3
    assert [entry["action_index_in_episode"] for entry in trace["entries"]] == [
        None,
        1,
        2,
    ]


def test_missing_history_does_not_infer_agent_iterations():
    trace = build_judged_iteration_trace(
        action_log={"Alice": [{"action": "navigateTo", "result": {"status": True}}]},
        agent_history=None,
        final_score={},
    )

    assert trace["outer_episode_count"] is None
    assert trace["agent_iteration"] == {
        "available": False,
        "source": None,
        "limit": None,
        "limit_source": None,
        "limit_available": False,
        "used": None,
        "reason": "not captured by runtime",
    }
    assert trace["entries"][0]["outer_episode_index"] is None


def test_malformed_history_elements_are_not_counted_as_episodes():
    trace = build_judged_iteration_trace(
        action_log={"Alice": [{"action": "navigateTo", "result": {"status": True}}]},
        agent_history=[None, {"action_list": [{"action": {"tool": "navigateTo"}}]}],
        final_score={},
    )

    assert trace["outer_episode_count"] == 1
    assert trace["malformed_outer_episode_count"] == 1
    assert trace["agent_iteration"]["used"] == 1
