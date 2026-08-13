import json
from pathlib import Path

import pytest

from benchmarks.minecraft.eac_identity import EXECUTION_ID, child_manifest, runtime_identity, verify_eac_premanifest
from benchmarks.minecraft.gate_a_v4_owned_execution import FIXED_REVISION, FIXED_RUNTIME_DIGEST


ROOT = Path(__file__).resolve().parents[1]
FROZEN_REVISION = "issue-510-minecraft-eac-v1"


def test_minecraft_eac_identity_is_distinct_and_deterministic():
    first = runtime_identity(execution_revision="test-revision")
    second = runtime_identity(execution_revision="test-revision")
    assert first == second
    assert first["execution_identity"] == EXECUTION_ID
    assert first["execution_revision"] != FIXED_REVISION
    assert first["runtime_digest"] != FIXED_RUNTIME_DIGEST
    assert first["judged_execution"] is False and first["production"] is False
    assert child_manifest()["child_count"] == 18


def test_minecraft_eac_identity_binds_frozen_semantic_artifacts():
    identity = runtime_identity(execution_revision="test-revision")
    assert identity["support_policy"]["digest"] == "ef34b67ef618ed4b34a9c2720d854e02d8fb6af917a0cbe472daef8cc5603d51"
    assert identity["eac_source_profile"]["digest"] == "0bdfd40f6cf7a0d2575498e00cf18020edf7bf9a1246217a5acbff6b948bdb01"
    assert identity["epre_classification"]["digest"] == "34f887d45280fe1e0cd61c7799d81ed208badf2b255d287dcb0777d2924b911a"


def test_eac_premanifest_admission_fails_closed(tmp_path):
    path = tmp_path / "premanifest.json"
    identity = runtime_identity(execution_revision="test-revision")
    path.write_text(json.dumps(identity), encoding="utf-8")
    assert verify_eac_premanifest(path, execution_revision="test-revision") == identity
    path.write_text(json.dumps({**identity, "runtime_digest": "sha256:" + "0" * 64}), encoding="utf-8")
    with pytest.raises(ValueError, match="identity mismatch"):
        verify_eac_premanifest(path, execution_revision="test-revision")


def test_committed_nonjudged_premanifest_is_exact_and_fixture_binds_it():
    premanifest_path = ROOT / "docs/eac/minecraft_eac_premanifest_v1.json"
    fixture = json.loads((ROOT / "docs/eac/minecraft_eac_nonjudged_fixture_v1.json").read_text())
    assert fixture["task_selection_policy"] == "dual_dag_authority"
    assert fixture["task_type"] == "none"
    assert fixture["eac_execution_revision"] == FROZEN_REVISION
    assert fixture["eac_premanifest"] == "docs/eac/minecraft_eac_premanifest_v1.json"
    identity = verify_eac_premanifest(premanifest_path, execution_revision=FROZEN_REVISION)
    assert identity["judged_execution"] is False
    assert identity["production"] is False
