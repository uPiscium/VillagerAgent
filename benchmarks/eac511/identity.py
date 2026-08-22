"""Canonical identities for the benchmark protocol, never the frozen runtime."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping

from benchmarks.common.eac.canonical import canonical_bytes

SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True, slots=True)
class Frozen510Identity:
    support_policy_identity: str = "eac-primary-support"
    support_policy_version: int = 1
    support_policy_digest: str = "ef34b67ef618ed4b34a9c2720d854e02d8fb6af917a0cbe472daef8cc5603d51"
    source_profile_digest: str = "01f65a8fd4bb68b1631e81d3c8d50f073747b5179995eeb60be3a55fdb6979be"
    classification_digest: str = "7c8bf97b80c96f1d05e8250cb9d89bb21b35c073f49979501090d72f13b56001"
    execution_revision: str = "6879ee7175619d01125c1a3374b41cc5da2b954e"
    runtime_manifest_digest: str = "6f1fa2b601839c7240ba4a389ece3e65bcc0e980e151d7912a5f65074088ef7d"
    premanifest_identity: str = "a98084095093095ef495e1be85d04b94f95b919ddbb057e034f229ccd4d61317"

    def __post_init__(self) -> None:
        if not COMMIT_RE.fullmatch(self.execution_revision):
            raise ValueError("frozen execution revision must be an immutable commit")
        for value in (self.support_policy_digest, self.source_profile_digest,
                      self.classification_digest, self.runtime_manifest_digest,
                      self.premanifest_identity):
            if not SHA256_RE.fullmatch(value):
                raise ValueError("invalid frozen SHA-256 identity")

    def as_dict(self) -> dict[str, Any]:
        return {
            "classification_digest": self.classification_digest,
            "execution_revision": self.execution_revision,
            "premanifest_identity": self.premanifest_identity,
            "runtime_manifest_digest": self.runtime_manifest_digest,
            "source_profile_digest": self.source_profile_digest,
            "support_policy": {
                "digest": self.support_policy_digest,
                "identity": self.support_policy_identity,
                "version": self.support_policy_version,
            },
        }


FROZEN_510 = Frozen510Identity()


def detached_digest(value: Any, field: str = "detached_artifact_sha256") -> str:
    """Hash canonical JSON after removing exactly one top-level detached field."""
    copied = deepcopy(value)
    if isinstance(copied, dict):
        copied.pop(field, None)
    return hashlib.sha256(canonical_bytes(copied)).hexdigest()


def verify_detached(value: Mapping[str, Any], field: str = "detached_artifact_sha256") -> str:
    declared = value.get(field)
    if not isinstance(declared, str) or not SHA256_RE.fullmatch(declared):
        raise ValueError(f"{field} must be a lowercase SHA-256")
    observed = detached_digest(value, field)
    if declared != observed:
        raise ValueError(f"{field} mismatch")
    return observed


def semantic_digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def canonical_equal(left: Any, right: Any) -> bool:
    return canonical_bytes(left) == canonical_bytes(right)


def verify_frozen_runtime_inputs(root: str | None = None) -> dict[str, Any]:
    """Authenticate the local #510 runtime closure and frozen EAC artifacts."""
    import json
    from pathlib import Path

    from benchmarks.common.eac import bind_source_profile, load_support_policy
    from env.runtime_execution import RuntimeExecution

    repository = Path(__file__).resolve().parents[2] if root is None else Path(root)
    execution = RuntimeExecution.resolve(repository)
    if execution.asset_count != 153 or execution.manifest_sha256 != FROZEN_510.runtime_manifest_digest:
        raise ValueError("frozen RuntimeExecution closure mismatch")
    policy = load_support_policy(repository / "docs/eac/support_policy_v1.json")
    profile = bind_source_profile(json.loads(
        (repository / "docs/eac/minecraft_source_profile_v1.json").read_text(encoding="utf-8")))
    classification = json.loads(
        (repository / "docs/eac/minecraft_preconditions_v1.json").read_text(encoding="utf-8"))
    premanifest = json.loads(
        (repository / "docs/eac/minecraft_eac_premanifest_v1.json").read_text(encoding="utf-8"))
    payload = {
        "execution_identity": "minecraft-eac-runtime-v1",
        "execution_revision": FROZEN_510.execution_revision,
        "child_manifest_sha256": execution.manifest_sha256,
        "child_count": execution.asset_count,
        "eac_source_profile": {"identity": profile.profile_id,
                               "version": profile.profile_version,
                               "digest": profile.digest_sha256},
        "epre_classification": {"identity": classification["artifact_id"],
                                "version": classification["artifact_version"],
                                "digest": classification["detached_artifact_sha256"]},
        "support_policy": {"identity": policy.policy_id,
                           "version": policy.policy_version,
                           "digest": policy.digest_sha256},
        "artifact_identity": "minecraft-eac-audit/1",
    }
    runtime_digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    expected_without_identity = {
        **payload, "runtime_digest": "sha256:" + runtime_digest,
        "judged_execution": False, "production": False,
    }
    expected_premanifest = {
        **expected_without_identity,
        "premanifest_identity": hashlib.sha256(
            canonical_bytes(expected_without_identity)).hexdigest(),
    }
    if premanifest != expected_premanifest:
        raise ValueError("frozen #510 premanifest bytes mismatch")
    observed = {
        "classification_digest": classification.get("detached_artifact_sha256"),
        "premanifest_identity": premanifest.get("premanifest_identity"),
        "runtime_manifest_digest": execution.manifest_sha256,
        "source_profile_digest": profile.digest_sha256,
        "support_policy_digest": policy.digest_sha256,
    }
    expected = {
        "classification_digest": FROZEN_510.classification_digest,
        "premanifest_identity": FROZEN_510.premanifest_identity,
        "runtime_manifest_digest": FROZEN_510.runtime_manifest_digest,
        "source_profile_digest": FROZEN_510.source_profile_digest,
        "support_policy_digest": FROZEN_510.support_policy_digest,
    }
    if observed != expected or premanifest.get("execution_revision") != FROZEN_510.execution_revision:
        raise ValueError("frozen #510 artifact identity mismatch")
    return observed
