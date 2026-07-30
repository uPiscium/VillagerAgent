from types import SimpleNamespace

from benchmarks.minecraft.judged_diagnostics import (
    build_judged_iteration_trace,
    build_judged_terminal_diagnostics,
)
from env.movement_diagnostics import movement_completion


def test_movement_completion_uses_judger_strict_axis_tolerance():
    target = SimpleNamespace(x=5, y=-60, z=5)

    boundary = movement_completion(
        SimpleNamespace(x=5, y=-59, z=5),
        target,
        1,
    )
    reached = movement_completion(
        SimpleNamespace(x=5, y=-59.001, z=5),
        target,
        1,
    )

    assert boundary["target_reached"] is False
    assert boundary["axis_delta"] == {"x": 0.0, "y": 1.0, "z": 0.0}
    assert reached["target_reached"] is True
    assert reached["completion_semantics"] == "all_axis_deltas_strictly_below_tolerance"


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
    )

    assert trace[0]["tool_status"] is True
    assert trace[0]["post_action_world_state"]["available"] is True
    assert trace[0]["task_state_after"] == "unknown"
    assert trace[1]["model_response_type"] == "final_answer"
    assert trace[1]["judger_progress_after"] == 0


def test_terminal_diagnostics_identifies_external_judger_iteration_source():
    diagnostics = build_judged_terminal_diagnostics(
        summary={
            "final_score": {
                "status": "failure",
                "progress": 0,
                "end_reason": "max iteration out",
                "iteration": {
                    "source": "external_judger_history_episode_count",
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
        trace=[],
        runtime_snapshot={"summary": {"terminal_state": "failure"}},
    )

    assert diagnostics["judger_iteration_source"] == "external_judger_history_episode_count"
    assert diagnostics["judger_iteration_limit"] == 1
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

    assert [item["model_response_type"] for item in trace] == [
        "parse_error",
        "structured_action",
        "structured_action",
        "final_answer",
    ]
    assert trace[2]["tool_completion_semantics"] == "legacy_status_only"
    assert trace[2]["post_action_world_state"]["available"] is False
    assert diagnostics["root_cause_category"] == "world_state_not_observed"
    assert diagnostics["judger_iteration_source"] is None
