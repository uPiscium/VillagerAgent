import json
from dataclasses import FrozenInstanceError

import pytest

from benchmarks.minecraft.collection_spec import (
    ApprovedProductionLaneSpec,
    CollectionSpecError,
    EnvironmentBindings,
    ExternallyManagedLaneSpec,
    CollectionPlan,
    collection_plan_sha256,
    collection_plan_to_dict,
    parse_collection_plan,
    validate_collection_plan,
)


def approved(lane_id="lane-a", **overrides):
    lane = {
        "lane_id": lane_id, "kind": "approved-production", "data_class": "official", "approved_experiment": "exp-1",
        "control_plane_worktree": "/srv/control-a", "control_plane_revision": "a" * 40,
        "execution_worktree": "/srv/exec-a", "execution_revision": "b" * 40,
        "python_executable": "/usr/bin/python3", "output_root": "/srv/output-a",
        "batch_count": 2, "batch_timeout_seconds": 30.0,
        "environment": {"docker_host_env": "DOCKER_HOST", "docker_context_env": "DOCKER_CONTEXT",
                         "model_api_base_env": "MODEL_BASE", "model_api_key_env": "MODEL_KEY",
                         "lock_root": "/srv/lock-a"},
        "resource_groups": ["gpu:gpu-a"],
    }
    lane.update(overrides)
    return lane


def external(lane_id="external"):
    return {"lane_id": lane_id, "kind": "externally-managed", "data_class": "telemetry",
            "resource_groups": ["external:external-a"]}


def plan(*lanes):
    return {"schema_version": 1, "session_id": "session-1", "max_parallel_lanes": len(lanes),
            "lanes": list(lanes)}


def test_valid_mixed_plan_roundtrips_and_hash_is_deterministic():
    parsed = parse_collection_plan(plan(approved(), external()))
    assert len(parsed.lanes) == 2
    assert parse_collection_plan(json.dumps(collection_plan_to_dict(parsed))) == parsed
    reordered = {"lanes": [dict(reversed(list(item.items()))) for item in plan(approved(), external())["lanes"]],
                 "max_parallel_lanes": 2, "session_id": "session-1", "schema_version": 1}
    assert collection_plan_sha256(parsed) == collection_plan_sha256(parse_collection_plan(reordered))
    assert collection_plan_sha256(parsed) == "692f8fd6ce84ad53454e1a0421a9efa7932dfddcc43c375741ca28ca631eae5d"


@pytest.mark.parametrize("bad", [
    lambda p: p["lanes"].append(approved("lane-a")),
    lambda p: p["lanes"][0].update(control_plane_worktree="relative"),
    lambda p: p["lanes"][0].update(batch_count=True),
    lambda p: p["lanes"][0].update(batch_count=0),
    lambda p: p["lanes"][0].update(batch_count=-1),
    lambda p: p["lanes"][0].update(batch_timeout_seconds=float("inf")),
    lambda p: p["lanes"][0].update(batch_timeout_seconds=0),
    lambda p: p["lanes"][0].update(approved_experiment=""),
    lambda p: p["lanes"][0].update(kind="runtime-soak"),
    lambda p: p.update(schema_version=99),
    lambda p: p["lanes"][0].update(environment={"docker_host_env": "bad-name", "docker_context_env": None,
                                                   "model_api_base_env": "BASE", "model_api_key_env": "KEY",
                                                   "lock_root": "/srv/lock-a"}),
    lambda p: p["lanes"][0].update(resource_groups=["gpu-a"]),
    lambda p: p["lanes"][0].pop("approved_experiment"),
    lambda p: p["lanes"][0].update(api_key="secret"),
    lambda p: p.update(max_parallel_lanes=2),
])
def test_invalid_plan_shapes_are_rejected(bad):
    value = plan(approved())
    bad(value)
    with pytest.raises(CollectionSpecError):
        parse_collection_plan(value)


def test_external_lane_rejects_command_and_secret_like_unknown_fields():
    value = plan(external())
    value["lanes"][0]["command"] = "run"
    with pytest.raises(CollectionSpecError):
        parse_collection_plan(value)


def test_duplicate_json_keys_and_malformed_json_are_rejected():
    with pytest.raises(CollectionSpecError):
        parse_collection_plan('{"schema_version":1,"schema_version":1,"session_id":"s","max_parallel_lanes":1,"lanes":[]}')
    with pytest.raises(CollectionSpecError):
        parse_collection_plan("{")


def test_shared_paths_and_groups_are_rejected():
    second = approved("lane-b", control_plane_worktree="/srv/control-b", execution_worktree="/srv/exec-b",
                      output_root="/srv/output-b", resource_groups=["gpu:gpu-b"])
    second["environment"]["lock_root"] = "/srv/lock-a"
    with pytest.raises(CollectionSpecError):
        parse_collection_plan(plan(approved(), second))


def test_dataclasses_are_frozen():
    parsed = parse_collection_plan(plan(approved()))
    with pytest.raises(FrozenInstanceError):
        parsed.session_id = "changed"


def test_direct_dataclass_validation_rejects_discriminator_and_nested_types():
    env = EnvironmentBindings("DOCKER_HOST", "DOCKER_CONTEXT", "MODEL_BASE", "MODEL_KEY", "/srv/lock")
    lane = ApprovedProductionLaneSpec("lane", "externally-managed", "official", "exp", "/srv/c", "a" * 40,
                                     "/srv/e", "b" * 40, "/usr/bin/python", "/srv/o", 1, 1.0, env, ("gpu:x",))
    with pytest.raises(CollectionSpecError):
        validate_collection_plan(CollectionPlan(1, "session", 1, (lane,)))
    external_lane = ExternallyManagedLaneSpec("external", "externally-managed", "official", ["external:x"])
    with pytest.raises(CollectionSpecError):
        validate_collection_plan(CollectionPlan(1, "session", 1, (external_lane,)))


def test_canonical_path_aliases_overlap_and_environment_aliases_rejected():
    value = plan(approved())
    value["lanes"][0]["output_root"] = "/srv/output-a/"
    with pytest.raises(CollectionSpecError):
        parse_collection_plan(value)


@pytest.mark.parametrize("data_class", [None, {}, ""])
def test_external_data_class_is_nonempty_identifier(data_class):
    value = plan(external())
    value["lanes"][0]["data_class"] = data_class
    with pytest.raises(CollectionSpecError):
        parse_collection_plan(value)


def test_json_and_mapping_nonfinite_timeouts_and_max_parallel_bounds_rejected():
    value = plan(approved())
    value["lanes"][0]["batch_timeout_seconds"] = float("nan")
    with pytest.raises(CollectionSpecError):
        parse_collection_plan(value)
    with pytest.raises(CollectionSpecError):
        parse_collection_plan(json.dumps(plan(approved())).replace("30.0", "NaN"))
    value = plan(approved())
    value["lanes"][0]["output_root"] = "/srv/control-a/sub"
    with pytest.raises(CollectionSpecError):
        parse_collection_plan(value)
    value = plan(approved())
    value["lanes"][0]["environment"]["model_api_key_env"] = "DOCKER_HOST"
    with pytest.raises(CollectionSpecError):
        parse_collection_plan(value)
