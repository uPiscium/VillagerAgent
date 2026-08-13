"""Deterministic non-executing identity for the Minecraft EAC runtime v1."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from benchmarks.common.eac import load_support_policy
from benchmarks.common.eac.canonical import canonical_bytes
from benchmarks.common.eac.policy import bind_source_profile
from env.runtime_execution import RuntimeExecution

EXECUTION_ID = "minecraft-eac-runtime-v1"
_COMMIT = re.compile(r"[0-9a-f]{40}")


def resolve_git_revision(root: Path) -> str:
    """Return the immutable commit checked out at an exact runtime root."""
    root = root.resolve(strict=True)
    environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
        text=True, timeout=10, check=False, env=environment,
    )
    revision = result.stdout.strip()
    if result.returncode != 0 or _COMMIT.fullmatch(revision) is None:
        raise ValueError("Minecraft EAC execution root has no immutable Git revision")
    return revision


def runtime_identity(execution: RuntimeExecution, *, execution_revision: str) -> dict:
    execution.verify()
    if _COMMIT.fullmatch(execution_revision) is None:
        raise ValueError("Minecraft EAC execution revision must be a full Git commit")
    root = execution.root
    classification = json.loads((root / "docs/eac/minecraft_preconditions_v1.json").read_text())
    profile = bind_source_profile(json.loads((root / "docs/eac/minecraft_source_profile_v1.json").read_text()))
    policy = load_support_policy(root / "docs/eac/support_policy_v1.json")
    payload = {
        "execution_identity": EXECUTION_ID,
        "execution_revision": execution_revision,
        "child_manifest_sha256": execution.manifest_sha256,
        "child_count": execution.asset_count,
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


def verify_eac_premanifest(path: Path, *, execution: RuntimeExecution,
                           execution_revision: str) -> dict:
    if resolve_git_revision(execution.root) != execution_revision:
        raise ValueError("Minecraft EAC execution revision mismatch")
    value = json.loads(path.read_text(encoding="utf-8"))
    expected = runtime_identity(execution, execution_revision=execution_revision)
    if value != expected:
        raise ValueError("Minecraft EAC premanifest identity mismatch")
    return value
