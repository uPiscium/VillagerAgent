"""Strict JSON artifact loading, detached digest checking, and visibility rules."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Mapping
from benchmarks.common.eac.canonical import canonical_bytes
from .identity import detached_digest

_SHA256_HEX = set("0123456789abcdef")
_FORBIDDEN_KEYS = frozenset({
    "oracle", "oracle_state", "oracle_plan", "oracle_moves", "evaluator_oracle",
    "evaluator_state", "evaluator_plan", "evaluator_answer", "ground_truth",
})
_PUBLIC_FORBIDDEN_TOKENS = ("oracle", "evaluator", "groundtruth", "truthstatus", "labelrule")


def load_artifact(path: str | Path, *, expected_digest: str | None = None) -> Mapping[str, Any]:
    """Load an object artifact, rejecting duplicate keys and digest mismatches."""
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result: raise ValueError(f"duplicate artifact key: {key}")
            result[key] = value
        return result
    value = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs,
                       parse_constant=lambda token: (_ for _ in ()).throw(
                           ValueError(f"non-finite JSON number: {token}")))
    if not isinstance(value, dict): raise ValueError("artifact root must be an object")
    if expected_digest is not None and (
            not _is_digest(expected_digest) or detached_digest(value) != expected_digest):
        raise ValueError("detached artifact digest mismatch")
    validate_artifact(value)
    return value


def validate_artifact(value: Mapping[str, Any]) -> None:
    """Reject non-JSON control-plane artifacts and evaluator/oracle leakage."""
    if not isinstance(value, Mapping):
        raise TypeError("artifact root must be an object")
    def walk(item: Any) -> None:
        if isinstance(item, dict):
            if _FORBIDDEN_KEYS.intersection(item):
                raise ValueError("evaluator/oracle keys are forbidden in launch artifacts")
            for child in item.values(): walk(child)
        elif isinstance(item, list):
            for child in item: walk(child)
    walk(dict(value))
    canonical_bytes(value)


def publication_key_allowed(key: str) -> bool:
    normalized = "".join(character for character in key.lower() if character.isalnum())
    return not any(token in normalized for token in _PUBLIC_FORBIDDEN_TOKENS)


def validate_publication(value: Mapping[str, Any]) -> None:
    """Fail closed on evaluator-bearing keys at every publication depth."""
    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str) or not publication_key_allowed(key):
                    raise ValueError("evaluator material is forbidden in publication")
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
    walk(value)
    validate_artifact(value)


def load_json_object(path: str | Path) -> dict[str, Any]:
    """Load a strict, canonicalizable design artifact object."""
    return dict(load_artifact(path))


def write_json_object(path: str | Path, value: Mapping[str, Any]) -> None:
    """Write a validated design artifact using canonical JSON bytes."""
    if not isinstance(value, Mapping):
        raise TypeError("JSON artifact must be an object")
    validate_artifact(value)
    Path(path).write_bytes(canonical_bytes(dict(value)) + b"\n")


def _is_digest(value: str) -> bool:
    return len(value) == 64 and set(value) <= _SHA256_HEX
