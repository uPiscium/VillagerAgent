"""Fail-closed loading and binding of the EAC v1 policy dependencies.

This module deliberately contains no policy evaluation.  The policy and source
profile identities returned here are explicit inputs to that evaluation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .canonical import canonical_bytes
from .model import PolicyRef, ProfileRef

POLICY_ID = "eac-primary-support"
POLICY_VERSION = 1
POLICY_SHA256 = "ef34b67ef618ed4b34a9c2720d854e02d8fb6af917a0cbe472daef8cc5603d51"


def _canonical(value: Any) -> bytes:
    return canonical_bytes(value)


def _json(path: Path) -> Mapping[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    if not isinstance(value, Mapping):
        raise ValueError("policy must be a JSON object")
    return value


@dataclass(frozen=True)
class PolicyBinding:
    policy_id: str
    policy_version: int
    digest_sha256: str
    policy: Mapping[str, Any]


@dataclass(frozen=True)
class SourceProfileBinding:
    profile_id: str
    profile_version: int
    digest_sha256: str
    profile: Mapping[str, Any]


def load_support_policy(path: str | Path | None = None) -> PolicyBinding:
    """Authenticate the checked-in SupportPolicy v1, rather than hiding it in code."""
    artifact = Path(path) if path is not None else Path(__file__).resolve().parents[3] / "docs/eac/support_policy_v1.json"
    policy = _json(artifact)
    import hashlib
    digest = hashlib.sha256(_canonical(policy)).hexdigest()
    if (policy.get("policy_id"), policy.get("policy_version"), digest) != (POLICY_ID, POLICY_VERSION, POLICY_SHA256):
        raise ValueError("SupportPolicy identity or digest is not the frozen v1 artifact")
    return PolicyBinding(POLICY_ID, POLICY_VERSION, digest, _freeze(policy))


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _sha(value: Any, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _required_map(item: Mapping[str, Any], fields: tuple[str, ...], label: str) -> None:
    for field in fields:
        if field not in item:
            raise ValueError(f"{label} missing {field}")


def bind_source_profile(profile: Mapping[str, Any], policy: PolicyBinding | None = None) -> SourceProfileBinding:
    """Validate and authenticate a SourceProfile mapping before any record is used."""
    if not isinstance(profile, Mapping) or profile.get("fail_closed") is not True:
        raise ValueError("SourceProfile must be a mapping with fail_closed=true")
    _required_map(profile, ("profile_id", "profile_version", "detached_profile_sha256",
                            "mapping_rules", "trusted_tools", "supersession_streams",
                            "derivation_rules", "aging_rules", "integrity_contract", "fail_closed"), "profile")
    if set(profile) != {"profile_id", "profile_version", "detached_profile_sha256", "mapping_rules",
                        "trusted_tools", "supersession_streams", "derivation_rules", "aging_rules",
                        "integrity_contract", "fail_closed"}:
        raise ValueError("SourceProfile contains unknown fields")
    profile_id = _text(profile["profile_id"], "profile_id")
    version = profile["profile_version"]
    if type(version) is not int or version < 1:
        raise ValueError("profile_version must be a positive integer")
    declared = _sha(profile["detached_profile_sha256"], "detached_profile_sha256")
    detached = dict(profile)
    del detached["detached_profile_sha256"]
    import hashlib
    digest = hashlib.sha256(_canonical(detached)).hexdigest()
    if digest != declared:
        raise ValueError("SourceProfile detached digest mismatch")

    tools: dict[tuple[str, str], Mapping[str, Any]] = {}
    integrity = profile["integrity_contract"]
    if not isinstance(integrity, Mapping) or set(integrity) != {
        "contract_id", "contract_version", "canonical_content_sha256",
        "issuer_authentication_rule_id", "rule_evaluation_contract_sha256",
    }:
        raise ValueError("integrity contract does not match SourceProfile v1")
    _text(integrity["contract_id"], "contract_id")
    if type(integrity["contract_version"]) is not int or integrity["contract_version"] < 1:
        raise ValueError("integrity contract version is invalid")
    _sha(integrity["canonical_content_sha256"], "canonical_content_sha256")
    _text(integrity["issuer_authentication_rule_id"], "issuer_authentication_rule_id")
    _sha(integrity["rule_evaluation_contract_sha256"], "rule_evaluation_contract_sha256")
    for tool in profile["trusted_tools"]:
        if not isinstance(tool, Mapping):
            raise ValueError("trusted tool must be an object")
        _required_map(tool, ("tool_identity", "tool_version", "allowed_proposition_namespaces", "integrity_contract_sha256"), "trusted tool")
        if set(tool) != {"tool_identity", "tool_version", "allowed_proposition_namespaces", "integrity_contract_sha256"}:
            raise ValueError("trusted tool contains unknown fields")
        key = (_text(tool["tool_identity"], "tool_identity"), _text(tool["tool_version"], "tool_version"))
        _sha(tool["integrity_contract_sha256"], "integrity_contract_sha256")
        namespaces = tool["allowed_proposition_namespaces"]
        if not isinstance(namespaces, list) or not namespaces or any(not isinstance(x, str) or not x for x in namespaces):
            raise ValueError("trusted tool namespaces are invalid")
        if key in tools:
            raise ValueError("duplicate trusted tool")
        tools[key] = tool

    streams: dict[str, Mapping[str, Any]] = {}
    for stream in profile["supersession_streams"]:
        if not isinstance(stream, Mapping):
            raise ValueError("supersession stream must be an object")
        _required_map(stream, ("source_stream_id", "authorized_issuer", "revision_field", "tracked_proposition_rule_id"), "stream")
        if set(stream) != {"source_stream_id", "authorized_issuer", "revision_field", "tracked_proposition_rule_id"}:
            raise ValueError("supersession stream contains unknown fields")
        sid = _text(stream["source_stream_id"], "source_stream_id")
        _text(stream["authorized_issuer"], "authorized_issuer")
        _text(stream["revision_field"], "revision_field")
        _text(stream["tracked_proposition_rule_id"], "tracked_proposition_rule_id")
        if sid in streams:
            raise ValueError("duplicate supersession stream")
        streams[sid] = stream

    mappings = profile["mapping_rules"]
    if not isinstance(mappings, list):
        raise ValueError("mapping_rules must be a list")
    for rule in mappings:
        if not isinstance(rule, Mapping):
            raise ValueError("mapping rule must be an object")
        fields = ("rule_id", "priority", "record_namespace", "record_type", "root_type",
                  "visibility_field", "source_lineage_field", "upstream_origin_field",
                  "trusted_tool_identity", "trusted_tool_version")
        _required_map(rule, fields, "mapping rule")
        if set(rule) != set(fields):
            raise ValueError("mapping rule contains unknown fields")
        if type(rule["priority"]) is not int or rule["priority"] < 0:
            raise ValueError("mapping priority is invalid")
        _text(rule["record_namespace"], "record_namespace")
        _text(rule["record_type"], "record_type")
        if rule["root_type"] not in {"direct_observation", "trusted_tool_result", "visible_action_outcome", "unverified_peer_report"}:
            raise ValueError("unknown mapping root type")
        for field in ("visibility_field", "source_lineage_field", "upstream_origin_field"):
            _text(rule[field], field)
        tool_id, tool_version = rule["trusted_tool_identity"], rule["trusted_tool_version"]
        if rule["root_type"] == "trusted_tool_result":
            key = (_text(tool_id, "trusted_tool_identity"), _text(tool_version, "trusted_tool_version"))
            if key not in tools or rule["record_namespace"] not in tools[key]["allowed_proposition_namespaces"]:
                raise ValueError("trusted tool binding is not declared")
        elif tool_id is not None or tool_version is not None:
            raise ValueError("non-tool mapping must have null tool binding")
    for label in ("derivation_rules", "aging_rules"):
        values = profile[label]
        if not isinstance(values, list):
            raise ValueError(f"{label} must be a list")
        seen = set()
        required = (("rule_id", "rule_version", "canonical_content_sha256") if label == "derivation_rules"
                    else ("rule_id", "rule_version", "canonical_content_sha256", "authorized_issuer", "event_type", "affected_scope_rule_id"))
        for rule in values:
            if not isinstance(rule, Mapping) or set(rule) != set(required):
                raise ValueError(f"{label} rule does not match SourceProfile v1")
            _text(rule["rule_id"], "rule_id")
            if type(rule["rule_version"]) is not int or rule["rule_version"] < 1:
                raise ValueError("rule version is invalid")
            _sha(rule["canonical_content_sha256"], "canonical_content_sha256")
            if rule["rule_id"] in seen:
                raise ValueError("duplicate profile rule")
            seen.add(rule["rule_id"])
            for field in required[3:]:
                _text(rule[field], field)
    return SourceProfileBinding(profile_id, version, digest, _freeze(profile))


def match_mapping(record: Mapping[str, Any], binding: SourceProfileBinding) -> Mapping[str, Any]:
    """Return the sole lowest-priority exact mapping, or fail closed."""
    namespace = record.get("namespace", record.get("record_namespace"))
    kind = record.get("type", record.get("record_type"))
    matches = [r for r in binding.profile["mapping_rules"] if r["record_namespace"] == namespace and r["record_type"] == kind]
    if not matches or sum(r["priority"] == min(x["priority"] for x in matches) for r in matches) != 1:
        raise ValueError("record mapping is ambiguous or absent")
    rule = min(matches, key=lambda x: x["priority"])
    for field in (rule["visibility_field"], rule["source_lineage_field"], rule["upstream_origin_field"]):
        if field not in record or record[field] in (None, ""):
            raise ValueError("record lacks required visibility or lineage data")
    stream_id = record.get("source_stream_id")
    if stream_id is not None:
        streams = binding.profile["supersession_streams"]
        stream = next((s for s in streams if s["source_stream_id"] == stream_id), None)
        if stream is None:
            raise ValueError("record names an unknown source stream")
        issuer = record.get("issuer", record.get("authorized_issuer"))
        if issuer != stream["authorized_issuer"]:
            raise ValueError("source stream issuer is not authorized")
        revision_field = stream["revision_field"]
        revision = record.get(revision_field)
        if type(revision) is not int or revision < 0:
            raise ValueError("source stream revision is invalid")
        if stream["tracked_proposition_rule_id"] != rule["rule_id"]:
            raise ValueError("source stream does not authorize this proposition mapping")
    if rule["root_type"] == "trusted_tool_result":
        key = (rule["trusted_tool_identity"], rule["trusted_tool_version"])
        tool = next((item for item in binding.profile["trusted_tools"]
                     if (item["tool_identity"], item["tool_version"]) == key), None)
        if (tool is None or record.get("tool_identity") != key[0]
                or record.get("tool_version") != key[1]
                or record.get("integrity_contract_sha256") != tool["integrity_contract_sha256"]):
            raise ValueError("trusted tool record identity or integrity mismatch")
    return rule


load_policy = load_support_policy
validate_source_profile = bind_source_profile


def _freeze(value):
    from types import MappingProxyType
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def policy_ref(binding: PolicyBinding) -> PolicyRef:
    return PolicyRef(binding.policy_id, binding.policy_version, binding.digest_sha256)


def profile_ref(binding: SourceProfileBinding) -> ProfileRef:
    return ProfileRef(binding.profile_id, binding.profile_version, binding.digest_sha256)
