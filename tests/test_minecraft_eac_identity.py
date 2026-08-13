import json
import io
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pytest

from benchmarks.minecraft.eac_identity import (
    EXECUTION_ID, resolve_git_revision, runtime_identity, verify_eac_premanifest,
)
from benchmarks.minecraft.gate_a_v4_owned_execution import FIXED_REVISION, FIXED_RUNTIME_DIGEST
from env.runtime_execution import RuntimeExecution

ROOT = Path(__file__).resolve().parents[1]


def test_minecraft_eac_identity_uses_runtime_execution_closure():
    execution = RuntimeExecution.resolve(ROOT)
    revision = resolve_git_revision(ROOT)
    first = runtime_identity(execution, execution_revision=revision)
    second = runtime_identity(execution, execution_revision=revision)
    assert first == second
    assert first["execution_identity"] == EXECUTION_ID
    assert first["execution_revision"] != FIXED_REVISION
    assert first["runtime_digest"] != FIXED_RUNTIME_DIGEST
    assert first["judged_execution"] is False and first["production"] is False
    assert first["child_count"] == execution.asset_count
    assert first["child_manifest_sha256"] == execution.manifest_sha256
    runtime_paths = {asset.relative_path for asset in execution.assets.values()}
    for path in ("docs/eac/support_policy_v1.json", "docs/eac/minecraft_preconditions_v1.json",
                 "docs/eac/minecraft_source_profile_v1.json", "docs/eac/minecraft_ingestion_contract_v1.json"):
        assert path in runtime_paths
    assert all("premanifest" not in asset.relative_path for asset in execution.assets.values())


def test_minecraft_eac_identity_binds_semantic_artifacts():
    identity = runtime_identity(RuntimeExecution.resolve(ROOT), execution_revision=resolve_git_revision(ROOT))
    assert identity["support_policy"]["digest"] == "ef34b67ef618ed4b34a9c2720d854e02d8fb6af917a0cbe472daef8cc5603d51"
    assert len(identity["eac_source_profile"]["digest"]) == 64
    assert len(identity["epre_classification"]["digest"]) == 64


def test_eac_premanifest_admission_fails_closed(tmp_path):
    execution = RuntimeExecution.resolve(ROOT)
    revision = resolve_git_revision(ROOT)
    path = tmp_path / "premanifest.json"
    identity = runtime_identity(execution, execution_revision=revision)
    path.write_text(json.dumps(identity), encoding="utf-8")
    assert verify_eac_premanifest(path, execution=execution, execution_revision=revision) == identity
    path.write_text(json.dumps({**identity, "runtime_digest": "sha256:" + "0" * 64}), encoding="utf-8")
    with pytest.raises(ValueError, match="identity mismatch"):
        verify_eac_premanifest(path, execution=execution, execution_revision=revision)


def test_symbolic_execution_revision_is_rejected():
    with pytest.raises(ValueError, match="full Git commit"):
        runtime_identity(RuntimeExecution.resolve(ROOT), execution_revision="issue-510-minecraft-eac-v1")


def test_committed_premanifest_recomputes_from_frozen_git_execution():
    premanifest = json.loads((ROOT / "docs/eac/minecraft_eac_premanifest_v1.json").read_text())
    revision = premanifest["execution_revision"]
    with tempfile.TemporaryDirectory(prefix="issue510-eac-frozen-") as temporary:
        archive = subprocess.run(
            ["git", "archive", "--format=tar", revision], cwd=ROOT,
            check=True, capture_output=True,
        ).stdout
        checkout = Path(temporary) / "checkout"
        checkout.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
            stream.extractall(checkout, filter="data")
        execution = RuntimeExecution.resolve(checkout)
        assert runtime_identity(execution, execution_revision=revision) == premanifest


def test_authority_and_advisory_fixtures_bind_same_execution_identity():
    authority = json.loads((ROOT / "docs/eac/minecraft_eac_nonjudged_fixture_v1.json").read_text())
    advisory = json.loads((ROOT / "docs/eac/minecraft_eac_nonjudged_advisory_fixture_v1.json").read_text())
    assert authority["task_selection_policy"] == "dual_dag_authority"
    assert advisory["task_selection_policy"] == "dual_dag_advisory"
    assert authority["eac_execution_revision"] == advisory["eac_execution_revision"]
    assert authority["eac_premanifest"] == advisory["eac_premanifest"]
