import math

from benchmarks.minecraft.position_contract import (
    PositionConvention,
    observe_entity_feet,
    resolve_position_convention,
)


EUCLIDEAN_DISTANCE = "euclidean_distance"
STRICT_PER_AXIS = "strict_per_axis"


def evaluate_movement_completion(
    position,
    target,
    tolerance,
    *,
    policy=EUCLIDEAN_DISTANCE,
    position_convention=None,
):
    convention = resolve_position_convention(position_convention)
    if convention not in {None, PositionConvention.ENTITY_FEET}:
        raise ValueError(
            "movement completion can compare raw positions only in the entity_feet convention"
        )
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
    remaining_delta = {
        axis: requested[axis] - observed[axis]
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
    result = {
        "requested_target": requested,
        "observed_position": observed,
        "distance_to_target": distance,
        "axis_delta": axis_delta,
        "remaining_delta": remaining_delta,
        "target_tolerance": float(tolerance),
        "target_reached": reached,
        "completion_policy": policy,
        "completion_semantics": semantics,
    }
    if convention is not None:
        result["position_convention"] = convention.value
        result["position_contract"] = {
            "target_convention": convention.value,
            "raw_entity_feet": observed,
            "observed": observe_entity_feet(observed).as_dict(),
            "normalized_observed": observed,
            "target": requested,
            "per_axis_delta": axis_delta,
            "remaining_delta": remaining_delta,
        }
    return result


def movement_status(move_succeeded, completion):
    return bool(move_succeeded and completion.get("target_reached") is True)
