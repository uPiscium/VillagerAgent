"""Fail-closed evaluator records and sanitized publication boundary."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .artifacts import publication_key_allowed, validate_publication
from .identity import detached_digest, semantic_digest
from .model import MatrixCell, Scenario
from .protocol import PROTOCOL_ID, load_committed_protocol

EVALUATOR_RECORD_VERSION = "eac-evaluator-record/1"
EVALUATOR_REGISTRY_VERSION = "eac-evaluator-label-registry/1"
EVALUATOR_RECORD_FIELDS = frozenset({
    "schema_version", "record_digest", "protocol_identity", "protocol_version",
    "run_id", "scenario_id", "scenario_digest", "condition", "seed",
    "opportunity_id", "logical_step", "commitment_id", "label_rule_identity",
    "justification_adequate", "proposition_true", "blocking_conflict_expected",
    "supersession_expected", "actor_scope_leakage_expected",
    "scope_isolation_applicable",
    "invalidation_expectation", "recovery_required",
    "recorded_before_subject_outcome", "source_fixture_digest",
})
EVALUATOR_REGISTRY_FIELDS = frozenset({
    "schema_version", "manifest_digest", "protocol_identity", "protocol_version",
    "approval_status", "entries",
})
EVALUATOR_REGISTRY_ENTRY_FIELDS = frozenset({
    "record_digest", "run_id", "scenario_id", "scenario_digest", "condition", "seed",
    "opportunity_id", "logical_step", "commitment_id", "label_rule_identity",
    "source_fixture_digest",
})


@dataclass(frozen=True, slots=True)
class EvaluatorOracle:
    """A preregistered evaluator label; missing labels never use subject output."""
    state: Mapping[str, Any]

    def evaluate(self, observation: Mapping[str, Any]) -> bool:
        del observation
        expected = self.state.get("expected")
        if type(expected) is not bool:
            raise ValueError("independent oracle expected label is missing or invalid")
        return expected


def label_rule_identity(scenario: Scenario) -> str:
    oracle = scenario.document["independent_adequacy_oracle"]
    return semantic_digest({"commitment_id": oracle["commitment_id"],
                            "label_rule": oracle["label_rule"]})


def evaluator_record_digest(record: Mapping[str, Any]) -> str:
    return detached_digest(record, "record_digest")


def evaluator_registry_digest(manifest: Mapping[str, Any]) -> str:
    return detached_digest(manifest, "manifest_digest")


def evaluator_registry_entry(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project a content-addressed evaluator record into its pre-launch commitment."""
    return {field: record[field] for field in EVALUATOR_REGISTRY_ENTRY_FIELDS}


def evaluator_registry_manifest(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build the canonical manifest that must be externally approved before launch."""
    entries = sorted((evaluator_registry_entry(record) for record in records),
                     key=lambda item: (item["run_id"], item["opportunity_id"],
                                       item["logical_step"], item["record_digest"]))
    manifest: dict[str, Any] = {
        "schema_version": EVALUATOR_REGISTRY_VERSION,
        "manifest_digest": "0" * 64,
        "protocol_identity": PROTOCOL_ID,
        "protocol_version": 1,
        "approval_status": "PRELAUNCH_APPROVED",
        "entries": entries,
    }
    manifest["manifest_digest"] = evaluator_registry_digest(manifest)
    return manifest


def validate_evaluator_registry(manifest: Mapping[str, Any], *,
                                approved_manifest_digest: str) -> dict[str, Any]:
    """Validate the externally approved pre-launch label commitment."""
    if not isinstance(manifest, Mapping) or set(manifest) != EVALUATOR_REGISTRY_FIELDS:
        raise ValueError("evaluator registry fields do not match the frozen schema")
    observed = deepcopy(dict(manifest))
    if (observed["schema_version"] != EVALUATOR_REGISTRY_VERSION or
            observed["protocol_identity"] != PROTOCOL_ID or
            observed["protocol_version"] != 1 or
            observed["approval_status"] != "PRELAUNCH_APPROVED"):
        raise ValueError("evaluator registry identity or pre-launch approval is invalid")
    digest = evaluator_registry_digest(observed)
    if observed["manifest_digest"] != digest or approved_manifest_digest != digest:
        raise ValueError("evaluator registry differs from the pre-launch approved digest")
    preregistered_digest = load_committed_protocol().get("preregistration", {}).get(
        "evaluator_label_registry_digest")
    if preregistered_digest == "REQUIRES_PREREGISTRATION_APPROVAL":
        raise ValueError("evaluator registry has not received preregistration approval")
    if preregistered_digest != approved_manifest_digest:
        raise ValueError("evaluator registry digest is not the committed preregistration identity")
    entries = observed["entries"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("evaluator registry must commit at least one label")
    keys: set[tuple[Any, ...]] = set()
    record_digests: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != EVALUATOR_REGISTRY_ENTRY_FIELDS:
            raise ValueError("evaluator registry entry fields are invalid")
        for field in ("record_digest", "scenario_digest", "label_rule_identity",
                      "source_fixture_digest"):
            value = entry[field]
            if (not isinstance(value, str) or len(value) != 64 or
                    any(character not in "0123456789abcdef" for character in value)):
                raise ValueError(f"evaluator registry {field} must be lowercase SHA-256")
        if type(entry["seed"]) is not int or type(entry["logical_step"]) is not int:
            raise ValueError("evaluator registry seed/logical step must be integers")
        key = (entry["run_id"], entry["scenario_id"], entry["condition"], entry["seed"],
               entry["opportunity_id"])
        if key in keys or entry["record_digest"] in record_digests:
            raise ValueError("evaluator registry entries must be unique per opportunity")
        keys.add(key)
        record_digests.add(entry["record_digest"])
    return observed


def validate_evaluator_record(record: Mapping[str, Any], *, cell: MatrixCell,
                              scenario: Scenario, opportunity_id: str,
                              logical_step: int,
                              materialized_fixture_digest: str,
                              evaluator_registry: Mapping[str, Any],
                              approved_registry_digest: str) -> dict[str, Any]:
    if not isinstance(record, Mapping) or set(record) != EVALUATOR_RECORD_FIELDS:
        raise ValueError("evaluator record fields do not match the frozen schema")
    observed = dict(record)
    if observed["schema_version"] != EVALUATOR_RECORD_VERSION:
        raise ValueError("unsupported evaluator record schema")
    if observed["record_digest"] != evaluator_record_digest(observed):
        raise ValueError("evaluator record digest mismatch")
    oracle = scenario.document["independent_adequacy_oracle"]
    if (observed["protocol_identity"] != PROTOCOL_ID or observed["protocol_version"] != 1 or
            observed["run_id"] != cell.run_id or
            observed["scenario_id"] != scenario.scenario_id or
            observed["scenario_digest"] != scenario.digest or
            observed["condition"] != cell.condition.value or observed["seed"] != cell.seed or
            observed["opportunity_id"] != opportunity_id or
            observed["logical_step"] != logical_step or
            observed["commitment_id"] != oracle["commitment_id"] or
            observed["label_rule_identity"] != label_rule_identity(scenario)):
        raise ValueError("evaluator record is not bound to the scenario opportunity")
    for field in ("justification_adequate", "proposition_true",
                  "blocking_conflict_expected", "supersession_expected",
                  "actor_scope_leakage_expected", "scope_isolation_applicable",
                  "recovery_required",
                  "recorded_before_subject_outcome"):
        if type(observed[field]) is not bool:
            raise ValueError(f"evaluator record {field} must be boolean")
    if observed["recorded_before_subject_outcome"] is not True:
        raise ValueError("evaluator label must be committed before subject outcome")
    if observed["invalidation_expectation"] not in {
            "AFFECTED", "UNAFFECTED", "NOT_APPLICABLE"}:
        raise ValueError("invalid evaluator invalidation expectation")
    digest = observed["source_fixture_digest"]
    if (not isinstance(digest, str) or len(digest) != 64 or
            any(character not in "0123456789abcdef" for character in digest)):
        raise ValueError("evaluator source fixture digest must be lowercase SHA-256")
    if digest != materialized_fixture_digest:
        raise ValueError("evaluator label is bound to a different materialized fixture")
    registry = validate_evaluator_registry(
        evaluator_registry, approved_manifest_digest=approved_registry_digest)
    matches = [entry for entry in registry["entries"]
               if entry["record_digest"] == observed["record_digest"]]
    if len(matches) != 1 or dict(matches[0]) != evaluator_registry_entry(observed):
        raise ValueError("evaluator record was not committed by the approved pre-launch registry")
    return observed


def sanitize_publication(record: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively remove evaluator-only material from a publication copy."""
    if not isinstance(record, Mapping):
        raise TypeError("publication record must be an object")

    def clean(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: clean(child) for key, child in value.items()
                    if publication_key_allowed(key)}
        if isinstance(value, list):
            return [clean(child) for child in value]
        return deepcopy(value)

    result = clean(record)
    validate_publication(result)
    return result


def sanitize_metric_publication(record: Mapping[str, Any]) -> dict[str, Any]:
    without_provenance = {key: value for key, value in record.items()
                          if key != "provenance"}
    return sanitize_publication(without_provenance)
