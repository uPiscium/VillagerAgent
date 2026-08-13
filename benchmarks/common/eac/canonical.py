"""Constrained RFC 8785 JSON Canonicalization and digest helpers."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any


class CanonicalizationError(ValueError):
    """Input is outside the deliberately small EAC canonical value domain."""


class CanonicalTypeError(CanonicalizationError, TypeError):
    pass


MAX_DEPTH = 32
MAX_ITEMS = 4096
MAX_BYTES = 1_048_576
SAFE_INTEGER = 2**53 - 1


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
    if value is None or isinstance(value, (bool, str, int)):
        canonical_bytes(value)
        return value
    if isinstance(value, list):
        return tuple(canonical_argument(item) for item in value)
    if isinstance(value, dict):
        if any(not isinstance(key, str) or not key.isascii() for key in value):
            raise CanonicalTypeError("argument object keys must be ASCII strings")
        return tuple((key, canonical_argument(value[key])) for key in sorted(value))
    raise CanonicalTypeError(f"unsupported argument type: {type(value).__name__}")


def canonical_arguments(values: dict[str, Any]) -> bytes:
    return canonical_bytes({key: canonical_argument(values[key]) for key in sorted(values)})
