from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping


class SeedScope(str, Enum):
    PYTHON_RANDOM = "python_random"
    META_JUDGER = "meta_judger"
    TASK_GENERATION = "task_generation"
    MODEL_SAMPLING = "model_sampling"
    WORLD_GENERATION = "world_generation"
    AGENT_ORDERING = "agent_ordering"


KNOWN_SEED_SCOPES = tuple(SeedScope)
UNSUPPORTED_SCOPE_REASON = "not implemented by the Minecraft runtime"


@dataclass(frozen=True)
class SeedContract:
    seed: int
    requested_scopes: frozenset[SeedScope]

    @classmethod
    def from_value(cls, value: object) -> SeedContract | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("seed_contract must be an object")
        unknown_fields = set(value) - {"seed", "requested_scopes"}
        if unknown_fields:
            raise ValueError(
                "seed_contract contains unknown field(s): "
                + ", ".join(sorted(str(field) for field in unknown_fields))
            )
        seed = value.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("seed_contract.seed must be an integer")
        raw_scopes = value.get("requested_scopes")
        if not isinstance(raw_scopes, list) or not raw_scopes:
            raise ValueError("seed_contract.requested_scopes must be a non-empty list")
        if any(not isinstance(scope, str) or not scope for scope in raw_scopes):
            raise ValueError(
                "seed_contract.requested_scopes must contain non-empty strings"
            )
        try:
            scopes = tuple(SeedScope(scope) for scope in raw_scopes)
        except ValueError as exc:
            unknown = sorted(set(raw_scopes) - {scope.value for scope in SeedScope})
            raise ValueError(
                "unknown seed scope(s): " + ", ".join(unknown)
            ) from exc
        if len(scopes) != len(set(scopes)):
            raise ValueError("seed_contract.requested_scopes must be unique")
        return cls(seed=seed, requested_scopes=frozenset(scopes))

    def to_dict(self) -> dict:
        return {
            "seed": self.seed,
            "requested_scopes": [
                scope.value for scope in KNOWN_SEED_SCOPES if scope in self.requested_scopes
            ],
        }

    def random(self) -> random.Random:
        """Return an isolated deterministic RNG without changing module-global state."""
        return random.Random(self.seed)


@dataclass(frozen=True)
class SeedResolution:
    contract: SeedContract
    supported_scopes: frozenset[SeedScope]
    applied_scopes: frozenset[SeedScope]

    def to_dict(self) -> dict:
        scopes = {}
        for scope in KNOWN_SEED_SCOPES:
            requested = scope in self.contract.requested_scopes
            supported = scope in self.supported_scopes
            applied = scope in self.applied_scopes
            entry = {
                "requested": requested,
                "supported": supported,
                "applied": applied,
            }
            if not supported:
                entry["reason"] = UNSUPPORTED_SCOPE_REASON
            elif not requested:
                entry["reason"] = "not requested"
            scopes[scope.value] = entry
        return {
            "schema_version": 1,
            **self.contract.to_dict(),
            "scopes": scopes,
        }


def resolve_seed_contract(
    value: object,
    *,
    supported_scopes: Iterable[SeedScope | str],
    applied_scopes: Iterable[SeedScope | str],
) -> SeedResolution | None:
    contract = SeedContract.from_value(value)
    if contract is None:
        return None
    supported = _scope_set(supported_scopes, field="supported_scopes")
    applied = _scope_set(applied_scopes, field="applied_scopes")
    invalid_applied = applied - supported
    if invalid_applied:
        raise ValueError(
            "unsupported seed scope(s) cannot be applied: "
            + ", ".join(sorted(scope.value for scope in invalid_applied))
        )
    unsupported_requested = contract.requested_scopes - supported
    if unsupported_requested:
        raise ValueError(
            "requested seed scope(s) are unsupported: "
            + ", ".join(sorted(scope.value for scope in unsupported_requested))
        )
    unapplied = contract.requested_scopes - applied
    if unapplied:
        raise ValueError(
            "requested seed scope(s) were not applied: "
            + ", ".join(sorted(scope.value for scope in unapplied))
        )
    return SeedResolution(contract, supported, applied & contract.requested_scopes)


def _scope_set(
    values: Iterable[SeedScope | str], *, field: str
) -> frozenset[SeedScope]:
    try:
        return frozenset(SeedScope(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} contains an unknown seed scope") from exc
