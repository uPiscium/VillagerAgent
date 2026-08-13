"""Pure sanitization contract for actor-visible Minecraft scan results."""
from __future__ import annotations

from collections.abc import Mapping


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
