"""Offline-only K11 trace reconstruction and P0 diagnostic analysis.

This module never mutates the measured runtime.  It replays captured actor-visible
evidence into a fresh advisory runtime and evaluates the original action semantics
there.  P0/P1 outputs from this module are diagnostic only unless bound by a
later frozen K11-E protocol.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from benchmarks.common.eac import Proposition, PropositionKey
from benchmarks.common.eac.authority import _proposition_slot
from benchmarks.minecraft.eac_runtime import CLASSIFICATION_PATH, MinecraftEACRuntime
from benchmarks.minecraft.k11_trace import (
    PRIMARY_EFFECT_ACTIONS,
    TRACE_SCHEMA_VERSION,
    _event_precedes,
    derive_positive_disposition,
    validate_p0_trace,
    validate_trace,
)


class K11AnalysisError(ValueError):
    pass


def load_trace(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != TRACE_SCHEMA_VERSION:
        raise K11AnalysisError("invalid K11 trace artifact")
    return value


def _proposition(value: Mapping[str, Any]) -> Proposition:
    return Proposition(
        PropositionKey(
            str(value["namespace"]),
            str(value["predicate"]),
            tuple(value.get("arguments", ())),
            str(value.get("temporal_scope", "")),
        ),
        polarity=value.get("polarity") is True,
    )


def _runtime(run_id: str) -> MinecraftEACRuntime:
    classification = json.loads(CLASSIFICATION_PATH.read_text(encoding="utf-8"))
    actions = classification.get("actions", [])
    names = [row["action_identity"] for row in actions if isinstance(row, Mapping)]
    pass_checks = {name: (lambda unused: True) for name in names}
    sec_checks = {
        row["action_identity"]: (lambda unused: True)
        for row in actions
        if isinstance(row, Mapping) and row.get("sec_pre")
    }
    return MinecraftEACRuntime(
        mode="dual_dag_advisory",
        run_id=run_id,
        env_prechecks=pass_checks,
        sec_prechecks=sec_checks,
        audit_path=None,
    )


def _evidence_events_before(trace: Mapping[str, Any], cutoff_seq: int) -> list[Mapping[str, Any]]:
    return [
        event for event in trace.get("events", [])
        if event.get("event_type") == "k11.eac_evidence_ingested"
        and isinstance(event.get("seq"), int)
        and event["seq"] < cutoff_seq
    ]


def _replay_evidence(runtime: MinecraftEACRuntime, events: list[Mapping[str, Any]]) -> None:
    for event in sorted(events, key=lambda item: item["seq"]):
        payload = event.get("payload", {})
        actor_id = event.get("actor_id")
        if not isinstance(actor_id, str) or not actor_id:
            raise K11AnalysisError("evidence event lacks actor identity")
        proposition_value = payload.get("proposition")
        if not isinstance(proposition_value, Mapping):
            raise K11AnalysisError("evidence event lacks proposition")
        record_type = payload.get("record_type")
        source = payload.get("source")
        root_id = payload.get("root_id")
        revision = payload.get("revision")
        if not all(isinstance(value, str) and value for value in (record_type, source, root_id)):
            raise K11AnalysisError("evidence event identity is incomplete")
        supersedes_value = payload.get("supersedes", [])
        if not isinstance(supersedes_value, list) or any(not isinstance(item, str) for item in supersedes_value):
            raise K11AnalysisError("evidence supersession identity is malformed")
        runtime.ingest_actor_record(
            actor_id=actor_id,
            proposition=_proposition(proposition_value),
            record_type=record_type,
            source=source,
            visible_to=(actor_id,),
            root_id=root_id,
            revision=revision,
            supersedes=tuple(supersedes_value),
        )


def replay_admissibility(
    trace: Mapping[str, Any],
    prepared_event: Mapping[str, Any],
    *,
    cutoff_seq: int,
    replay_label: str,
) -> dict[str, Any]:
    """Re-evaluate one prepared request using only evidence observed before cutoff."""
    payload = prepared_event.get("payload", {})
    request = payload.get("exact_request")
    actor_id = prepared_event.get("actor_id")
    if not isinstance(request, Mapping) or not isinstance(actor_id, str) or not actor_id:
        actor_scope = payload.get("actor_scope")
        actor_id = actor_scope.get("actor_id") if isinstance(actor_scope, Mapping) else actor_id
    if not isinstance(request, Mapping) or not isinstance(actor_id, str) or not actor_id:
        raise K11AnalysisError("prepared event lacks exact request or actor identity")
    action = request.get("action")
    arguments = request.get("arguments")
    if not isinstance(action, Mapping) or not isinstance(arguments, Mapping):
        raise K11AnalysisError("prepared request binding is malformed")
    tool_name = action.get("identity")
    if not isinstance(tool_name, str) or not tool_name:
        raise K11AnalysisError("prepared request action identity is missing")

    runtime = _runtime(f"offline:{trace.get('run_id')}:{replay_label}")
    _replay_evidence(runtime, _evidence_events_before(trace, cutoff_seq))

    def offline_native(**kwargs):
        return {"status": True}

    kwargs = {"player_name": actor_id, **dict(arguments), "emotion": [], "murmur": ""}
    prepared = runtime.prepare_tool(tool_name, offline_native, (), kwargs)
    if prepared.request.action.digest != action.get("digest"):
        raise K11AnalysisError("offline action semantic binding differs from captured request")
    decision = runtime.authority.evaluate(prepared.request.candidate_id)
    candidate = runtime.authority._candidates[prepared.request.candidate_id]
    manifest = candidate.manifest
    if manifest is None:
        raise K11AnalysisError("offline admissibility evaluation produced no dependency manifest")
    return {
        "admissible": decision.admissible,
        "reasons": list(decision.reasons),
        "recoveries": list(decision.recoveries),
        "manifest_fingerprint": manifest.fingerprint,
        "dependency_ids": [item.dependency_id for item in manifest.expectations],
    }


def _changed_dependency_ids(event: Mapping[str, Any]) -> set[str]:
    payload = event.get("payload", {})
    proposition_value = payload.get("proposition")
    actor_id = event.get("actor_id")
    if not isinstance(proposition_value, Mapping) or not isinstance(actor_id, str):
        return set()
    proposition = _proposition(proposition_value)
    changed = {
        _proposition_slot(proposition, actor_id),
        _proposition_slot(proposition, "*"),
    }
    root_id = payload.get("root_id")
    if isinstance(root_id, str) and root_id:
        changed.add("evidence:" + root_id)
    supersedes = payload.get("supersedes", [])
    if isinstance(supersedes, list):
        changed.update("evidence:" + item for item in supersedes if isinstance(item, str) and item)
    return changed


def _candidate_events(trace: Mapping[str, Any], event_type: str) -> dict[str, Mapping[str, Any]]:
    result = {}
    for event in trace.get("events", []):
        if event.get("event_type") != event_type:
            continue
        request = event.get("payload", {}).get("exact_request")
        candidate_id = request.get("candidate_id") if isinstance(request, Mapping) else None
        if isinstance(candidate_id, str):
            result[candidate_id] = event
    return result


def analyze_trace(trace: Mapping[str, Any]) -> dict[str, Any]:
    """Build P0 structural diagnostics and draft D1-D6/N0-N4 classifications."""
    validation = validate_trace(trace)
    p0_validation = validate_p0_trace(trace)
    prepared_by_id = _candidate_events(trace, "k11.eac_action_prepared")
    decision_by_id = _candidate_events(trace, "k11.eac_execution_decision_attempted")
    native_by_id = _candidate_events(trace, "k11.eac_native_effect_entered")
    rows = []

    for candidate_id, prepared in prepared_by_id.items():
        request = prepared.get("payload", {}).get("exact_request", {})
        action = request.get("action", {}) if isinstance(request, Mapping) else {}
        tool_name = action.get("identity") if isinstance(action, Mapping) else None
        if tool_name not in PRIMARY_EFFECT_ACTIONS:
            continue
        row = {
            "candidate_id": candidate_id,
            "tool_name": tool_name,
            "D1": True,
            "D2": False,
            "D3": False,
            "D4": False,
            "D5": False,
            "D6": False,
            "taxonomy": None,
            "qc_state": None,
        }
        decision = decision_by_id.get(candidate_id)
        positive_abandonment = derive_positive_disposition(trace, prepared)
        if decision is not None and positive_abandonment is not None:
            decision_precedes = _event_precedes(decision, positive_abandonment["marker"])
            if decision_precedes is True:
                positive_abandonment = None
            else:
                row["qc_state"] = (
                    "unsupported_path_observed" if decision_precedes is False
                    else "ordering_ambiguous"
                )
                rows.append(row)
                continue
        disposition = decision if decision is not None else (
            positive_abandonment["marker"] if positive_abandonment is not None else None
        )
        if disposition is None:
            row["qc_state"] = "disposition_unresolved"
            rows.append(row)
            continue
        prepare_ns = prepared.get("monotonic_ns")
        disposition_ns = disposition.get("monotonic_ns")
        if (not isinstance(prepare_ns, int) or not isinstance(disposition_ns, int)
                or disposition_ns <= prepare_ns):
            row["qc_state"] = "ordering_ambiguous"
            rows.append(row)
            continue
        row["prepare_to_disposition_ns"] = disposition_ns - prepare_ns
        if decision is not None:
            row["prepare_to_decision_ns"] = disposition_ns - prepare_ns

        try:
            eadm_prepare = replay_admissibility(
                trace, prepared, cutoff_seq=prepared["seq"], replay_label=candidate_id + ":prepare"
            )
            eadm_disposition = replay_admissibility(
                trace, prepared, cutoff_seq=disposition["seq"], replay_label=candidate_id + ":disposition"
            )
        except Exception as exc:
            row["qc_state"] = "offline_replay_failed"
            row["offline_replay_error_type"] = type(exc).__name__
            row["offline_replay_error"] = str(exc)
            rows.append(row)
            continue

        row["EAdm_prepare"] = eadm_prepare["admissible"]
        row["EAdm_disposition"] = eadm_disposition["admissible"]
        if eadm_prepare["admissible"] is not True:
            row["qc_state"] = "prepared_inadmissible_baseline"
            rows.append(row)
            continue
        row["D2"] = True

        actor_id = prepared.get("actor_id")
        interval_mutations = [
            event for event in trace.get("events", [])
            if event.get("event_type") == "k11.eac_evidence_ingested"
            and event.get("actor_id") == actor_id
            and prepared["seq"] < event.get("seq", -1) < disposition["seq"]
        ]
        if not interval_mutations:
            row["taxonomy"] = "N0"
            rows.append(row)
            continue
        row["D3"] = True

        dependency_ids = set(eadm_prepare["dependency_ids"])
        relevant = [
            event for event in interval_mutations
            if _changed_dependency_ids(event).intersection(dependency_ids)
        ]
        if not relevant:
            row["taxonomy"] = "N4"
            rows.append(row)
            continue
        row["D4"] = True
        row["relevant_mutation_event_ids"] = [event.get("event_id") for event in relevant]

        if eadm_disposition["admissible"] is True:
            row["taxonomy"] = "N3"
            rows.append(row)
            continue
        row["D5"] = True

        if positive_abandonment:
            row["taxonomy"] = "N1"
            row["disposition_kind"] = positive_abandonment["kind"]
            row["successor_candidate_ids"] = positive_abandonment["successor_candidate_ids"]
        elif decision is not None and (
            prepared.get("payload", {}).get("exact_request_digest")
            == decision.get("payload", {}).get("exact_request_digest")
        ):
            row["D6"] = True
            row["taxonomy"] = "N2"
            row["native_effect_entered"] = candidate_id in native_by_id
        else:
            row["qc_state"] = "disposition_unresolved"
        rows.append(row)

    denominators = {
        name: sum(row.get(name) is True for row in rows)
        for name in ("D1", "D2", "D3", "D4", "D5", "D6")
    }
    taxonomy = {
        name: sum(row.get("taxonomy") == name for row in rows)
        for name in ("N0", "N1", "N2", "N3", "N4")
    }
    qc = {}
    for row in rows:
        state = row.get("qc_state")
        if state:
            qc[state] = qc.get(state, 0) + 1
    durations = [row["prepare_to_decision_ns"] for row in rows if "prepare_to_decision_ns" in row]
    disposition_durations = [
        row["prepare_to_disposition_ns"] for row in rows if "prepare_to_disposition_ns" in row
    ]
    return {
        "artifact_id": "minecraft-k11-trace-analysis-draft",
        "artifact_version": 1,
        "prevalence_inference_allowed": False,
        "run_id": trace.get("run_id"),
        "trace_validation": validation,
        "p0_trace_validation": p0_validation,
        "denominators": denominators,
        "taxonomy": taxonomy,
        "qc_states": qc,
        "prepare_to_decision_ns": durations,
        "prepare_to_disposition_ns": disposition_durations,
        "actions": rows,
    }


def validate_p0_analysis(analysis: Mapping[str, Any], trace: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Validate that offline P0 replay diagnostics are complete and admissible."""
    errors: list[str] = []
    embedded_trace_validation = analysis.get("p0_trace_validation", {})
    trace_validation = (
        validate_p0_trace(trace) if trace is not None
        else embedded_trace_validation if isinstance(embedded_trace_validation, Mapping) else {}
    )
    if not isinstance(trace_validation, Mapping):
        trace_validation = {}
        errors.append("P0 trace validation result is malformed")
    if analysis.get("analysis_error") is not None:
        errors.append("P0 analysis contains a top-level analysis error")
    if analysis.get("artifact_id") != "minecraft-k11-trace-analysis-draft":
        errors.append("P0 analysis artifact identity is invalid")
    if analysis.get("prevalence_inference_allowed") is not False:
        errors.append("P0 analysis must explicitly forbid prevalence inference")
    if trace_validation.get("valid") is not True:
        errors.append("P0 trace validation did not pass")
    denominators = analysis.get("denominators", {})
    if not isinstance(denominators, Mapping):
        denominators = {}
        errors.append("P0 analysis denominators are malformed")
    if type(denominators.get("D1")) is not int or denominators["D1"] < 1:
        errors.append("P0 analysis requires at least one D1 primary action")
    actions = analysis.get("actions", [])
    if not isinstance(actions, list):
        actions = []
        errors.append("P0 analysis actions are malformed")
    primary = [row for row in actions if isinstance(row, Mapping) and row.get("tool_name") in PRIMARY_EFFECT_ACTIONS]
    if trace is not None:
        trace_candidates = [
            event.get("payload", {}).get("exact_request", {}).get("candidate_id")
            for event in trace.get("events", [])
            if event.get("event_type") == "k11.eac_action_prepared"
            and event.get("payload", {}).get("exact_request", {}).get("action", {}).get("identity")
            in PRIMARY_EFFECT_ACTIONS
        ]
        analysis_candidates = [row.get("candidate_id") for row in primary]
        if (any(not isinstance(value, str) or not value for value in trace_candidates + analysis_candidates)
                or len(set(trace_candidates)) != len(trace_candidates)
                or len(set(analysis_candidates)) != len(analysis_candidates)
                or set(trace_candidates) != set(analysis_candidates)):
            errors.append("P0 analysis does not cover every primary trace candidate exactly once")
    for name in ("D1", "D2", "D3", "D4", "D5", "D6"):
        expected = sum(row.get(name) is True for row in primary)
        observed = denominators.get(name) if isinstance(denominators, Mapping) else None
        if type(observed) is not int or observed != expected:
            errors.append(f"P0 analysis {name} denominator is inconsistent with primary actions")
    if any(row.get("D1") is not True for row in primary):
        errors.append("P0 analysis contains a primary action outside D1")
    replayed = [
        row for row in primary
        if type(row.get("EAdm_prepare")) is bool
        and type(row.get("EAdm_disposition")) is bool
    ]
    if not replayed:
        errors.append("P0 analysis lacks a replayed primary action")
    if len(replayed) != len(primary):
        errors.append("not all expected primary prepare/disposition replays completed")
    forbidden = {"offline_replay_failed", "ordering_ambiguous", "disposition_unresolved"}
    for row in primary:
        if row.get("qc_state") in forbidden:
            errors.append(f"P0 analysis contains instrumentation-related QC state: {row['qc_state']}")
    return {"valid": not errors, "errors": errors, "trace_validation": trace_validation,
            "counts": {"primary_actions": len(primary), "replayed_primary_actions": len(replayed)}}
