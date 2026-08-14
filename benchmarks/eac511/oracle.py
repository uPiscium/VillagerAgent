"""Evaluator-only oracle and sanitized publication boundary."""
from __future__ import annotations
from dataclasses import dataclass
from copy import deepcopy
from typing import Any, Mapping
from .artifacts import publication_key_allowed, validate_publication


@dataclass(frozen=True, slots=True)
class EvaluatorOracle:
    """Oracle state intentionally has no import path into runtime modules."""
    state: Mapping[str, Any]

    def evaluate(self, observation: Mapping[str, Any]) -> bool:
        return bool(self.state.get("expected", observation.get("outcome", False)))


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
    """Remove internal provenance before metric serialization for publication."""
    without_provenance = {key: value for key, value in record.items()
                          if key != "provenance"}
    return sanitize_publication(without_provenance)
