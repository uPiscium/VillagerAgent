"""Strict Advisory/Authority pre-gate equivalence comparison."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from benchmarks.common.eac.canonical import canonical_bytes
from .protocol import PRE_GATE_EQUIVALENCE_FIELDS
from .identity import FROZEN_510, semantic_digest
from .matrix import paired_cell_equal, validate_matrix_cell
from .model import Condition, MatrixCell, Scenario


PRE_GATE_FIELDS = PRE_GATE_EQUIVALENCE_FIELDS
_ALIASES = {"evidence": "required_evidence", "exact_request": "request",
            "manifest": "dependency_manifest"}


def _require_ref(value: Any, field: str, *, digest: bool = False) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a versioned reference")
    required = {"identity", "version"} | ({"digest"} if digest else set())
    if set(value) != required:
        raise ValueError(f"{field} has invalid reference fields")
    if not isinstance(value["identity"], str) or not value["identity"]:
        raise ValueError(f"{field}.identity must be a non-empty string")
    if (isinstance(value["version"], bool) or not isinstance(value["version"], (int, str)) or
            (isinstance(value["version"], str) and not value["version"])):
        raise ValueError(f"{field}.version must be typed")
    if digest and (not isinstance(value["digest"], str) or
                   len(value["digest"].removeprefix("sha256:")) != 64 or
                   any(c not in "0123456789abcdef" for c in value["digest"].removeprefix("sha256:"))):
        raise ValueError(f"{field}.digest must be a lowercase SHA-256")


def _validate_request(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != {"candidate_id", "attempt_id", "action", "arguments", "target"}:
        raise ValueError("request must include candidate/request identity, arguments, and target")
    for key in ("candidate_id", "attempt_id"):
        if not isinstance(value[key], str) or not value[key]:
            raise ValueError(f"request.{key} must be a non-empty string")
    _require_ref(value["action"], "request.action", digest=True)
    if not isinstance(value["arguments"], (list, Mapping)):
        raise ValueError("request.arguments must be a canonical array or object")
    canonical_bytes(value["arguments"]); canonical_bytes(value["target"])


def _validate_manifest(value: Any) -> None:
    required = {"fingerprint", "actor_scope", "expectations", "conflict_watches",
                "policy_binding", "profile_binding"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("dependency_manifest fields do not match the frozen contract")
    fingerprint = value["fingerprint"]
    if (not isinstance(fingerprint, str) or
            len(fingerprint.removeprefix("sha256:")) != 64):
        raise ValueError("dependency_manifest.fingerprint must be a SHA-256")
    scope = value["actor_scope"]
    if (not isinstance(scope, Mapping) or set(scope) != {"actor_id", "visibility_revision", "scope"}
            or not isinstance(scope["actor_id"], str) or not scope["actor_id"]
            or type(scope["visibility_revision"]) is not int or not isinstance(scope["scope"], list)):
        raise ValueError("dependency_manifest.actor_scope is invalid")
    if not isinstance(value["expectations"], list) or not isinstance(value["conflict_watches"], list):
        raise ValueError("dependency manifest expectations/conflict watches must be arrays")
    for item in value["expectations"]:
        if (not isinstance(item, Mapping) or set(item) != {"dependency_id", "revision", "kind"}
                or not isinstance(item["dependency_id"], str) or not item["dependency_id"]
                or not isinstance(item["kind"], str) or not item["kind"]):
            raise ValueError("dependency manifest expectation is invalid")
    _require_ref(value["policy_binding"], "dependency_manifest.policy_binding", digest=True)
    _require_ref(value["profile_binding"], "dependency_manifest.profile_binding", digest=True)
    canonical_bytes(value)


def _sha256(value: Any, field: str) -> None:
    if (not isinstance(value, str) or len(value) != 64 or
            any(character not in "0123456789abcdef" for character in value)):
        raise ValueError(f"{field} must be a lowercase SHA-256")


def _validate_runtime_identity(value: Any) -> None:
    required = {"execution_revision", "manifest_digest", "premanifest_identity"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("runtime_identity fields do not match the frozen contract")
    revision = value["execution_revision"]
    if (not isinstance(revision, str) or len(revision) != 40 or
            any(character not in "0123456789abcdef" for character in revision)):
        raise ValueError("runtime_identity.execution_revision must be a Git commit")
    _sha256(value["manifest_digest"], "runtime_identity.manifest_digest")
    _sha256(value["premanifest_identity"], "runtime_identity.premanifest_identity")


@dataclass(frozen=True, slots=True)
class GateComparison:
    """Comparison result; only final enforcement is allowed to differ."""
    equivalent: bool
    advisory_digest: str
    authority_digest: str
    differences: tuple[str, ...] = ()


def _projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("pre-gate result must be a mapping")
    normalized = dict(value)
    for alias, field in _ALIASES.items():
        if field not in normalized and alias in normalized:
            normalized[field] = normalized[alias]
    missing = tuple(field for field in PRE_GATE_FIELDS if field not in normalized)
    if missing:
        raise ValueError(f"pre-gate result missing required fields: {', '.join(missing)}")
    _require_ref(normalized["source_profile"], "source_profile", digest=True)
    _require_ref(normalized["policy"], "policy", digest=True)
    _validate_request(normalized["request"])
    _validate_manifest(normalized["dependency_manifest"])
    if type(normalized["seed"]) is not int:
        raise ValueError("seed must be an integer")
    for field in ("scenario_digest", "initial_state_digest", "materialized_fixture_digest",
                  "history_prefix_digest"):
        _sha256(normalized[field], field)
    _validate_runtime_identity(normalized["runtime_identity"])
    if (not isinstance(normalized["opportunity_id"], str) or not normalized["opportunity_id"] or
            normalized["opportunity_role"] not in {"primary", "recovery"}):
        raise ValueError("snapshot opportunity identity/role is invalid")
    return {field: normalized[field] for field in PRE_GATE_FIELDS}


def compare_pre_gate(advisory: Any, authority: Any) -> GateComparison:
    """Compare exactly the shared pre-gate evidence and decision inputs.

    Enforcement, permits, effects, and condition-specific output are
    deliberately outside the projection.  Missing required inputs fail closed.
    """
    a = canonical_bytes(_projection(advisory))
    b = canonical_bytes(_projection(authority))
    digest = lambda value: hashlib.sha256(value).hexdigest()
    differences = tuple(field for field in PRE_GATE_FIELDS
                        if canonical_bytes(_projection(advisory)[field]) !=
                        canonical_bytes(_projection(authority)[field]))
    return GateComparison(not differences, digest(a), digest(b), differences)


def pre_gate_snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    return semantic_digest(_projection(snapshot))


def validate_pre_gate_snapshot(cell: MatrixCell, scenario: Scenario,
                               snapshot: Mapping[str, Any]) -> None:
    validate_matrix_cell(cell, scenario)
    projected = _projection(snapshot)
    expected_runtime = {
        "execution_revision": FROZEN_510.execution_revision,
        "manifest_digest": FROZEN_510.runtime_manifest_digest,
        "premanifest_identity": FROZEN_510.premanifest_identity,
    }
    if (projected["seed"] != cell.seed or
            projected["scenario_digest"] != scenario.digest or
            projected["policy"] != scenario.document["support_policy"] or
            projected["source_profile"] != scenario.document["source_profile"] or
            projected["task"] != scenario.document["task_fixture_id"] or
            projected["runtime_identity"] != expected_runtime):
        raise ValueError("pre-gate snapshot differs from its cell, scenario, or frozen runtime")
    if (projected["opportunity_role"] == "primary" and
            projected["epre"] != scenario.document["affected_epre"]):
        raise ValueError("primary snapshot EPre differs from the frozen scenario")


def compare_paired_pre_gate(advisory_cell: MatrixCell, authority_cell: MatrixCell,
                            scenario: Scenario, advisory_snapshot: Mapping[str, Any],
                            authority_snapshot: Mapping[str, Any]) -> GateComparison:
    """Fail closed before analysis unless a paired run has equal canonical snapshots."""
    validate_matrix_cell(advisory_cell, scenario)
    validate_matrix_cell(authority_cell, scenario)
    validate_pre_gate_snapshot(advisory_cell, scenario, advisory_snapshot)
    validate_pre_gate_snapshot(authority_cell, scenario, authority_snapshot)
    if (advisory_snapshot["opportunity_role"] != "primary" or
            authority_snapshot["opportunity_role"] != "primary"):
        raise ValueError("primary enforcement analysis requires primary opportunity snapshots")
    if (advisory_cell.condition is not Condition.ADVISORY or
            authority_cell.condition is not Condition.AUTHORITY or
            not paired_cell_equal(advisory_cell, authority_cell)):
        raise ValueError("pre-gate comparison requires paired Advisory/Authority cells")
    comparison = compare_pre_gate(advisory_snapshot, authority_snapshot)
    if not comparison.equivalent:
        raise ValueError(f"paired pre-gate snapshots differ: {comparison.differences}")
    return comparison
