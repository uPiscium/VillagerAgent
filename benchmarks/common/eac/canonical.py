"""Constrained RFC 8785 JSON Canonicalization and digest helpers."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any


class CanonicalizationError(ValueError):
    """Input is outside the deliberately small EAC canonical value domain."""


class CanonicalTypeError(CanonicalizationError, TypeError):
    pass


MAX_DEPTH = 32
MAX_ITEMS = 4096
MAX_BYTES = 1_048_576
SAFE_INTEGER = 2**53 - 1


@dataclass(frozen=True, slots=True, eq=False)
class FrozenJSONArray:
    items: tuple[Any, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(canonical_argument(item) for item in self.items))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, FrozenJSONArray) and canonical_bytes(self) == canonical_bytes(other)

    def __hash__(self) -> int:
        return hash(canonical_bytes(self))


@dataclass(frozen=True, slots=True, eq=False)
class FrozenJSONObject:
    items: tuple[tuple[str, Any], ...]

    def __post_init__(self) -> None:
        pairs = tuple(self.items)
        if any(not isinstance(pair, tuple) or len(pair) != 2 for pair in pairs):
            raise CanonicalTypeError("frozen object items must be key/value pairs")
        keys = tuple(pair[0] for pair in pairs)
        if (len(keys) != len(set(keys))
                or any(not isinstance(key, str) or not key or not key.isascii() for key in keys)):
            raise CanonicalTypeError("frozen object keys must be unique non-empty ASCII strings")
        object.__setattr__(self, "items", tuple(sorted(
            ((key, canonical_argument(value)) for key, value in pairs), key=lambda item: item[0])))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, FrozenJSONObject) and canonical_bytes(self) == canonical_bytes(other)

    def __hash__(self) -> int:
        return hash(canonical_bytes(self))


def thaw_json(value: Any) -> Any:
    """Return ordinary JSON containers without losing frozen container types."""
    if isinstance(value, FrozenJSONArray):
        return [thaw_json(item) for item in value.items]
    if isinstance(value, FrozenJSONObject):
        return {key: thaw_json(item) for key, item in value.items}
    if isinstance(value, list):
        return [thaw_json(item) for item in value]
    if isinstance(value, dict):
        return {key: thaw_json(item) for key, item in value.items()}
    return value


def _check(value: Any, depth: int, state: list[int]) -> None:
    if depth > MAX_DEPTH:
        raise CanonicalizationError("maximum canonicalization depth exceeded")
    if isinstance(value, bool) or value is None or isinstance(value, str) or isinstance(value, int):
        if isinstance(value, int) and not isinstance(value, bool) and abs(value) > SAFE_INTEGER:
            raise CanonicalizationError("integer is outside the RFC 8785 safe range")
        if isinstance(value, str) and any("\ud800" <= c <= "\udfff" for c in value):
            raise CanonicalizationError("surrogate characters are not permitted")
        state[0] += 1
        if state[0] > MAX_ITEMS:
            raise CanonicalizationError("maximum canonical item count exceeded")
        return
    if isinstance(value, (float, complex)):
        raise CanonicalTypeError("floating point values are not permitted")
    if isinstance(value, list):
        state[0] += 1
        if state[0] > MAX_ITEMS:
            raise CanonicalizationError("maximum canonical item count exceeded")
        for item in value:
            _check(item, depth + 1, state)
        return
    if isinstance(value, dict):
        state[0] += 1
        if state[0] > MAX_ITEMS:
            raise CanonicalizationError("maximum canonical item count exceeded")
        for key, item in value.items():
            if not isinstance(key, str) or any(ord(c) > 127 for c in key):
                raise CanonicalTypeError("object keys must be ASCII strings")
            _check(item, depth + 1, state)
        return
    raise CanonicalTypeError(f"unsupported canonical value type: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    """Return JCS bytes for dict/list/string/bool/null and safe integers."""
    value = thaw_json(value)
    _check(value, 0, [0])
    try:
        result = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                            separators=(",", ":"), check_circular=True).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise CanonicalizationError(f"cannot canonicalize value: {exc}") from exc
    if len(result) > MAX_BYTES:
        raise CanonicalizationError("canonical value exceeds byte bound")
    return result


def canonical_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def canonical_argument(value: Any) -> Any:
    """Validate an effect argument and return its deterministic typed form."""
    if isinstance(value, (FrozenJSONArray, FrozenJSONObject)):
        canonical_bytes(value)
        return value
    if value is None or isinstance(value, (bool, str, int)):
        canonical_bytes(value)
        return value
    if isinstance(value, list):
        return FrozenJSONArray(tuple(canonical_argument(item) for item in value))
    if isinstance(value, dict):
        if any(not isinstance(key, str) or not key.isascii() for key in value):
            raise CanonicalTypeError("argument object keys must be ASCII strings")
        return FrozenJSONObject(tuple((key, canonical_argument(value[key])) for key in sorted(value)))
    raise CanonicalTypeError(f"unsupported argument type: {type(value).__name__}")


def canonical_arguments(values: dict[str, Any]) -> bytes:
    return canonical_bytes({key: canonical_argument(values[key]) for key in sorted(values)})
