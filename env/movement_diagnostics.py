import math


def movement_completion(position, target, tolerance):
    observed = {
        "x": float(position.x),
        "y": float(position.y),
        "z": float(position.z),
    }
    requested = {
        "x": float(target.x),
        "y": float(target.y),
        "z": float(target.z),
    }
    axis_delta = {
        axis: abs(observed[axis] - requested[axis])
        for axis in ("x", "y", "z")
    }
    return {
        "requested_target": requested,
        "observed_position": observed,
        "distance_to_target": math.sqrt(sum(delta ** 2 for delta in axis_delta.values())),
        "axis_delta": axis_delta,
        "target_tolerance": float(tolerance),
        "target_reached": all(delta < tolerance for delta in axis_delta.values()),
        "completion_semantics": "all_axis_deltas_strictly_below_tolerance",
    }
