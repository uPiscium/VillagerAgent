from copy import deepcopy

from benchmarks.minecraft.eac_runtime import MinecraftEACRuntime
from benchmarks.minecraft.k11_instrumentation import instrument_runtime
from benchmarks.minecraft.k11_trace import (
    K11TraceRecorder,
    exact_request_digest,
    validate_trace,
)


def _mine(*, player_name, x, y, z, emotion=None, murmur=""):
    return {"status": True, "message": f"mined {x},{y},{z}"}


def _runtime(run_id="k11-test"):
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


def test_k11_advisory_prepare_does_not_add_measurement_evaluation() -> None:
    runtime, trace = _runtime("k11-no-extra-eval")
    runtime.ingest_target_observation(
        "Alice", "MineBlock", {"x": 1, "y": 2, "z": 3}
    )

    original_evaluate = runtime.authority.evaluate
    calls = []

    def counted(candidate_id):
        calls.append(candidate_id)
        return original_evaluate(candidate_id)

    runtime.authority.evaluate = counted
    prepared = _prepare(runtime)

    assert calls == []
    runtime.execute_prepared(prepared)
    assert calls

    artifact = trace.artifact()
    assert validate_trace(artifact)["valid"] is True


def test_k11_trace_correlates_exact_request_decision_native_and_terminal() -> None:
    runtime, trace = _runtime("k11-correlate")
    runtime.ingest_target_observation(
        "Alice", "MineBlock", {"x": 1, "y": 2, "z": 3}
    )
    prepared = _prepare(runtime)
    result = runtime.execute_prepared(prepared)

    assert result["status"] is True
    artifact = trace.artifact()
    validation = validate_trace(artifact)
    assert validation["valid"] is True
    assert validation["counts"]["prepared"] == 1
    assert validation["counts"]["execution_decisions"] == 1
    assert validation["counts"]["native_entries"] == 1
    assert validation["counts"]["native_completions"] == 1
    assert validation["counts"]["terminals"] == 1
    assert validation["counts"]["evidence_ingestions"] >= 1

    by_type = {}
    for event in artifact["events"]:
        by_type.setdefault(event["event_type"], []).append(event)

    prepared_event = by_type["k11.eac_action_prepared"][0]
    decision_event = by_type["k11.eac_execution_decision_attempted"][0]
    native_event = by_type["k11.eac_native_effect_entered"][0]
    terminal_event = by_type["k11.eac_action_terminal"][0]

    digests = {
        event["payload"]["exact_request_digest"]
        for event in (prepared_event, decision_event, native_event, terminal_event)
    }
    assert len(digests) == 1
    assert decision_event["monotonic_ns"] > prepared_event["monotonic_ns"]

    request = prepared_event["payload"]["exact_request"]
    assert request["candidate_id"] == prepared.request.candidate_id
    assert request["attempt_id"] == prepared.request.attempt_id
    assert request["action"]["identity"] == "MineBlock"
    assert request["arguments"] == {"x": 1, "y": 2, "z": 3}
    assert request["target"] == {"x": 1, "y": 2, "z": 3}
    assert prepared_event["payload"]["exact_request_digest"] == exact_request_digest(request)


def test_k11_evidence_event_preserves_actor_visible_semantic_identity() -> None:
    runtime, trace = _runtime("k11-evidence")
    runtime.ingest_target_observation(
        "Alice", "MineBlock", {"x": 4, "y": 5, "z": 6}
    )

    evidence = [
        event for event in trace.artifact()["events"]
        if event["event_type"] == "k11.eac_evidence_ingested"
    ]
    assert len(evidence) == 1
    event = evidence[0]
    assert event["actor_id"] == "Alice"
    assert event["payload"]["visible_to"] == ["Alice"]
    assert event["payload"]["record_type"] == "direct_observation"
    assert event["payload"]["proposition"] == {
        "namespace": "minecraft",
        "predicate": "target_block_present",
        "arguments": [4, 5, 6],
        "temporal_scope": "current",
        "polarity": True,
    }


def test_k11_trace_validator_rejects_exact_request_substitution() -> None:
    runtime, trace = _runtime("k11-substitution")
    runtime.ingest_target_observation(
        "Alice", "MineBlock", {"x": 1, "y": 2, "z": 3}
    )
    prepared = _prepare(runtime)
    runtime.execute_prepared(prepared)

    artifact = deepcopy(trace.artifact())
    decision = next(
        event for event in artifact["events"]
        if event["event_type"] == "k11.eac_execution_decision_attempted"
    )
    decision["payload"]["exact_request_digest"] = "sha256:" + "0" * 64

    validation = validate_trace(artifact)
    assert validation["valid"] is False
    assert any("exact request changed" in error for error in validation["errors"])


def test_k11_trace_contains_no_online_natural_classification_labels() -> None:
    runtime, trace = _runtime("k11-no-online-labels")
    runtime.ingest_target_observation(
        "Alice", "MineBlock", {"x": 1, "y": 2, "z": 3}
    )
    runtime.execute_prepared(_prepare(runtime))

    serialized = str(trace.artifact())
    for label in ("N0", "N1", "N2", "N3", "N4", "reconsidered", "invalidated"):
        assert label not in serialized
