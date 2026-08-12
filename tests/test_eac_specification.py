import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
POLICY = ROOT / "docs/eac/support_policy_v1.json"
SCHEMA = ROOT / "docs/eac/support_policy_schema_v1.json"


def _load_no_duplicates(path):
    def object_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate key: {key}")
            value[key] = item
        return value

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=object_pairs)


def _canonical_subset(value):
    """JCS-compatible for this integer/string/bool/null policy artifact."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def test_frozen_support_policy_identity_and_digest():
    policy = _load_no_duplicates(POLICY)
    assert (policy["policy_id"], policy["policy_version"]) == ("eac-primary-support", 1)
    assert hashlib.sha256(_canonical_subset(policy)).hexdigest() == "ef34b67ef618ed4b34a9c2720d854e02d8fb6af917a0cbe472daef8cc5603d51"


def test_support_policy_has_exact_primary_roots_and_mode_constraints():
    policy = _load_no_duplicates(POLICY)
    assert set(policy["support_semantics"]["sufficient_roots"]) == {
        "direct_observation", "trusted_tool_result", "visible_action_outcome",
    }
    constraints = policy["confirmatory_use_constraints"]
    assert constraints["immutable"] is True
    assert constraints["scenario_specific_change"] == "forbidden"
    assert constraints["post_outcome_tuning"] == "forbidden"
    assert constraints["advisory_authority_policy_difference"] == "forbidden"


def test_dynamic_fluent_identity_excludes_evidence_time_and_revision():
    identity = _load_no_duplicates(POLICY)["proposition_identity"]
    assert identity["required_fields"] == [
        "namespace", "predicate", "arguments", "temporal_scope", "polarity",
    ]
    stable_rule = identity["stable_tracked_proposition"]
    assert "observation time" in stable_rule
    assert "source_stream_revision" in stable_rule
    assert "evidence_revision" in stable_rule
    assert "never part of this key" in stable_rule
    assert "never observation time" in identity["temporal_scope_rule"]


def test_dynamic_fluent_supersession_oracles_are_frozen_in_spec():
    specification = (ROOT / "docs/eac/eac_semantics_v1.md").read_text(encoding="utf-8")
    assert "old evidence superseded; old witness invalid; dependent permit stale/revoked" in specification
    assert "old proposition not superseded; unrelated permit remains valid" in specification
    assert "stable tracked proposition identity is unchanged" in specification
    assert "#507 owned run envelope / supervision" in specification
    assert "-> EAC execution-permit gate" in specification


def test_support_policy_schema_is_strict_and_matches_identity():
    schema = _load_no_duplicates(SCHEMA)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["policy_id"]["const"] == "eac-primary-support"
    assert schema["properties"]["policy_version"]["const"] == 1
    assert schema["properties"]["source_profile_contract"]["properties"]["identity_fields"]["const"] == [
        "profile_id", "profile_version", "detached_profile_sha256",
    ]
    roots = schema["properties"]["support_semantics"]["properties"]["sufficient_roots"]
    assert roots["additionalProperties"] is False
    assert set(roots["required"]) == {
        "direct_observation", "trusted_tool_result", "visible_action_outcome",
    }


def test_source_profile_schema_is_strict_and_fail_closed():
    schema = _load_no_duplicates(ROOT / "docs/eac/source_profile_schema_v1.json")
    assert schema["additionalProperties"] is False
    assert "mapping_rules" in schema["required"]
    assert schema["properties"]["fail_closed"]["const"] is True
