from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from types import MappingProxyType

from benchmarks.minecraft.position_contract import PositionConvention


VARIANT_SCHEMA_VERSION = 2
VARIANT_ORDER = ("near", "diagonal", "long_distance")


@dataclass(frozen=True)
class MovementTarget:
    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        for axis, value in (("x", self.x), ("y", self.y), ("z", self.z)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"target {axis} must be numeric")
            if not math.isfinite(value):
                raise ValueError(f"target {axis} must be finite")

    def as_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "z": self.z}


@dataclass(frozen=True)
class MovementVariant:
    variant_id: str
    initial_position: MovementTarget
    target: MovementTarget
    prompt: str
    evaluation: str
    position_convention: str
    completion_policy: str = "strict_per_axis"
    completion_semantics: str = "all_axis_deltas_strictly_below_tolerance"
    tolerance: float = 1.0

    def as_dict(self) -> dict:
        return {
            "schema_version": VARIANT_SCHEMA_VERSION,
            "variant_id": self.variant_id,
            "initial_position": self.initial_position.as_dict(),
            "target": self.target.as_dict(),
            "prompt": self.prompt,
            "evaluation": self.evaluation,
            "position_convention": self.position_convention,
            "completion_policy": self.completion_policy,
            "completion_semantics": self.completion_semantics,
            "tolerance": self.tolerance,
        }

    @property
    def definition_sha256(self) -> str:
        encoded = json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _variant(variant_id: str, x: float, y: float, z: float) -> MovementVariant:
    return MovementVariant(
        variant_id=variant_id,
        initial_position=MovementTarget(x=14, y=-59, z=5),
        target=MovementTarget(x=x, y=y, z=z),
        prompt=f"Move to ({x:g}, {y:g}, {z:g}). You can go there directly.",
        evaluation="final_position_strict_per_axis",
        position_convention=PositionConvention.ENTITY_FEET.value,
    )


MOVEMENT_VARIANTS = MappingProxyType({
    "near": _variant("near", 10, -59, 5),
    "diagonal": _variant("diagonal", 5, -60, 5),
    "long_distance": _variant("long_distance", 20, -60, 18),
})


def get_movement_variant(variant_id: str) -> MovementVariant:
    try:
        return MOVEMENT_VARIANTS[variant_id]
    except KeyError as exc:
        known = ", ".join(VARIANT_ORDER)
        raise ValueError(f"unknown movement variant {variant_id!r}; expected one of: {known}") from exc


def variant_definition_hash(variant_id: str) -> str:
    return get_movement_variant(variant_id).definition_sha256
