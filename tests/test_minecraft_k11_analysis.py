from benchmarks.common.eac import Proposition, PropositionKey
from benchmarks.minecraft.eac_runtime import MinecraftEACRuntime
from benchmarks.minecraft.k11_analysis import analyze_trace, replay_admissibility
from benchmarks.minecraft.k11_instrumentation import instrument_runtime
from benchmarks.minecraft.k11_trace import K11TraceRecorder, K11TraceScope, use_scope


def _mine(**kwargs):
    return {"status": True, "message": "ok"}


def _runtime(run_id: str):
    runtime = MinecraftEACRuntime(
        mode="dual_dag_advisory",
        run_id=run_id,
        env_prechecks={"MineBlock": lambda unused: True},
        audit_path=None,
    )
    trace = K11TraceRecorder(run_id)
    instrument_runtime(runtime, trace)
    return runtime, trace


def _prepare(runtime):
    return runtime.prepare_tool(
        "MineBlock",
        _mine,
        (),
        {
            "player_name": "Alice",
            "x": 1,
            "y": 2,
            "z": 3,
            "emotion": [],
            "murmur": "",
        },
    )


def test_k11_offline_replay_matches_positive_prepare_state() -> None:
    runtime, trace = _runtime("k11-replay-positive")
    scope = K11TraceScope(
        trace.run_id,
        task_id="task-1",
        actor_id="Alice",
        agent_step_id="step-1",
        tool_call_id="tool-1",
    )
    with use_scope(scope):
        runtime.ingest_target_observation(
            "Alice", "MineBlock", {"x": 1, "y": 2, "z": 3}
        )
        prepared = _prepare(runtime)

    artifact = trace.artifact()
    prepared_event = next(
        event for event in artifact["events"]
        if event["event_type"] == "k11.eac_action_prepared"
    )
    result = replay_admissibility(
        artifact,
        prepared_event,
        cutoff_seq=prepared_event["seq"],
        replay_label="positive",
    )

    assert result["admissible"] is True
    assert result["dependency_ids"]
    assert prepared.request.action.digest == prepared_event["payload"]["exact_request"]["action"]["digest"]


def test_k11_offline_analysis_recognizes_controlled_relevant_invalidation_fixture() -> None:
    """Development fixture only; this is not a natural K11 prevalence observation."""
    runtime, trace = _runtime("k11-replay-invalidated")
    scope = K11TraceScope(
        trace.run_id,
        task_id="task-1",
        actor_id="Alice",
        agent_step_id="step-1",
        tool_call_id="tool-1",
    )
    with use_scope(scope):
        runtime.ingest_target_observation(
            "Alice", "MineBlock", {"x": 1, "y": 2, "z": 3}
        )
        prepared = _prepare(runtime)
        negative = Proposition(
            PropositionKey("minecraft", "target_block_present", (1, 2, 3), "current"),
            polarity=False,
        )
        runtime._ingest_current_fluent(
            "Alice",
            negative,
            source="minecraft-visible-observation",
        )
        runtime.execute_prepared(prepared)

    analysis = analyze_trace(trace.artifact())

    assert analysis["prevalence_inference_allowed"] is False
    assert analysis["trace_validation"]["valid"] is True
    assert analysis["denominators"] == {
        "D1": 1,
        "D2": 1,
        "D3": 1,
        "D4": 1,
        "D5": 1,
        "D6": 1,
    }
    assert analysis["taxonomy"] == {
        "N0": 0,
        "N1": 0,
        "N2": 1,
        "N3": 0,
        "N4": 0,
    }
    action = analysis["actions"][0]
    assert action["EAdm_prepare"] is True
    assert action["EAdm_disposition"] is False
    assert action["native_effect_entered"] is True
    assert action["prepare_to_decision_ns"] > 0
