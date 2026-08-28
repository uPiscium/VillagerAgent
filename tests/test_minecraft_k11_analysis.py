from benchmarks.common.eac import Proposition, PropositionKey
from benchmarks.minecraft.eac_runtime import MinecraftEACRuntime
from benchmarks.minecraft.k11_analysis import analyze_trace, replay_admissibility, validate_p0_analysis
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


def _prepare_at(runtime, x, y, z):
    return runtime.prepare_tool(
        "MineBlock", _mine, (),
        {"player_name": "Alice", "x": x, "y": y, "z": z, "emotion": [], "murmur": ""},
    )


def _window_after_first_prepare(artifact, *, reason="fixed_observation_horizon"):
    artifact = deepcopy(artifact)
    original = artifact["events"]
    pivot = next(
        index for index, event in enumerate(original)
        if event["event_type"] == "k11.eac_action_prepared"
    )
    opened_ns = 1_000
    horizon_ns = opened_ns + 1_000_000_000
    close_ns = horizon_ns if reason == "fixed_observation_horizon" else 10_000
    opened = {
        "seq": 1,
        "event_id": artifact["run_id"] + ":window-open",
        "event_type": "k11.observation_window_opened",
        "source": "test",
        "payload": {
            "configured_horizon_seconds": 1,
            "horizon_monotonic_ns": horizon_ns,
        },
        "monotonic_ns": opened_ns,
        "thread_id": 1,
        "run_id": artifact["run_id"],
        "task_id": None,
        "actor_id": None,
        "agent_step_id": None,
        "tool_call_id": None,
    }
    closed = {
        **opened,
        "event_id": artifact["run_id"] + ":window-close",
        "event_type": "k11.observation_window_closed",
        "payload": {
            "reason": reason,
            "configured_horizon_seconds": 1,
            "window_close_monotonic_ns": close_ns,
            "shutdown_requested": reason == "fixed_observation_horizon",
        },
        "monotonic_ns": close_ns,
    }
    rebuilt = [opened]
    for index, event in enumerate(original):
        event["monotonic_ns"] = (
            opened_ns + index + 1 if index <= pivot else close_ns + index + 1
        )
        rebuilt.append(event)
        if index == pivot:
            rebuilt.append(closed)
    for seq, event in enumerate(rebuilt, 1):
        event["seq"] = seq
    artifact["events"] = rebuilt
    return artifact


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


def test_k11_offline_analysis_recognizes_controlled_positive_replacement_as_n1() -> None:
    """Development classifier fixture only; never a natural prevalence observation."""
    runtime, trace = _runtime("k11-replay-replacement")
    scope = K11TraceScope(
        trace.run_id, task_id="task-1", actor_id="Alice",
        agent_step_id="step-1", tool_call_id="tool-1",
    )
    successor_scope = K11TraceScope(
        trace.run_id, task_id="task-1", actor_id="Alice",
        agent_step_id="step-1", tool_call_id="tool-2",
    )
    with use_scope(scope):
        runtime.ingest_target_observation("Alice", "MineBlock", {"x": 1, "y": 2, "z": 3})
        original = _prepare_at(runtime, 1, 2, 3)
        runtime._ingest_current_fluent(
            "Alice",
            Proposition(
                PropositionKey("minecraft", "target_block_present", (1, 2, 3), "current"),
                polarity=False,
            ),
            source="minecraft-visible-observation",
        )
        trace.record(
            "k11.tool_call_exited",
            source="controlled-development-fixture",
            payload={"outcome": "returned"},
        )
    with use_scope(successor_scope):
        runtime.ingest_target_observation("Alice", "MineBlock", {"x": 4, "y": 5, "z": 6})
        successor = _prepare_at(runtime, 4, 5, 6)
        runtime.execute_prepared(successor)
    with use_scope(K11TraceScope(
        trace.run_id, task_id="task-1", actor_id="Alice", agent_step_id="step-1",
    )):
        trace.record(
            "k11.agent_step_completed",
            source="controlled-development-fixture",
            payload={"outcome": "returned"},
        )

    analysis = analyze_trace(trace.artifact())

    assert analysis["trace_validation"]["valid"] is True
    assert analysis["taxonomy"]["N1"] == 1
    original_row = next(row for row in analysis["actions"] if row["candidate_id"] == original.request.candidate_id)
    assert original_row["D1"] is True
    assert original_row["D2"] is True
    assert original_row["D3"] is True
    assert original_row["D4"] is True
    assert original_row["D5"] is True
    assert original_row["D6"] is False
    assert original_row["EAdm_prepare"] is True
    assert original_row["EAdm_disposition"] is False
    assert original_row["taxonomy"] == "N1"
    assert original_row["disposition_kind"] == "replacement"


def test_k11_offline_analysis_keeps_ambiguous_disappearance_unresolved() -> None:
    runtime, trace = _runtime("k11-replay-unresolved")
    runtime.ingest_target_observation("Alice", "MineBlock", {"x": 1, "y": 2, "z": 3})
    _prepare(runtime)

    analysis = analyze_trace(trace.artifact())

    assert analysis["taxonomy"]["N1"] == 0
    assert analysis["actions"][0]["qc_state"] == "disposition_unresolved"


def test_k11_fixed_window_right_censors_prepare_without_in_window_disposition() -> None:
    runtime, trace = _runtime("k11-window-censored")
    scope = K11TraceScope(
        trace.run_id, task_id="task-1", actor_id="Alice",
        agent_step_id="step-1", tool_call_id="tool-1",
    )
    with use_scope(scope):
        trace.record("k11.agent_step_started", source="test")
        trace.record("k11.model_call_started", source="test", payload={"model_call_id": "model-1"})
        trace.record("k11.model_call_completed", source="test", payload={"model_call_id": "model-1"})
        trace.record("k11.tool_call_entered", source="test")
        runtime.ingest_target_observation("Alice", "MineBlock", {"x": 1, "y": 2, "z": 3})
        runtime.execute_prepared(_prepare(runtime))
        trace.record("k11.tool_call_exited", source="test")
        trace.record("k11.agent_step_completed", source="test")
    artifact = _window_after_first_prepare(trace.artifact())

    analysis = analyze_trace(artifact)

    assert analysis["trace_validation"]["valid"] is True
    assert analysis["denominators"] == {
        "D1": 1, "D2": 0, "D3": 0, "D4": 0, "D5": 0, "D6": 0,
    }
    assert analysis["prepared_inside_window"] == 1
    assert analysis["complete_dispositions_inside_window"] == 0
    assert analysis["window_censored_preparations"] == 1
    assert analysis["censoring_fraction"] == 1.0
    assert analysis["actions"][0]["qc_state"] == "observation_window_censored"
    assert validate_p0_analysis(analysis, artifact)["valid"] is True


def test_k11_natural_close_does_not_relabel_missing_disposition_as_censored() -> None:
    runtime, trace = _runtime("k11-window-natural-unresolved")
    runtime.ingest_target_observation("Alice", "MineBlock", {"x": 1, "y": 2, "z": 3})
    _prepare(runtime)
    artifact = _window_after_first_prepare(
        trace.artifact(), reason="natural_runtime_terminal",
    )

    analysis = analyze_trace(artifact)

    assert analysis["window_censored_preparations"] == 0
    assert analysis["actions"][0]["qc_state"] == "disposition_unresolved"


def test_k11_observation_window_uses_half_open_end_boundary() -> None:
    runtime, trace = _runtime("k11-window-half-open")
    runtime.ingest_target_observation("Alice", "MineBlock", {"x": 1, "y": 2, "z": 3})
    runtime.execute_prepared(_prepare(runtime))
    artifact = _window_after_first_prepare(trace.artifact())
    close_ns = next(
        event["monotonic_ns"] for event in artifact["events"]
        if event["event_type"] == "k11.observation_window_closed"
    )
    prepared = next(
        event for event in artifact["events"]
        if event["event_type"] == "k11.eac_action_prepared"
    )
    prepared["monotonic_ns"] = close_ns

    analysis = analyze_trace(artifact)

    assert analysis["prepared_inside_window"] == 0
    assert analysis["denominators"]["D1"] == 0


def test_k11_p0_analysis_rejects_trace_failure_even_without_analysis_error() -> None:
    runtime, trace = _runtime("k11-analysis-gate")
    runtime.ingest_target_observation("Alice", "MineBlock", {"x": 1, "y": 2, "z": 3})
    runtime.execute_prepared(_prepare(runtime))
    analysis = analyze_trace(trace.artifact())
    analysis["p0_trace_validation"] = {"valid": False, "errors": ["missing lifecycle"]}
    result = validate_p0_analysis(analysis)
    assert result["valid"] is False
    assert any("trace validation" in error for error in result["errors"])


def test_k11_p0_analysis_accepts_inadmissible_baseline_after_complete_replay() -> None:
    analysis = {
        "artifact_id": "minecraft-k11-trace-analysis-draft",
        "prevalence_inference_allowed": False,
        "p0_trace_validation": {"valid": True},
        "denominators": {"D1": 1, "D2": 0, "D3": 0, "D4": 0, "D5": 0, "D6": 0},
        "actions": [{
            "tool_name": "MineBlock", "D1": True,
            "EAdm_prepare": False, "EAdm_disposition": False,
            "qc_state": "prepared_inadmissible_baseline",
        }],
    }
    assert validate_p0_analysis(analysis)["valid"] is True


def test_k11_p0_analysis_rejects_non_boolean_or_missing_replay_results() -> None:
    analysis = {
        "artifact_id": "minecraft-k11-trace-analysis-draft",
        "prevalence_inference_allowed": False,
        "p0_trace_validation": {"valid": True},
        "denominators": {"D1": 1, "D2": 0, "D3": 0, "D4": 0, "D5": 0, "D6": 0},
        "actions": [{
            "tool_name": "MineBlock", "D1": True,
            "EAdm_prepare": True, "EAdm_disposition": None,
        }],
    }
    result = validate_p0_analysis(analysis)
    assert result["valid"] is False
    assert any("replay" in error for error in result["errors"])


def test_k11_p0_analysis_rejects_top_level_analysis_error() -> None:
    analysis = {
        "artifact_id": "minecraft-k11-trace-analysis-draft",
        "prevalence_inference_allowed": False,
        "analysis_error": "replay crashed",
        "p0_trace_validation": {"valid": True},
        "denominators": {"D1": 1, "D2": 0, "D3": 0, "D4": 0, "D5": 0, "D6": 0},
        "actions": [{
            "tool_name": "MineBlock", "D1": True,
            "EAdm_prepare": True, "EAdm_disposition": True,
        }],
    }
    result = validate_p0_analysis(analysis)
    assert result["valid"] is False
    assert any("top-level analysis error" in error for error in result["errors"])


def test_k11_p0_analysis_rejects_inconsistent_higher_denominator() -> None:
    analysis = {
        "artifact_id": "minecraft-k11-trace-analysis-draft",
        "prevalence_inference_allowed": False,
        "p0_trace_validation": {"valid": True},
        "denominators": {"D1": 1, "D2": 1, "D3": 0, "D4": 0, "D5": 0, "D6": 0},
        "actions": [{
            "tool_name": "MineBlock", "D1": True, "D2": False,
            "EAdm_prepare": False, "EAdm_disposition": False,
            "qc_state": "prepared_inadmissible_baseline",
        }],
    }
    result = validate_p0_analysis(analysis)
    assert result["valid"] is False
    assert any("D2 denominator" in error for error in result["errors"])


def test_k11_p0_analysis_rejects_dropped_primary_trace_candidate(monkeypatch) -> None:
    monkeypatch.setattr(
        "benchmarks.minecraft.k11_analysis.validate_p0_trace",
        lambda unused: {"valid": True},
    )
    trace = {"events": [{
        "event_type": "k11.eac_action_prepared",
        "payload": {"exact_request": {
            "candidate_id": candidate,
            "action": {"identity": "MineBlock"},
        }},
    } for candidate in ("candidate-1", "candidate-2")]}
    analysis = {
        "artifact_id": "minecraft-k11-trace-analysis-draft",
        "prevalence_inference_allowed": False,
        "denominators": {"D1": 1, "D2": 0, "D3": 0, "D4": 0, "D5": 0, "D6": 0},
        "actions": [{
            "candidate_id": "candidate-1", "tool_name": "MineBlock", "D1": True,
            "EAdm_prepare": False, "EAdm_disposition": False,
            "qc_state": "prepared_inadmissible_baseline",
        }],
    }
    result = validate_p0_analysis(analysis, trace)
    assert result["valid"] is False
    assert any("every primary trace candidate" in error for error in result["errors"])


def test_k11_p0_analysis_rejects_malformed_validation_structures() -> None:
    result = validate_p0_analysis({
        "artifact_id": "minecraft-k11-trace-analysis-draft",
        "prevalence_inference_allowed": False,
        "p0_trace_validation": [],
        "denominators": [],
        "actions": [],
    })
    assert result["valid"] is False
from copy import deepcopy
