from copy import deepcopy

from benchmarks.common.eac import Proposition, PropositionKey
from benchmarks.minecraft.eac_runtime import MinecraftEACError, MinecraftEACRuntime
from benchmarks.minecraft.k11_instrumentation import instrument_runtime
from benchmarks.minecraft.k11_trace import (
    K11TraceRecorder,
    K11TraceScope,
    derive_positive_disposition,
    exact_request_digest,
    use_scope,
    valid_evidence_ingestion,
    validate_p0_trace,
    validate_trace,
)


def _mine(*, player_name, x, y, z, emotion=None, murmur=""):
    return {"status": True, "message": f"mined {x},{y},{z}"}


def _runtime(run_id="k11-test", *, env_precheck=True):
    runtime = MinecraftEACRuntime(
        mode="dual_dag_advisory",
        run_id=run_id,
        env_prechecks={"MineBlock": lambda unused: env_precheck},
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


def _with_natural_window(artifact):
    artifact = deepcopy(artifact)
    events = artifact["events"]
    opened_ns = min(event["monotonic_ns"] for event in events) - 1
    closed_ns = max(event["monotonic_ns"] for event in events) + 1
    horizon_seconds = 3600
    common = {
        "run_id": artifact["run_id"],
        "task_id": None,
        "actor_id": None,
        "agent_step_id": None,
        "tool_call_id": None,
        "source": "test",
        "thread_id": 1,
    }
    opened = {
        **common,
        "event_id": artifact["run_id"] + ":window-open",
        "event_type": "k11.observation_window_opened",
        "monotonic_ns": opened_ns,
        "payload": {
            "configured_horizon_seconds": horizon_seconds,
            "horizon_monotonic_ns": opened_ns + horizon_seconds * 1_000_000_000,
        },
    }
    closed = {
        **common,
        "event_id": artifact["run_id"] + ":window-close",
        "event_type": "k11.observation_window_closed",
        "monotonic_ns": closed_ns,
        "payload": {
            "reason": "natural_runtime_terminal",
            "configured_horizon_seconds": horizon_seconds,
            "window_close_monotonic_ns": closed_ns,
            "shutdown_requested": False,
        },
    }
    artifact["events"] = [opened, *events, closed]
    for seq, event in enumerate(artifact["events"], 1):
        event["seq"] = seq
    return artifact


def _with_fixed_close_after(artifact, event_type):
    artifact = deepcopy(artifact)
    events = [
        event for event in artifact["events"]
        if not event["event_type"].startswith("k11.observation_window_")
    ]
    pivot = next(index for index, event in enumerate(events)
                 if event["event_type"] == event_type)
    opened_ns = 1_000
    closed_ns = opened_ns + 1_000_000_000
    common = {
        "run_id": artifact["run_id"], "task_id": None, "actor_id": None,
        "agent_step_id": None, "tool_call_id": None, "source": "test", "thread_id": 1,
    }
    opened = {
        **common, "event_id": artifact["run_id"] + ":window-open",
        "event_type": "k11.observation_window_opened", "monotonic_ns": opened_ns,
        "payload": {"configured_horizon_seconds": 1, "horizon_monotonic_ns": closed_ns},
    }
    closed = {
        **common, "event_id": artifact["run_id"] + ":window-close",
        "event_type": "k11.observation_window_closed", "monotonic_ns": closed_ns,
        "payload": {
            "reason": "fixed_observation_horizon", "configured_horizon_seconds": 1,
            "window_close_monotonic_ns": closed_ns, "shutdown_requested": True,
        },
    }
    rebuilt = [opened]
    for index, event in enumerate(events):
        event["monotonic_ns"] = (
            opened_ns + index + 1 if index <= pivot else closed_ns + index + 1
        )
        rebuilt.append(event)
        if index == pivot:
            rebuilt.append(closed)
    for seq, event in enumerate(rebuilt, 1):
        event["seq"] = seq
    artifact["events"] = rebuilt
    return artifact


def _complete_p0_artifact(run_id="k11-p0-complete"):
    runtime, trace = _runtime(run_id)
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
    return _with_natural_window(trace.artifact())


def _complete_zero_evidence_p0_artifact(run_id="k11-p0-zero-evidence"):
    runtime, trace = _runtime(run_id, env_precheck=False)
    scope = K11TraceScope(
        trace.run_id, task_id="task-1", actor_id="Alice",
        agent_step_id="step-1", tool_call_id="tool-1",
    )
    with use_scope(scope):
        trace.record("k11.agent_step_started", source="test")
        trace.record("k11.model_call_started", source="test", payload={"model_call_id": "model-1"})
        trace.record("k11.model_call_completed", source="test", payload={"model_call_id": "model-1"})
        trace.record("k11.tool_call_entered", source="test")
        try:
            runtime.execute_prepared(_prepare(runtime))
        except MinecraftEACError:
            trace.record("k11.tool_call_exited", source="test", payload={"outcome": "raised"})
        trace.record("k11.agent_step_completed", source="test")
    return _with_natural_window(trace.artifact())


def _complete_p0_abandonment_artifact(run_id="k11-p0-abandonment"):
    runtime, trace = _runtime(run_id)
    agent_scope = K11TraceScope(
        trace.run_id, task_id="task-1", actor_id="Alice", agent_step_id="step-1",
    )
    first_scope = K11TraceScope(
        trace.run_id, task_id="task-1", actor_id="Alice",
        agent_step_id="step-1", tool_call_id="tool-1",
    )
    second_scope = K11TraceScope(
        trace.run_id, task_id="task-1", actor_id="Alice",
        agent_step_id="step-1", tool_call_id="tool-2",
    )
    with use_scope(agent_scope):
        trace.record("k11.agent_step_started", source="test")
        trace.record("k11.model_call_started", source="test", payload={"model_call_id": "model-1"})
        trace.record("k11.model_call_completed", source="test", payload={"model_call_id": "model-1"})
    with use_scope(first_scope):
        trace.record("k11.tool_call_entered", source="test")
        runtime.ingest_target_observation("Alice", "MineBlock", {"x": 1, "y": 2, "z": 3})
        _prepare_at(runtime, 1, 2, 3)
        runtime._ingest_current_fluent(
            "Alice",
            Proposition(
                PropositionKey("minecraft", "target_block_present", (1, 2, 3), "current"),
                polarity=False,
            ),
            source="minecraft-visible-observation",
        )
        trace.record("k11.tool_call_exited", source="test", payload={"outcome": "returned"})
    with use_scope(second_scope):
        trace.record("k11.tool_call_entered", source="test")
        runtime.ingest_target_observation("Alice", "MineBlock", {"x": 4, "y": 5, "z": 6})
        successor = _prepare_at(runtime, 4, 5, 6)
        runtime.execute_prepared(successor)
        trace.record("k11.tool_call_exited", source="test")
    with use_scope(agent_scope):
        trace.record("k11.agent_step_completed", source="test", payload={"outcome": "returned"})
    return _with_natural_window(trace.artifact())


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


def test_k11_p0_trace_rejects_high_level_only_artifact() -> None:
    artifact = {"schema_version": "minecraft-k11-trace/2", "run_id": "empty", "events": []}
    assert validate_p0_trace(artifact)["valid"] is False


def test_k11_p0_trace_rejects_primary_digest_mismatch() -> None:
    runtime, trace = _runtime("k11-p0-digest")
    runtime.ingest_target_observation("Alice", "MineBlock", {"x": 1, "y": 2, "z": 3})
    runtime.execute_prepared(_prepare(runtime))
    artifact = deepcopy(trace.artifact())
    terminal = next(event for event in artifact["events"] if event["event_type"] == "k11.eac_action_terminal")
    terminal["payload"]["exact_request_digest"] = "sha256:" + "f" * 64
    assert validate_p0_trace(artifact)["valid"] is False


def test_k11_p0_trace_accepts_complete_correlated_run() -> None:
    validation = validate_p0_trace(_complete_p0_artifact())
    assert validation["valid"] is True
    assert validation["counts"]["prepared"] == 1


def test_k11_p0_trace_accepts_complete_zero_evidence_run() -> None:
    validation = validate_p0_trace(_complete_zero_evidence_p0_artifact())

    assert validation["valid"] is True
    assert validation["counts"]["evidence_ingestions"] == 0
    assert validation["counts"]["prepared"] == 1


def test_k11_p0_trace_rejects_malformed_evidence_identity() -> None:
    artifact = _complete_p0_artifact("k11-p0-malformed-evidence")
    evidence = next(
        event for event in artifact["events"]
        if event["event_type"] == "k11.eac_evidence_ingested"
    )
    evidence["payload"]["visible_to"] = []

    validation = validate_p0_trace(artifact)

    assert validation["valid"] is False
    assert any("evidence ingestion" in error for error in validation["errors"])


def test_k11_non_stream_evidence_retains_replay_identity_without_stream_fields() -> None:
    artifact = _complete_p0_artifact("k11-p0-non-stream-evidence")
    evidence = next(
        event for event in artifact["events"]
        if event["event_type"] == "k11.eac_evidence_ingested"
    )
    for record_type in ("trusted_tool_result", "peer_report"):
        candidate = deepcopy(evidence)
        candidate["payload"]["record_type"] = record_type
        candidate["payload"]["source_stream_id"] = None
        candidate["payload"]["source_stream_revision"] = None
        candidate["payload"]["supersedes"] = []
        assert valid_evidence_ingestion(candidate, run_id=artifact["run_id"]) is True


def test_k11_stream_evidence_requires_matching_integer_revision() -> None:
    artifact = _complete_p0_artifact("k11-p0-stream-revision")
    evidence = next(
        event for event in artifact["events"]
        if event["event_type"] == "k11.eac_evidence_ingested"
        and event["payload"]["record_type"] == "direct_observation"
    )
    for revision, stream_revision in (("1", 1), (1, 2)):
        candidate = deepcopy(evidence)
        candidate["payload"]["revision"] = revision
        candidate["payload"]["source_stream_revision"] = stream_revision
        assert valid_evidence_ingestion(candidate, run_id=artifact["run_id"]) is False


def test_k11_evidence_rejects_noncanonical_proposition_argument() -> None:
    artifact = _complete_p0_artifact("k11-p0-evidence-argument")
    evidence = next(
        event for event in artifact["events"]
        if event["event_type"] == "k11.eac_evidence_ingested"
    )
    evidence["payload"]["proposition"]["arguments"] = [1.5]

    assert valid_evidence_ingestion(evidence, run_id=artifact["run_id"]) is False
    assert validate_p0_trace(artifact)["valid"] is False


def test_k11_p0_trace_rejects_missing_or_mismatched_run_identity() -> None:
    artifact = _complete_p0_artifact("k11-p0-run-identity")
    artifact["run_id"] = ""
    assert validate_p0_trace(artifact)["valid"] is False

    artifact = _complete_p0_artifact("k11-p0-run-identity")
    artifact["events"][1]["run_id"] = "another-run"
    validation = validate_p0_trace(artifact)
    assert validation["valid"] is False
    assert any("run identity" in error for error in validation["errors"])


def test_k11_p0_zero_evidence_run_still_rejects_malformed_lifecycle() -> None:
    artifact = _complete_zero_evidence_p0_artifact("k11-p0-zero-evidence-malformed")
    artifact["events"] = [
        event for event in artifact["events"]
        if event["event_type"] != "k11.tool_call_exited"
    ]

    validation = validate_p0_trace(artifact)

    assert validation["valid"] is False
    assert any("tool lifecycle" in error for error in validation["errors"])


def test_k11_p0_trace_requires_declared_observation_window() -> None:
    artifact = _complete_p0_artifact("k11-p0-window-required")
    artifact["events"] = [
        event for event in artifact["events"]
        if not event["event_type"].startswith("k11.observation_window_")
    ]
    for seq, event in enumerate(artifact["events"], 1):
        event["seq"] = seq

    assert validate_trace(artifact)["valid"] is True
    validation = validate_p0_trace(artifact)
    assert validation["valid"] is False
    assert any("observation window" in error for error in validation["errors"])


def test_k11_trace_rejects_natural_close_at_fixed_horizon() -> None:
    artifact = _complete_p0_artifact("k11-natural-at-horizon")
    opened = artifact["events"][0]
    closed = artifact["events"][-1]
    horizon_ns = opened["payload"]["horizon_monotonic_ns"]
    closed["monotonic_ns"] = horizon_ns
    closed["payload"]["window_close_monotonic_ns"] = horizon_ns

    validation = validate_trace(artifact)

    assert validation["valid"] is False
    assert any("natural observation close" in error for error in validation["errors"])


def test_k11_trace_accepts_disposition_with_cleanup_after_fixed_close() -> None:
    artifact = _with_fixed_close_after(
        _complete_p0_artifact("k11-p0-cross-window-cleanup"),
        "k11.eac_native_effect_entered",
    )

    assert validate_trace(artifact)["valid"] is True
    assert validate_p0_trace(artifact)["valid"] is True


def test_k11_trace_fails_closed_on_malformed_post_window_terminal() -> None:
    artifact = _with_fixed_close_after(
        _complete_p0_artifact("k11-p0-malformed-cross-window"),
        "k11.eac_native_effect_entered",
    )
    terminal = next(
        event for event in artifact["events"]
        if event["event_type"] == "k11.eac_action_terminal"
    )
    terminal["payload"] = None

    validation = validate_p0_trace(artifact)

    assert validation["valid"] is False
    assert any("terminal" in error for error in validation["errors"])


def test_k11_p0_trace_accepts_positive_replacement_disposition() -> None:
    validation = validate_p0_trace(_complete_p0_abandonment_artifact())

    assert validation["valid"] is True
    assert validation["counts"]["prepared"] == 2
    assert validation["counts"]["positive_abandonments"] == 1


def test_k11_p0_trace_rejects_disappearance_without_positive_tool_exit() -> None:
    artifact = _complete_p0_abandonment_artifact("k11-p0-missing-tool-exit")
    artifact["events"] = [
        event for event in artifact["events"]
        if not (event["event_type"] == "k11.tool_call_exited"
                and event.get("tool_call_id") == "tool-1")
    ]

    validation = validate_p0_trace(artifact)

    assert validation["valid"] is False
    assert any("disposition" in error for error in validation["errors"])


def test_k11_p0_trace_rejects_raised_tool_exit_as_positive_disposition() -> None:
    artifact = _complete_p0_abandonment_artifact("k11-p0-raised-tool-exit")
    first_exit = next(
        event for event in artifact["events"]
        if event["event_type"] == "k11.tool_call_exited" and event.get("tool_call_id") == "tool-1"
    )
    first_exit["payload"]["outcome"] = "raised"

    validation = validate_p0_trace(artifact)

    assert validation["valid"] is False
    assert any("disposition" in error for error in validation["errors"])


def test_k11_p0_trace_rejects_delayed_decision_after_positive_replacement() -> None:
    artifact = _complete_p0_abandonment_artifact("k11-p0-delayed-decision")
    original = next(
        event for event in artifact["events"]
        if event["event_type"] == "k11.eac_action_prepared" and event.get("tool_call_id") == "tool-1"
    )
    delayed = next(
        deepcopy(event) for event in artifact["events"]
        if event["event_type"] == "k11.eac_execution_decision_attempted"
    )
    delayed["seq"] = max(event["seq"] for event in artifact["events"]) + 1
    delayed["monotonic_ns"] = max(event["monotonic_ns"] for event in artifact["events"]) + 1
    delayed["event_id"] = "k11-p0-delayed-decision:k11:delayed"
    delayed["actor_id"] = original["actor_id"]
    delayed["task_id"] = original["task_id"]
    delayed["agent_step_id"] = original["agent_step_id"]
    delayed["tool_call_id"] = original["tool_call_id"]
    delayed["payload"]["exact_request"] = deepcopy(original["payload"]["exact_request"])
    delayed["payload"]["exact_request_digest"] = original["payload"]["exact_request_digest"]
    close = next(
        event for event in artifact["events"]
        if event["event_type"] == "k11.observation_window_closed"
    )
    close["monotonic_ns"] += 2
    close["payload"]["window_close_monotonic_ns"] = close["monotonic_ns"]
    delayed["monotonic_ns"] = close["monotonic_ns"] - 1
    artifact["events"].insert(artifact["events"].index(close), delayed)
    for seq, event in enumerate(artifact["events"], 1):
        event["seq"] = seq

    validation = validate_p0_trace(artifact)

    assert validation["valid"] is False
    assert any("decision after positive abandonment" in error for error in validation["errors"])


def test_k11_positive_disposition_does_not_treat_cross_step_successor_as_replacement() -> None:
    artifact = _complete_p0_abandonment_artifact("k11-p0-cross-step-successor")
    original = next(
        event for event in artifact["events"]
        if event["event_type"] == "k11.eac_action_prepared" and event.get("tool_call_id") == "tool-1"
    )
    successor = next(
        event for event in artifact["events"]
        if event["event_type"] == "k11.eac_action_prepared" and event.get("tool_call_id") == "tool-2"
    )
    successor["agent_step_id"] = "step-2"

    disposition = derive_positive_disposition(artifact, original)

    assert disposition is not None
    assert disposition["kind"] == "cancellation"
    assert disposition["successor_candidate_ids"] == []


def test_k11_trace_validator_fails_closed_on_malformed_event_payload() -> None:
    artifact = _complete_p0_artifact("k11-p0-malformed-payload")
    artifact["events"][0]["payload"] = None

    validation = validate_p0_trace(artifact)

    assert validation["valid"] is False
    assert any("payload is malformed" in error for error in validation["errors"])


def test_k11_p0_trace_rejects_cross_actor_lifecycle_pairing() -> None:
    artifact = _complete_p0_artifact("k11-p0-cross-actor")
    completed = next(
        event for event in artifact["events"]
        if event["event_type"] == "k11.agent_step_completed"
    )
    completed["actor_id"] = "Bob"
    validation = validate_p0_trace(artifact)
    assert validation["valid"] is False
    assert any("agent lifecycle" in error for error in validation["errors"])


def test_k11_p0_trace_rejects_additional_dangling_model_call() -> None:
    artifact = _complete_p0_artifact("k11-p0-dangling-model")
    started = next(
        deepcopy(event) for event in artifact["events"]
        if event["event_type"] == "k11.model_call_started"
    )
    started["seq"] = max(event["seq"] for event in artifact["events"]) + 1
    started["event_id"] = "k11-p0-dangling-model:k11:dangling"
    started["payload"]["model_call_id"] = "model-dangling"
    artifact["events"].append(started)

    validation = validate_p0_trace(artifact)
    assert validation["valid"] is False
    assert any("model lifecycle" in error for error in validation["errors"])


def test_k11_p0_trace_rejects_cross_scope_eac_correlation() -> None:
    artifact = _complete_p0_artifact("k11-p0-cross-scope")
    decision = next(
        event for event in artifact["events"]
        if event["event_type"] == "k11.eac_execution_decision_attempted"
    )
    decision["actor_id"] = "Bob"

    validation = validate_p0_trace(artifact)
    assert validation["valid"] is False
    assert any("not correlated" in error for error in validation["errors"])


def test_k11_p0_trace_rejects_orphan_eac_event() -> None:
    artifact = _complete_p0_artifact("k11-p0-orphan")
    orphan = next(
        deepcopy(event) for event in artifact["events"]
        if event["event_type"] == "k11.eac_execution_decision_attempted"
    )
    orphan["seq"] = max(event["seq"] for event in artifact["events"]) + 1
    orphan["event_id"] = "k11-p0-orphan:k11:orphan"
    orphan["payload"]["exact_request"]["candidate_id"] = "orphan-candidate"
    orphan["payload"]["exact_request_digest"] = exact_request_digest(
        orphan["payload"]["exact_request"]
    )
    artifact["events"].append(orphan)

    validation = validate_p0_trace(artifact)
    assert validation["valid"] is False
    assert any("has no preparation" in error for error in validation["errors"])
