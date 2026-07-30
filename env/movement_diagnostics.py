import math


EUCLIDEAN_DISTANCE = "euclidean_distance"
STRICT_PER_AXIS = "strict_per_axis"


def evaluate_movement_completion(
    position,
    target,
    tolerance,
    *,
    policy=EUCLIDEAN_DISTANCE,
):
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
    distance = math.sqrt(sum(delta ** 2 for delta in axis_delta.values()))
    if policy == EUCLIDEAN_DISTANCE:
        reached = distance < tolerance
        semantics = "euclidean_distance_below_tolerance"
    elif policy == STRICT_PER_AXIS:
        reached = all(delta < tolerance for delta in axis_delta.values())
        semantics = "all_axis_deltas_strictly_below_tolerance"
    else:
        raise ValueError(f"unsupported movement completion policy: {policy}")
    return {
        "requested_target": requested,
        "observed_position": observed,
        "distance_to_target": distance,
        "axis_delta": axis_delta,
        "target_tolerance": float(tolerance),
        "target_reached": reached,
        "completion_policy": policy,
        "completion_semantics": semantics,
    }


def movement_status(move_succeeded, completion):
    return bool(move_succeeded and completion.get("target_reached") is True)
