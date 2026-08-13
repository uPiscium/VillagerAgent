"""Deterministic non-executing identity for the Minecraft EAC runtime v1."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from benchmarks.common.eac import load_support_policy
from benchmarks.common.eac.canonical import canonical_bytes
from benchmarks.common.eac.policy import bind_source_profile

ROOT = Path(__file__).resolve().parents[2]
EXECUTION_ID = "minecraft-eac-runtime-v1"
FROZEN_REVISION = "issue-510-minecraft-eac-v1"
FROZEN_PREMANIFEST = ROOT / "docs/eac/minecraft_eac_premanifest_v1.json"
IDENTITY_ASSETS = (
    "benchmarks/common/eac/authority.py",
    "benchmarks/common/eac/__init__.py",
    "benchmarks/common/eac/canonical.py",
    "benchmarks/common/eac/gateway.py",
    "benchmarks/common/eac/model.py",
    "benchmarks/common/eac/policy.py",
    "benchmarks/common/eac/witness.py",
    "benchmarks/minecraft/experiment.py",
    "benchmarks/minecraft/eac_runtime.py",
    "env/env.py",
    "env/env_api.py",
    "env/minecraft_client.py",
    "env/minecraft_server_fast.py",
    "env/runtime_paths.py",
    "pipeline/controller_tiny.py",
    "start_with_config.py",
    "docs/eac/minecraft_preconditions_v1.json",
    "docs/eac/minecraft_source_profile_v1.json",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def child_manifest(root: Path = ROOT) -> dict:
    assets = [{"path": item, "sha256": _sha(root / item)} for item in IDENTITY_ASSETS]
    payload = {"identity": EXECUTION_ID, "child_count": len(assets), "children": assets}
    return {**payload, "child_manifest_sha256": hashlib.sha256(canonical_bytes(payload)).hexdigest()}


def runtime_identity(root: Path = ROOT, *, execution_revision: str) -> dict:
    manifest = child_manifest(root)
    classification = json.loads((root / "docs/eac/minecraft_preconditions_v1.json").read_text())
    profile = bind_source_profile(json.loads((root / "docs/eac/minecraft_source_profile_v1.json").read_text()))
    policy = load_support_policy(root / "docs/eac/support_policy_v1.json")
    payload = {
        "execution_identity": EXECUTION_ID,
        "execution_revision": execution_revision,
        "child_manifest_sha256": manifest["child_manifest_sha256"],
        "child_count": manifest["child_count"],
        "eac_source_profile": {"identity": profile.profile_id, "version": profile.profile_version,
                               "digest": profile.digest_sha256},
        "epre_classification": {"identity": classification["artifact_id"],
                                "version": classification["artifact_version"],
                                "digest": classification["detached_artifact_sha256"]},
        "support_policy": {"identity": policy.policy_id, "version": policy.policy_version,
                           "digest": policy.digest_sha256},
        "artifact_identity": "minecraft-eac-audit/1",
    }
    runtime_digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    premanifest = {**payload, "runtime_digest": "sha256:" + runtime_digest,
                   "judged_execution": False, "production": False}
    return {**premanifest,
            "premanifest_identity": hashlib.sha256(canonical_bytes(premanifest)).hexdigest()}


def verify_eac_premanifest(path: Path, *, execution_revision: str, root: Path = ROOT) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    expected = runtime_identity(root, execution_revision=execution_revision)
    if value != expected:
        raise ValueError("Minecraft EAC premanifest identity mismatch")
    return value
