"""Versioned benchmark event validation and deterministic normalization."""
from __future__ import annotations
from copy import deepcopy
from typing import Any, Mapping
from benchmarks.common.eac.canonical import canonical_bytes
from .artifacts import validate_publication
from .model import Condition, InjectionPhase, MatrixCell, Scenario, Visibility
from .matrix import matrix_cell_digest, validate_matrix_cell
from .identity import FROZEN_510, semantic_digest
from .equivalence import (baseline_snapshot_digest, pre_gate_snapshot_digest,
                          validate_baseline_snapshot, validate_pre_gate_snapshot)
from .oracle import validate_evaluator_record, validate_evaluator_registry
from .protocol import (EVENT_APPLICABILITY, EVENT_PAYLOAD_REQUIRED, EVENT_REQUIRED_FIELDS,
                       EVENT_TYPES, PROTOCOL_ID, RUN_STATUSES)


EVENT_VERSION = 1
REQUIRED_EVENT_FIELDS = frozenset(EVENT_REQUIRED_FIELDS)
_ACTION_EVENTS = frozenset(EVENT_APPLICABILITY["action_binding_events"])
_EAC_EVENTS = frozenset(EVENT_APPLICABILITY["eac_binding_events"])
_AUTHORITY_EVENTS = frozenset(EVENT_APPLICABILITY["authority_reference_events"])
_EVALUATOR_EVENTS = frozenset(EVENT_APPLICABILITY["evaluator_reference_events"])
_ACTOR_EVENTS = frozenset(EVENT_APPLICABILITY["actor_required_events"])


def _typed_reference(value: Any, field: str) -> None:
    if not isinstance(value, Mapping) or set(value) != {"identity", "version", "digest"}:
        raise ValueError(f"{field} must be a typed identity/version/digest reference")
    if not isinstance(value["identity"], str) or not value["identity"]:
        raise ValueError(f"{field}.identity must be non-empty")
    if isinstance(value["version"], bool) or not isinstance(value["version"], (int, str)):
        raise ValueError(f"{field}.version must be typed")
    digest = value["digest"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError(f"{field}.digest must be lowercase SHA-256")


def _digest(value: Any, field: str) -> None:
    if (not isinstance(value, str) or len(value) != 64 or
            any(character not in "0123456789abcdef" for character in value)):
        raise ValueError(f"{field} must be a lowercase SHA-256")


def _validate_applicability(result: Mapping[str, Any]) -> None:
    kind = result["event_type"]
    if kind in _ACTOR_EVENTS and (not isinstance(result["actor_id"], str) or not result["actor_id"]):
        raise ValueError(f"{kind} requires actor_id")
    if (kind == "perturbation_injected" and result["visibility"] == Visibility.ACTOR_VISIBLE.value
            and (not isinstance(result["actor_id"], str) or not result["actor_id"])):
        raise ValueError("actor-visible perturbation injection requires actor_id")
    if (kind == "perturbation_injected" and result["visibility"] != Visibility.ACTOR_VISIBLE.value
            and result["actor_id"] is not None):
        raise ValueError("non-actor-visible perturbation injection cannot name an actor")
    if (kind not in _ACTOR_EVENTS and kind != "perturbation_injected" and
            result["actor_id"] is not None):
        raise ValueError(f"actor_id is not applicable to {kind}")
    if kind in _ACTION_EVENTS:
        for field in ("candidate_identity", "request_identity", "action_identity", "action_version",
                      "action_digest", "opportunity_id"):
            if result[field] is None:
                raise ValueError(f"{kind} requires {field}")
    requires_eac_binding = kind in _EAC_EVENTS or (kind in _ACTION_EVENTS and
                                                   result["condition"] != "baseline")
    if requires_eac_binding:
        for field in ("epre_identity", "epre_version", "support_policy", "source_profile",
                      "dependency_manifest_fingerprint"):
            if result[field] is None:
                raise ValueError(f"{kind} requires {field}")
    elif kind in _ACTION_EVENTS and any(result[field] is not None for field in (
            "epre_identity", "epre_version", "support_policy", "source_profile",
            "dependency_manifest_fingerprint")):
        raise ValueError("baseline action events must not synthesize EAC bindings")
    if kind not in _ACTION_EVENTS and any(result[field] is not None for field in (
            "candidate_identity", "request_identity", "action_identity", "action_version",
            "action_digest", "epre_identity", "epre_version", "support_policy", "source_profile",
            "dependency_manifest_fingerprint")):
        raise ValueError(f"action/EAC bindings are not applicable to {kind}")
    if kind in {"oracle_state_changed", "actor_visible_evidence_exposed", "recovery_action"}:
        if not isinstance(result["opportunity_id"], str) or not result["opportunity_id"]:
            raise ValueError(f"{kind} requires opportunity_id")
    elif kind not in _ACTION_EVENTS and result["opportunity_id"] is not None:
        raise ValueError(f"opportunity_id is not applicable to {kind}")
    if kind == "eadm_evaluated" and result["condition"] == "baseline":
        raise ValueError("baseline does not emit synthetic EAdm events")
    if result["condition"] == "baseline" and kind in _EAC_EVENTS:
        raise ValueError("Baseline does not emit EAC opportunity/permit events")
    if kind.startswith("permit_") and result["condition"] != "authority":
        raise ValueError("execution-permit events are Authority-only")
    bypass_attempt = (kind == "effect_attempted" and result["condition"] == "authority" and
                      result["payload"].get("attempt_class") == "BYPASS")
    # Effect outcomes inherit bypass/non-bypass applicability from the preceding
    # attempt and are checked with that lifecycle below.
    deferred_effect_outcome = kind in {"effect_allowed", "effect_rejected"}
    requires_authority = (kind in _AUTHORITY_EVENTS and not bypass_attempt and
                          not deferred_effect_outcome and
                          (kind in {"eadm_evaluated", "permit_issued", "permit_staled", "permit_rejected"}
                           or result["condition"] != "baseline"))
    if requires_authority and result["authority_reference"] is None:
        raise ValueError(f"{kind} requires authority_reference")
    if kind in _EVALUATOR_EVENTS and result["evaluator_reference"] is None:
        raise ValueError(f"{kind} requires evaluator_reference")
    if kind not in _EVALUATOR_EVENTS and result["evaluator_reference"] is not None:
        raise ValueError(f"evaluator reference is not applicable to {kind}")
    if kind not in _AUTHORITY_EVENTS and result["authority_reference"] is not None:
        raise ValueError(f"authority reference is not applicable to {kind}")
    if result["condition"] == "baseline" and result["authority_reference"] is not None:
        raise ValueError("baseline events cannot reference Authority")
    if result["visibility"] == Visibility.ACTOR_VISIBLE.value and result["evaluator_reference"] is not None:
        raise ValueError("evaluator reference is forbidden in actor-visible events")
    if kind in _EVALUATOR_EVENTS and result["visibility"] == Visibility.ACTOR_VISIBLE.value:
        raise ValueError("evaluator events cannot be actor-visible")


def _validate_payload(kind: str, payload: Mapping[str, Any]) -> None:
    required = set(EVENT_PAYLOAD_REQUIRED[kind])
    missing = required - set(payload)
    if missing:
        raise ValueError(f"{kind} payload missing fields: {sorted(missing)}")
    if kind == "eadm_evaluated":
        if type(payload["admissible"]) is not bool:
            raise ValueError("eadm_evaluated.admissible must be boolean")
        if not isinstance(payload["witness_ids"], list) or not isinstance(payload["reason_codes"], list):
            raise ValueError("eadm_evaluated witness/reason fields must be arrays")
        if (type(payload["witness_grounded"]) is not bool or
                type(payload["actor_scope_leakage_detected"]) is not bool):
            raise ValueError("eadm_evaluated diagnostics must be boolean")
    if kind == "envpre_checked" and type(payload["result"]) is not bool:
        raise ValueError("envpre_checked.result must be boolean")
    if kind == "effect_attempted" and payload["attempt_class"] not in {
            "NORMAL", "STALE", "REPLAY", "BYPASS"}:
        raise ValueError("invalid effect attempt class")
    if kind == "actor_visible_evidence_exposed" and payload["evidence_change"] not in {
            "EXPOSED", "SUPERSEDED", "INVALIDATED"}:
        raise ValueError("invalid actor-visible evidence change")
    for field in ("operator_identity", "injection_event_identity", "visibility_effect",
                  "oracle_commitment_id", "mutation_identity", "evidence_root_id",
                  "root_type", "actor_scope", "opportunity_id", "reason",
                  "envpre_identity", "attempt_id", "outcome"):
        if field in payload and (not isinstance(payload[field], str) or not payload[field]):
            raise ValueError(f"{kind}.{field} must be a non-empty string")
    for field in ("permit_id", "permit_validation_reference"):
        if field in payload and payload[field] is not None and (
                not isinstance(payload[field], str) or not payload[field]):
            raise ValueError(f"{kind}.{field} must be a non-empty string or null")
    if kind in {"permit_issued", "permit_staled"} and (
            not isinstance(payload.get("permit_id"), str) or not payload["permit_id"]):
        raise ValueError(f"{kind}.permit_id must be a non-empty string")
    if kind == "permit_rejected":
        if payload["rejection_stage"] not in {"issuance", "validation"}:
            raise ValueError("permit_rejected.rejection_stage is invalid")
        if ((payload["rejection_stage"] == "issuance" and payload["permit_id"] is not None) or
                (payload["rejection_stage"] == "validation" and
                 (not isinstance(payload["permit_id"], str) or not payload["permit_id"]))):
            raise ValueError("permit rejection stage and permit ID are inconsistent")
    for field in ("dependency_manifest_fingerprint", "permit_validation_reference"):
        if field in payload and payload[field] is not None:
            _digest(payload[field], f"{kind}.{field}")
    if "oracle_record_digest" in payload:
        _digest(payload["oracle_record_digest"], f"{kind}.oracle_record_digest")
    if "witness_ids" in payload and any(not isinstance(item, str) or not item
                                         for item in payload["witness_ids"]):
        raise ValueError("witness IDs must be non-empty strings")
    if "reason_codes" in payload and any(not isinstance(item, str) or not item
                                          for item in payload["reason_codes"]):
        raise ValueError("reason codes must be non-empty strings")
    if kind == "recovery_action" and payload["recovery_class"] not in {
            "OBSERVE", "CLARIFY", "COMMUNICATE", "WAIT", "ALTERNATE_ACTION",
            "REPLAN", "RESOLVE_CONFLICT", "ABANDON", "NO_RECOVERY", "UNKNOWN"}:
        raise ValueError("invalid recovery class")
    if kind == "recovery_action" and payload["recovery_class"] in {"NO_RECOVERY", "UNKNOWN"}:
        raise ValueError("recovery_action must identify an actual recovery attempt")
    if kind == "run_terminal" and payload["run_status"] not in RUN_STATUSES:
        raise ValueError("invalid terminal run status")
    if kind == "run_terminal":
        if type(payload["task_success"]) is not bool:
            raise ValueError("run_terminal.task_success must be boolean")
        for field in ("task_goals", "completed_task_goals", "llm_calls", "tokens",
                      "wall_clock_ms", "eac_overhead_us", "permit_overhead_us"):
            if type(payload[field]) is not int or payload[field] < 0:
                raise ValueError(f"run_terminal.{field} must be a non-negative integer")
        if payload["completed_task_goals"] > payload["task_goals"]:
            raise ValueError("completed goals cannot exceed task goals")


def _validate_reference_record(record: Mapping[str, Any], reference_type: str) -> None:
    context = {"reference_type", "artifact_identity", "run_id", "scenario_id",
               "scenario_digest", "condition", "seed", "matrix_cell_digest",
               "runtime_premanifest_identity", "event_sequence"}
    required = (context | {"candidate_id", "attempt_id", "permit_id", "decision"}
                if reference_type == "authority" else context)
    if set(record) != required or record.get("reference_type") != reference_type:
        raise ValueError(f"{reference_type} reference record fields are invalid")
    if (not isinstance(record["artifact_identity"], str) or not record["artifact_identity"] or
            type(record["event_sequence"]) is not int or record["event_sequence"] < 0):
        raise ValueError(f"{reference_type} reference record identity is invalid")
    if reference_type == "authority":
        for field in ("candidate_id", "attempt_id", "decision"):
            if not isinstance(record[field], str) or not record[field]:
                raise ValueError(f"authority reference {field} is invalid")
        if record["permit_id"] is not None and (not isinstance(record["permit_id"], str) or
                                                not record["permit_id"]):
            raise ValueError("authority reference permit_id is invalid")
    elif not isinstance(record["scenario_id"], str) or not record["scenario_id"]:
        raise ValueError("evaluator reference scenario_id is invalid")


def normalize_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an event and return a sorted, detached JSON-compatible copy."""
    if not isinstance(event, Mapping):
        raise TypeError("benchmark event must be an object")
    missing = REQUIRED_EVENT_FIELDS - set(event)
    if missing:
        raise ValueError(f"benchmark event missing fields: {sorted(missing)}")
    extra = set(event) - REQUIRED_EVENT_FIELDS
    if extra:
        raise ValueError(f"benchmark event has unknown fields: {sorted(extra)}")
    if (event.get("schema_version") != EVENT_VERSION
            or not isinstance(event.get("event_id"), str)
            or not event["event_id"]):
        raise ValueError("unsupported or unidentified benchmark event")
    if event.get("event_type") not in EVENT_TYPES:
        raise ValueError("invalid benchmark event type")
    for field in ("run_id", "scenario_id"):
        if not isinstance(event.get(field), str) or not event[field]:
            raise ValueError(f"{field} must be a non-empty string")
    if type(event.get("monotonic_index")) is not int or event["monotonic_index"] < 0:
        raise ValueError("monotonic_index must be a non-negative integer")
    if not isinstance(event.get("payload"), Mapping):
        raise ValueError("event payload must be an object")
    _validate_payload(str(event.get("event_type")), event["payload"])
    if event.get("emission_status") not in {"RECORDED", "SANITIZED"}:
        raise ValueError("invalid emission status")
    if event.get("protocol_identity") != PROTOCOL_ID or event.get("protocol_version") != 1:
        raise ValueError("invalid protocol identity/version")
    if event.get("condition") not in {"baseline", "advisory", "authority"}:
        raise ValueError("invalid event condition")
    if type(event.get("seed")) is not int or event["seed"] < 0:
        raise ValueError("seed must be a non-negative integer")
    for field in ("logical_step", "sequence"):
        if type(event.get(field)) is not int or event[field] < 0:
            raise ValueError(f"{field} must be a non-negative integer")
    if event["sequence"] != event["monotonic_index"]:
        raise ValueError("sequence must equal monotonic_index")
    for field in ("scenario_digest", "matrix_cell_digest", "pre_gate_snapshot_digest",
                  "runtime_premanifest_identity", "evaluator_registry_digest"):
        _digest(event[field], field)
    for field in ("action_digest", "dependency_manifest_fingerprint"):
        if event[field] is not None:
            _digest(event[field], field)
    for field in ("candidate_identity", "request_identity", "action_identity", "epre_identity",
                  "opportunity_id"):
        if event[field] is not None:
            if field == "request_identity" and isinstance(event[field], Mapping):
                required = {"candidate_id", "attempt_id", "arguments", "target"}
                if set(event[field]) != required or any(
                        not isinstance(event[field][key], str) or not event[field][key]
                        for key in ("candidate_id", "attempt_id")):
                    raise ValueError("request_identity must include candidate, attempt, arguments, and target")
                canonical_bytes(event[field]["arguments"]); canonical_bytes(event[field]["target"])
                if event["candidate_identity"] != event[field]["candidate_id"]:
                    raise ValueError("candidate identity must equal ExactRequest candidate_id")
            elif not isinstance(event[field], str) or not event[field]:
                raise ValueError(f"{field} must be a non-empty string or null")
    for field in ("action_version", "epre_version"):
        if event[field] is not None and (isinstance(event[field], bool) or not isinstance(event[field], (int, str))):
            raise ValueError(f"{field} must be typed or null")
    for field in ("support_policy", "source_profile"):
        if event[field] is not None:
            _typed_reference(event[field], field)
    for field in ("authority_reference", "evaluator_reference"):
        if event[field] is not None:
            _digest(event[field], field)
    result = deepcopy(dict(event))
    if "phase" in result:
        try: InjectionPhase(result["phase"])
        except (TypeError, ValueError) as exc: raise ValueError("invalid injection phase") from exc
    if "visibility" in result:
        try: Visibility(result["visibility"])
        except (TypeError, ValueError) as exc: raise ValueError("invalid event visibility") from exc
    _validate_applicability(result)
    canonical_bytes(result)
    if result["visibility"] in {Visibility.ACTOR_VISIBLE.value,
                                Visibility.PUBLIC_SANITIZED.value} or result["emission_status"] == "SANITIZED":
        validate_publication(result["payload"])
    return {key: result[key] for key in sorted(result)}


def validate_event_stream(events: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
                          *, cell: MatrixCell, scenario: Scenario,
                          pre_gate_snapshots: Mapping[str, Mapping[str, Any]],
                          reference_records: Mapping[str, Mapping[str, Any]],
                          evaluator_registry: Mapping[str, Any],
                          approved_evaluator_registry_digest: str) -> tuple[dict[str, Any], ...]:
    normalized = tuple(normalize_event(event) for event in events)
    registry = validate_evaluator_registry(
        evaluator_registry,
        approved_manifest_digest=approved_evaluator_registry_digest)
    validate_matrix_cell(cell, scenario)
    if not pre_gate_snapshots:
        raise ValueError("event stream requires authenticated pre-gate snapshots")
    opportunity_ids: set[str] = set()
    primary_count = 0
    for digest, snapshot in pre_gate_snapshots.items():
        if cell.condition is Condition.BASELINE:
            validate_baseline_snapshot(cell, scenario, snapshot)
            observed_digest = baseline_snapshot_digest(snapshot)
        else:
            validate_pre_gate_snapshot(cell, scenario, snapshot)
            observed_digest = pre_gate_snapshot_digest(snapshot)
        if observed_digest != digest:
            raise ValueError("pre-gate snapshot registry digest mismatch")
        if snapshot["opportunity_id"] in opportunity_ids:
            raise ValueError("pre-gate snapshot opportunity IDs must be unique")
        opportunity_ids.add(snapshot["opportunity_id"])
        primary_count += snapshot["opportunity_role"] == "primary"
    if primary_count != 1:
        raise ValueError("snapshot registry requires exactly one primary opportunity")
    indexes = tuple(event["monotonic_index"] for event in normalized)
    if indexes != tuple(range(len(normalized))):
        raise ValueError("event monotonic indexes must be contiguous from zero")
    logical_steps = tuple(event["logical_step"] for event in normalized)
    if logical_steps != tuple(sorted(logical_steps)):
        raise ValueError("event logical steps must be nondecreasing")
    if len({event["event_id"] for event in normalized}) != len(normalized):
        raise ValueError("event identifiers must be unique")
    if normalized and (len({event["run_id"] for event in normalized}) != 1 or
                       len({event["scenario_id"] for event in normalized}) != 1):
        raise ValueError("an event stream must belong to one run and scenario")
    if normalized and (len({event["condition"] for event in normalized}) != 1 or
                       len({event["seed"] for event in normalized}) != 1 or
                       len({(event["protocol_identity"], event["protocol_version"])
                            for event in normalized}) != 1):
        raise ValueError("event stream protocol, condition, and seed must remain fixed")
    terminal = [event for event in normalized if event["event_type"] == "run_terminal"]
    if len(terminal) != 1 or normalized[-1]["event_type"] != "run_terminal":
        raise ValueError("complete event stream must terminate explicitly")
    expected_cell_digest = matrix_cell_digest(cell)
    for event in normalized:
        snapshot = pre_gate_snapshots.get(event["pre_gate_snapshot_digest"])
        if snapshot is None:
            raise ValueError("event pre-gate snapshot is not in the authenticated registry")
        snapshot_request = snapshot["request"]
        snapshot_manifest = snapshot.get("dependency_manifest")
        if event["opportunity_id"] is not None and event["opportunity_id"] != snapshot["opportunity_id"]:
            raise ValueError("opportunity-bearing event differs from its authenticated snapshot")
        if (event["run_id"] != cell.run_id or event["scenario_id"] != scenario.scenario_id or
                event["scenario_digest"] != scenario.digest or
                event["scenario_digest"] != cell.scenario_digest or
                event["matrix_cell_digest"] != expected_cell_digest or
                event["condition"] != cell.condition.value or event["seed"] != cell.seed or
                event["phase"] != scenario.document["injection_phase"] or
                event["runtime_premanifest_identity"] != FROZEN_510.premanifest_identity or
                event["evaluator_registry_digest"] != approved_evaluator_registry_digest):
            raise ValueError("event does not bind the planned matrix cell and frozen scenario")
        if event["support_policy"] is not None and event["support_policy"] != scenario.document["support_policy"]:
            raise ValueError("event SupportPolicy differs from the frozen scenario")
        if event["source_profile"] is not None and event["source_profile"] != scenario.document["source_profile"]:
            raise ValueError("event SourceProfile differs from the frozen scenario")
        if event["event_type"] in _ACTION_EVENTS:
            request = event["request_identity"]
            if (event["opportunity_id"] != snapshot["opportunity_id"] or
                    event["candidate_identity"] != snapshot["candidate"] or
                    request["candidate_id"] != snapshot_request["candidate_id"] or
                    request["attempt_id"] != snapshot_request["attempt_id"] or
                    request["arguments"] != snapshot_request["arguments"] or
                    request["target"] != snapshot_request["target"] or
                    event["action_identity"] != snapshot_request["action"]["identity"] or
                    event["action_version"] != snapshot_request["action"]["version"] or
                    event["action_digest"] != snapshot_request["action"]["digest"]):
                raise ValueError("action event differs from the authenticated pre-gate ExactRequest")
            if event["condition"] != "baseline" and (
                    event["epre_identity"] != snapshot["epre"]["identity"] or
                    event["epre_version"] != snapshot["epre"]["version"] or
                    event["dependency_manifest_fingerprint"] != snapshot_manifest["fingerprint"]):
                raise ValueError("EAC event differs from the authenticated pre-gate bindings")
            if "dependency_manifest_fingerprint" in event["payload"] and \
                    event["payload"]["dependency_manifest_fingerprint"] != event["dependency_manifest_fingerprint"]:
                raise ValueError("payload dependency manifest contradicts the authenticated snapshot")
            if event["event_type"] == "epre_opportunity" and \
                    event["payload"]["opportunity_id"] != event["opportunity_id"]:
                raise ValueError("EPre opportunity payload does not match its snapshot")
        for field, reference_type in (("authority_reference", "authority"),
                                      ("evaluator_reference", "evaluator")):
            reference = event[field]
            if reference is not None:
                record = reference_records.get(reference)
                if not isinstance(record, Mapping):
                    raise ValueError(f"{field} is not a resolvable content-addressed record")
                if reference_type == "evaluator":
                    validate_evaluator_record(record, cell=cell, scenario=scenario,
                                              opportunity_id=event["opportunity_id"],
                                               logical_step=event["logical_step"],
                                               materialized_fixture_digest=snapshot[
                                                   "materialized_fixture_digest"],
                                               evaluator_registry=registry,
                                               approved_registry_digest=approved_evaluator_registry_digest)
                    if (record["record_digest"] != reference or
                            event["payload"]["oracle_record_digest"] != reference or
                            event["payload"]["oracle_commitment_id"] != record["commitment_id"]):
                        raise ValueError("oracle event does not bind its evaluator record")
                    continue
                if semantic_digest(record) != reference or record.get("reference_type") != reference_type:
                    raise ValueError(f"{field} is not a resolvable content-addressed record")
                _validate_reference_record(record, reference_type)
                if (record["run_id"] != cell.run_id or
                        record["scenario_id"] != scenario.scenario_id or
                        record["scenario_digest"] != scenario.digest or
                        record["condition"] != cell.condition.value or record["seed"] != cell.seed or
                        record["matrix_cell_digest"] != expected_cell_digest or
                        record["runtime_premanifest_identity"] != FROZEN_510.premanifest_identity or
                        record["event_sequence"] != event["sequence"]):
                    raise ValueError(f"{field} context differs from the event MatrixCell")
                if reference_type == "authority" and event["request_identity"] is not None:
                    if (record.get("candidate_id") != event["candidate_identity"] or
                            record.get("attempt_id") != event["request_identity"]["attempt_id"]):
                        raise ValueError("authority reference does not match the event request")
                    if "permit_id" in event["payload"] and event["payload"]["permit_id"] is not None and \
                            record.get("permit_id") != event["payload"]["permit_id"]:
                        raise ValueError("authority reference does not match the event permit")
                    if event["event_type"] == "eadm_evaluated":
                        expected_decision = ("admissible" if event["payload"]["admissible"]
                                             else "not_admissible")
                    elif event["event_type"] == "envpre_checked":
                        expected_decision = ("passed" if event["payload"]["result"] else "rejected")
                    else:
                        expected_decision = {
                            "permit_issued": "issued", "permit_staled": "stale",
                            "permit_rejected": "rejected", "effect_allowed": "allowed",
                            "effect_rejected": "rejected",
                        }.get(event["event_type"])
                    if expected_decision is not None and record.get("decision") != expected_decision:
                        raise ValueError("authority reference decision contradicts the event")
    issued_permits: dict[str, tuple[str, str, str, str, str, str]] = {}
    attempted_effects: dict[tuple[str, str], tuple[str, str, Any, str, str, str, str]] = {}
    opportunities: dict[str, tuple[str, str, str, str, bool | None]] = {}
    referenced_opportunities: set[str] = set()
    primary_opportunity = next(snapshot["opportunity_id"] for snapshot in pre_gate_snapshots.values()
                               if snapshot["opportunity_role"] == "primary")
    for event in normalized:
        payload = event["payload"]
        if event["event_type"] in _ACTION_EVENTS:
            referenced_opportunities.add(event["opportunity_id"])
        if event["event_type"] == "epre_opportunity":
            if event["opportunity_id"] in opportunities:
                raise ValueError("EPre opportunity identifiers cannot be repeated")
            opportunities[event["opportunity_id"]] = (
                event["candidate_identity"], event["request_identity"]["attempt_id"],
                event["pre_gate_snapshot_digest"], event["dependency_manifest_fingerprint"], None)
        elif event["event_type"] == "eadm_evaluated":
            binding = opportunities.get(event["opportunity_id"])
            expected = (event["candidate_identity"], event["request_identity"]["attempt_id"],
                        event["pre_gate_snapshot_digest"], event["dependency_manifest_fingerprint"])
            if binding is None or binding[:4] != expected or binding[4] is not None:
                raise ValueError("EAdm must follow its unique EPre opportunity")
            opportunities[event["opportunity_id"]] = (*binding[:4], payload["admissible"])
        elif event["event_type"] == "permit_issued":
            opportunity = opportunities.get(event["opportunity_id"])
            if opportunity is None or opportunity[4] is not True:
                raise ValueError("permit issuance requires an earlier admissible EAdm")
            if payload["permit_id"] in issued_permits:
                raise ValueError("permit identifiers cannot be issued twice")
            issued_permits[payload["permit_id"]] = (
                event["candidate_identity"], event["request_identity"]["attempt_id"],
                event["opportunity_id"], event["pre_gate_snapshot_digest"],
                event["dependency_manifest_fingerprint"], "issued")
        elif event["event_type"] == "permit_rejected" and payload["rejection_stage"] == "issuance":
            opportunity = opportunities.get(event["opportunity_id"])
            if opportunity is None or opportunity[4] is not False:
                raise ValueError("permit issuance rejection requires an earlier non-admissible EAdm")
        elif event["event_type"] in {"permit_staled", "permit_rejected"}:
            binding = issued_permits.get(payload["permit_id"])
            if binding != (event["candidate_identity"], event["request_identity"]["attempt_id"],
                           event["opportunity_id"], event["pre_gate_snapshot_digest"],
                           event["dependency_manifest_fingerprint"], "issued"):
                raise ValueError("permit transition must match an earlier issued permit binding")
            issued_permits[payload["permit_id"]] = (
                *binding[:5], "stale" if event["event_type"] == "permit_staled" else "rejected")
        elif event["event_type"] == "effect_attempted":
            attempt_key = (event["candidate_identity"], payload["attempt_id"])
            if attempt_key in attempted_effects:
                raise ValueError("effect attempt already awaits a result")
            if payload["attempt_id"] != event["request_identity"]["attempt_id"]:
                raise ValueError("effect attempt must bind the ExactRequest attempt")
            permit_id = payload["permit_id"]
            if event["condition"] == "authority":
                attempt_class = payload["attempt_class"]
                permit_binding = issued_permits.get(permit_id)
                validation_reference = payload["permit_validation_reference"]
                derived_class = ("BYPASS" if permit_id is None else
                                 "REPLAY" if permit_binding is not None and permit_binding[5] == "consumed" else
                                 "STALE" if permit_binding is not None and permit_binding[5] in {"stale", "rejected"} else
                                 "NORMAL")
                if attempt_class != derived_class:
                    raise ValueError("effect attempt class contradicts permit lifecycle")
                if derived_class == "BYPASS":
                    if (permit_id is not None or validation_reference is not None or
                            event["authority_reference"] is not None):
                        raise ValueError("bypass attempt must not claim Authority or permit validation")
                else:
                    if (permit_binding is None or permit_binding[:5] !=
                            (event["candidate_identity"], payload["attempt_id"],
                             event["opportunity_id"], event["pre_gate_snapshot_digest"],
                             event["dependency_manifest_fingerprint"])):
                        raise ValueError("Authority effect attempt must bind an earlier permit")
                    record = reference_records.get(validation_reference)
                    if (not isinstance(record, Mapping) or semantic_digest(record) != validation_reference or
                            record.get("reference_type") != "authority" or
                            record.get("decision") not in {"allowed", "rejected"} or
                            record.get("candidate_id") != event["candidate_identity"] or
                            record.get("attempt_id") != payload["attempt_id"] or
                            record.get("permit_id") != permit_id):
                        raise ValueError("permit validation reference is not resolvable")
                    _validate_reference_record(record, "authority")
                    if (record["run_id"] != cell.run_id or record["scenario_id"] != scenario.scenario_id or
                            record["scenario_digest"] != scenario.digest or
                            record["condition"] != cell.condition.value or record["seed"] != cell.seed or
                            record["matrix_cell_digest"] != expected_cell_digest or
                            record["runtime_premanifest_identity"] != FROZEN_510.premanifest_identity or
                            record["event_sequence"] != event["sequence"]):
                        raise ValueError("permit validation reference context differs from the event")
                    if event["authority_reference"] != validation_reference:
                        raise ValueError("effect attempt authority and permit-validation references must agree")
                    expected_decision = "allowed" if derived_class == "NORMAL" else "rejected"
                    if record.get("decision") != expected_decision:
                        raise ValueError("permit validation decision contradicts attempt class")
            elif permit_id is not None or payload["permit_validation_reference"] is not None:
                raise ValueError("non-Authority effect attempts cannot claim permit validation")
            elif payload["attempt_class"] != "NORMAL":
                raise ValueError("Baseline/Advisory effect attempts must be NORMAL")
            attempted_effects[attempt_key] = (
                event["candidate_identity"], event["request_identity"]["attempt_id"], permit_id,
                payload["attempt_class"],
                event["opportunity_id"], event["pre_gate_snapshot_digest"],
                event["dependency_manifest_fingerprint"])
        elif event["event_type"] in {"effect_allowed", "effect_rejected"}:
            attempt_id = payload["attempt_id"]
            attempt_key = (event["candidate_identity"], attempt_id)
            expected = attempted_effects.get(attempt_key)
            observed = (event["candidate_identity"], event["request_identity"]["attempt_id"],
                        payload["permit_id"], event["opportunity_id"],
                        event["pre_gate_snapshot_digest"], event["dependency_manifest_fingerprint"])
            if expected is None or (expected[:3] + expected[4:]) != observed:
                raise ValueError("effect result must uniquely match an earlier effect attempt")
            permit_id = payload["permit_id"]
            if expected[3] == "BYPASS":
                if event["authority_reference"] is not None or permit_id is not None:
                    raise ValueError("bypass effect outcome must not fabricate Authority validation")
            elif event["condition"] == "authority" and event["authority_reference"] is None:
                raise ValueError("non-bypass Authority effect outcome requires authority_reference")
            attempted_effects.pop(attempt_key)
            if permit_id is not None and permit_id in issued_permits and expected[3] == "NORMAL":
                binding = issued_permits[permit_id]
                issued_permits[permit_id] = (*binding[:5], "consumed")
    if cell.condition is Condition.BASELINE:
        if primary_opportunity not in referenced_opportunities:
            raise ValueError("Baseline stream must reference its primary control opportunity")
    else:
        if primary_opportunity not in opportunities:
            raise ValueError("complete stream must reference the primary EPre opportunity")
        if opportunities[primary_opportunity][4] is None:
            raise ValueError("EAC conditions must evaluate the primary EPre opportunity")
    if attempted_effects:
        raise ValueError("complete stream contains unresolved effect attempts")
    return normalized
