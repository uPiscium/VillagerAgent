import json
import hashlib
import math
from pathlib import Path
import shutil
import subprocess

import pytest

from benchmarks.minecraft.position_contract import (
    PositionConvention,
    entity_feet_position,
    entity_feet_to_block_cell,
    entity_feet_to_support_block,
    observe_entity_feet,
    normalize_observed_position,
    resolve_position_convention,
)
from env.movement_diagnostics import STRICT_PER_AXIS, evaluate_movement_completion


FIXTURES = Path("tests/fixtures/minecraft_entity_feet_positions.json")


def test_entity_feet_conversions_cover_centers_and_negative_coordinates():
    assert entity_feet_to_block_cell((5.5, -59.0, 5.4838)).as_dict() == {
        "x": 5,
        "y": -59,
        "z": 5,
    }
    assert entity_feet_to_support_block((5.5, -59.0, 5.4838)).as_dict() == {
        "x": 5,
        "y": -60,
        "z": 5,
    }
    observation = observe_entity_feet((-0.1, -59.0, -4.2))
    assert observation.block_cell.as_dict() == {"x": -1, "y": -59, "z": -5}
    assert observation.support_block.as_dict() == {"x": -1, "y": -60, "z": -5}


def test_position_convention_is_explicit_and_invalid_values_fail_closed():
    assert resolve_position_convention("entity_feet") is PositionConvention.ENTITY_FEET
    assert resolve_position_convention(None) is None
    with pytest.raises(ValueError, match="required"):
        resolve_position_convention(None, required=True)
    with pytest.raises(ValueError, match="unsupported"):
        resolve_position_convention("feet")
    with pytest.raises(ValueError, match="finite"):
        entity_feet_position((0, math.inf, 0))


def test_support_block_normalization_requires_full_block_world_evidence():
    observed = (5.5, -59.0, 5.4838)
    with pytest.raises(ValueError, match="world query"):
        normalize_observed_position(
            observed, target_convention=PositionConvention.SUPPORT_BLOCK
        )
    assert normalize_observed_position(
        observed,
        target_convention=PositionConvention.SUPPORT_BLOCK,
        world_query=lambda _position: {
            "collision_shape": "full_block",
            "fluid": None,
            "falling": False,
        },
    ).as_dict() == {"x": 5, "y": -60, "z": 5}
    with pytest.raises(ValueError, match="full-block"):
        normalize_observed_position(
            observed,
            target_convention=PositionConvention.SUPPORT_BLOCK,
            world_query=lambda _position: {"collision_shape": "slab"},
        )


def test_issue_445_failed_position_remains_entity_feet_failure():
    result = evaluate_movement_completion(
        entity_feet_position((5.5, -59.0, 5.483812240562806)),
        entity_feet_position((5, -60, 5)),
        1.0,
        policy=STRICT_PER_AXIS,
        position_convention=PositionConvention.ENTITY_FEET,
    )

    assert result["target_reached"] is False
    assert result["axis_delta"] == {
        "x": 0.5,
        "y": 1.0,
        "z": 0.4838122405628056,
    }
    assert result["remaining_delta"] == {
        "x": -0.5,
        "y": -1.0,
        "z": -0.4838122405628056,
    }
    assert result["position_contract"]["observed"]["support_block"] == {
        "x": 5,
        "y": -60,
        "z": 5,
    }
    with pytest.raises(ValueError, match="entity_feet"):
        evaluate_movement_completion(
            entity_feet_position((5.5, -59.0, 5.5)),
            entity_feet_position((5, -60, 5)),
            1.0,
            policy=STRICT_PER_AXIS,
            position_convention=PositionConvention.SUPPORT_BLOCK,
        )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is unavailable")
def test_python_and_javascript_position_contracts_match_fixtures():
    fixture = json.loads(FIXTURES.read_text(encoding="utf-8"))
    completed = subprocess.run(
        ["node", "benchmarks/minecraft/position_contract.js", str(FIXTURES)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    javascript = {
        item["id"]: item for item in json.loads(completed.stdout)["results"]
    }
    for item in fixture["cases"]:
        js = javascript[item["id"]]
        if "error" in item:
            assert js["error"] == item["error"]
            continue
        python = evaluate_movement_completion(
            entity_feet_position(item["observed"]),
            entity_feet_position(item["target"]),
            item["tolerance"],
            policy=STRICT_PER_AXIS,
            position_convention=item["position_convention"],
        )
        assert js["result"]["target_reached"] is item["reached"]
        assert js["result"]["axis_delta"] == python["axis_delta"]
        assert js["result"]["remaining_delta"] == python["remaining_delta"]
        assert js["result"]["observation"] == python["position_contract"]["observed"]


def test_approved_baseline_attestation_revalidates_all_six_conditions():
    asset_root = Path("benchmarks/minecraft/assets/issue_443")
    registry = json.loads((asset_root / "baseline_registry.json").read_text())
    contract = registry["position_contract"]
    attestation_path = Path(contract["attestation"]["path"])
    assert hashlib.sha256(attestation_path.read_bytes()).hexdigest() == contract[
        "attestation"
    ]["sha256"]
    attestation = json.loads(attestation_path.read_text())
    assert attestation["position_convention"] == "entity_feet"
    assert attestation["archive_and_tree_hashes_unchanged"] is True
    assert attestation["total_reachable_conditions"] == 6
    for baseline in attestation["baselines"]:
        provenance = json.loads(
            (
                asset_root
                / baseline["baseline_id"]
                / "baseline_provenance.json"
            ).read_text()
        )
        probes = provenance["reachability_probes"]
        assert [item["variant_id"] for item in probes] == baseline[
            "reachable_variants"
        ]
        assert all(item["reachable"] is True for item in probes)
        assert all("y=0.000" in item["evidence"] for item in probes)
