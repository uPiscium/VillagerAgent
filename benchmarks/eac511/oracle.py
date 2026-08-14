"""Fail-closed evaluator records and sanitized publication boundary."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from .artifacts import publication_key_allowed, validate_publication
from .identity import detached_digest, semantic_digest
from .model import MatrixCell, Scenario
from .protocol import PROTOCOL_ID

EVALUATOR_RECORD_VERSION = "eac-evaluator-record/1"
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


def validate_evaluator_record(record: Mapping[str, Any], *, cell: MatrixCell,
                              scenario: Scenario, opportunity_id: str,
                              logical_step: int,
                              materialized_fixture_digest: str) -> dict[str, Any]:
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
