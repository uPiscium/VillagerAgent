"""Strict Advisory/Authority pre-gate equivalence comparison."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from benchmarks.common.eac.canonical import canonical_bytes


PRE_GATE_FIELDS = (
    "required_evidence", "epre", "classification", "policy", "witness",
    "eadm", "candidate", "task",
)
_ALIASES = {"evidence": "required_evidence"}


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
