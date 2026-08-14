"""Pure sanitization contract for actor-visible Minecraft scan results."""
from __future__ import annotations

from collections.abc import Mapping
import math


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
        position = row.get("position")
        if position is None and all(key in row for key in ("x", "y", "z")):
            position = [row["x"], row["y"], row["z"]]
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
        if (not isinstance(name, str) or not name or "air" in name
                or not isinstance(position, (list, tuple)) or len(position) != 3
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) for value in position)):
            continue
        coordinates = tuple(position)
        if coordinates in seen:
            continue
        seen.add(coordinates)
        output.append((name, coordinates))
    return tuple(output)
