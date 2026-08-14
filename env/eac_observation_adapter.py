"""Pure sanitization contract for actor-visible Minecraft scan results."""
from __future__ import annotations

from collections.abc import Mapping
import math


def _canonical_position(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    coordinates = []
    for coordinate in value:
        if (isinstance(coordinate, bool) or not isinstance(coordinate, (int, float))
                or not math.isfinite(coordinate)):
            return None
        if isinstance(coordinate, float):
            if not coordinate.is_integer():
                return None
            coordinate = int(coordinate)
        coordinates.append(coordinate)
    return coordinates


def sanitized_scan_rows(result, request_arguments=None):
    if not isinstance(result, Mapping) or result.get("status") is not True:
        return ()
    rows = result.get("data")
    if not isinstance(rows, list):
        return ()
    output = []
    for row in rows[:128]:
        if not isinstance(row, Mapping):
            continue
        name = (row.get("name") or row.get("item_name") or row.get("type")
                or result.get("observed_name") or (request_arguments or {}).get("item_name"))
        if not isinstance(name, str) or not name:
            name = None
        position = row.get("position")
        if position is None and all(key in row for key in ("x", "y", "z")):
            position = [row["x"], row["y"], row["z"]]
        position = _canonical_position(position)
        if name is not None or position is not None:
            output.append((name, position))
    return tuple(output)


def sanitized_visible_blocks(state, *, limit=128):
    """Extract the same bounded block positions shown in an actor's environment state."""
    if not isinstance(state, Mapping) or state.get("status") is not True:
        return ()
    message = state.get("message")
    blocks = message.get("blocks") if isinstance(message, Mapping) else None
    if not isinstance(blocks, list):
        return ()
    output, seen = [], set()
    for row in blocks[:limit]:
        if not isinstance(row, Mapping):
            continue
        name, position = row.get("name"), row.get("position")
        if name is None:
            named = [(key, value) for key, value in row.items()
                     if key not in {"facing", "axis", "part", "hinge", "powered", "face", "open"}
                     and isinstance(value, (list, tuple)) and len(value) == 3]
            if len(named) == 1:
                name, position = named[0]
        normalized_name = name.removeprefix("minecraft:") if isinstance(name, str) else name
        coordinates = _canonical_position(position)
        if (not isinstance(name, str) or not name or normalized_name in {"air", "cave_air", "void_air"}
                or coordinates is None):
            continue
        coordinate_key = tuple(coordinates)
        if coordinate_key in seen:
            continue
        seen.add(coordinate_key)
        output.append((name, coordinates))
    return tuple(output)
