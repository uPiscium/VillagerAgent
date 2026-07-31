from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping


class PositionConvention(str, Enum):
    ENTITY_FEET = "entity_feet"
    BLOCK_CELL = "block_cell"
    SUPPORT_BLOCK = "support_block"


@dataclass(frozen=True)
class BlockPosition:
    x: int
    y: int
    z: int

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "z": self.z}


@dataclass(frozen=True)
class EntityFeetPosition:
    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.x, self.y, self.z)):
            raise ValueError("entity-feet coordinates must be finite")

    def as_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "z": self.z}


@dataclass(frozen=True)
class PositionObservation:
    entity_feet: EntityFeetPosition
    block_cell: BlockPosition
    support_block: BlockPosition

    def as_dict(self) -> dict[str, dict[str, int | float]]:
        return {
            "entity_feet": self.entity_feet.as_dict(),
            "block_cell": self.block_cell.as_dict(),
            "support_block": self.support_block.as_dict(),
        }


def resolve_position_convention(
    value: str | PositionConvention | None,
    *,
    required: bool = False,
) -> PositionConvention | None:
    if value is None:
        if required:
            raise ValueError("position convention is required")
        return None
    try:
        return value if isinstance(value, PositionConvention) else PositionConvention(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unsupported position convention: {value!r}") from exc


def entity_feet_position(position: Any) -> EntityFeetPosition:
    if isinstance(position, Mapping):
        values = tuple(position[axis] for axis in ("x", "y", "z"))
    elif isinstance(position, (tuple, list)) and len(position) == 3:
        values = tuple(position)
    else:
        values = tuple(getattr(position, axis) for axis in ("x", "y", "z"))
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise ValueError("entity-feet coordinates must be numeric")
    return EntityFeetPosition(*(float(value) for value in values))


def entity_feet_to_block_cell(position: Any) -> BlockPosition:
    feet = entity_feet_position(position)
    return BlockPosition(math.floor(feet.x), math.floor(feet.y), math.floor(feet.z))


def entity_feet_to_support_block(position: Any) -> BlockPosition:
    cell = entity_feet_to_block_cell(position)
    return BlockPosition(cell.x, cell.y - 1, cell.z)


def observe_entity_feet(position: Any) -> PositionObservation:
    feet = entity_feet_position(position)
    cell = entity_feet_to_block_cell(feet)
    return PositionObservation(
        entity_feet=feet,
        block_cell=cell,
        support_block=BlockPosition(cell.x, cell.y - 1, cell.z),
    )


def normalize_observed_position(
    raw_position: Any,
    *,
    target_convention: str | PositionConvention,
    world_query: Callable[[BlockPosition], Mapping[str, Any]] | None = None,
) -> EntityFeetPosition | BlockPosition:
    convention = resolve_position_convention(target_convention, required=True)
    if convention is PositionConvention.ENTITY_FEET:
        return entity_feet_position(raw_position)
    if convention is PositionConvention.BLOCK_CELL:
        return entity_feet_to_block_cell(raw_position)
    if world_query is None:
        raise ValueError("support_block normalization requires a world query")
    support = entity_feet_to_support_block(raw_position)
    state = world_query(support)
    if not isinstance(state, Mapping) or state.get("collision_shape") != "full_block":
        raise ValueError("support_block normalization requires a full-block collision shape")
    if state.get("fluid") in {"water", "lava"} or state.get("falling") is True:
        raise ValueError("support_block normalization rejects fluid and falling states")
    return support
