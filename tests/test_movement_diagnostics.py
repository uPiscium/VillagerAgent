from types import SimpleNamespace

import pytest

from env.movement_diagnostics import (
    EUCLIDEAN_DISTANCE,
    STRICT_PER_AXIS,
    evaluate_movement_completion,
    movement_status,
)


def position(x, y, z):
    return SimpleNamespace(x=x, y=y, z=z)


def test_judged_exact_axis_boundary_is_not_reached():
    completion = evaluate_movement_completion(
        position(5.5, -59, 5.483812240562806),
        position(5, -60, 5),
        1,
        policy=STRICT_PER_AXIS,
        position_convention="entity_feet",
    )

    assert completion["target_reached"] is False
    assert movement_status(True, completion) is False
    assert completion["axis_delta"] == {
        "x": 0.5,
        "y": 1.0,
        "z": 0.4838122405628056,
    }
    assert completion["completion_policy"] == STRICT_PER_AXIS
    assert completion["position_convention"] == "entity_feet"
    assert completion["remaining_delta"]["y"] == -1.0


def test_judged_diagonal_within_each_axis_is_reached():
    completion = evaluate_movement_completion(
        position(0.99, 0.99, 0.99),
        position(0, 0, 0),
        1,
        policy=STRICT_PER_AXIS,
    )

    assert completion["distance_to_target"] > 1
    assert completion["target_reached"] is True


def test_default_diagonal_uses_euclidean_distance():
    completion = evaluate_movement_completion(
        position(0.9, 0.9, 0.9),
        position(0, 0, 0),
        1,
    )

    assert completion["target_reached"] is False
    assert completion["completion_policy"] == EUCLIDEAN_DISTANCE
    assert completion["completion_semantics"] == "euclidean_distance_below_tolerance"


def test_default_exact_euclidean_boundary_is_not_reached():
    completion = evaluate_movement_completion(
        position(1, 0, 0),
        position(0, 0, 0),
        1,
    )

    assert completion["distance_to_target"] == 1
    assert completion["target_reached"] is False


def test_non_default_tolerance_keeps_policies_distinct():
    current = position(1.5, 1.5, 0)
    target = position(0, 0, 0)

    distance = evaluate_movement_completion(current, target, 2)
    strict = evaluate_movement_completion(
        current,
        target,
        2,
        policy=STRICT_PER_AXIS,
    )

    assert distance["target_reached"] is False
    assert strict["target_reached"] is True


def test_unknown_completion_policy_is_rejected():
    with pytest.raises(ValueError, match="unsupported movement completion policy"):
        evaluate_movement_completion(
            position(0, 0, 0),
            position(0, 0, 0),
            1,
            policy="unknown",
        )
