"""Executable contract tests for the public EAC authority/gateway APIs."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from benchmarks.common.eac import (
    ActionRef,
    ActorScope,
    AuthorityError,
    EffectGateway,
    EffectRejected,
    EPreRef,
    EvidenceRoot,
    ExactRequest,
    PolicyRef,
    ProfileRef,
    NativeEffectResult,
    Proposition,
    PropositionKey,
    ProvenanceRecord,
    RejectionReason,
    RuntimeAuthority,
    bind_source_profile,
    load_support_policy,
)
from benchmarks.common.eac.canonical import FrozenJSONArray, FrozenJSONObject


def _profile():
    """Return a valid, deliberately small SourceProfile binding."""
    profile = {
        "profile_id": "test-profile",
        "profile_version": 1,
        "mapping_rules": [{
            "rule_id": "direct", "priority": 0, "record_namespace": "test",
            "record_type": "observation", "root_type": "direct_observation",
            "visibility_field": "visible_to", "source_lineage_field": "lineage",
            "upstream_origin_field": "origin", "trusted_tool_identity": None,
            "trusted_tool_version": None,
        }],
        "trusted_tools": [],
        "supersession_streams": [],
        "derivation_rules": [],
        "aging_rules": [],
        "integrity_contract": {
            "contract_id": "test-integrity", "contract_version": 1,
            "canonical_content_sha256": "0" * 64,
            "issuer_authentication_rule_id": "test-issuer",
            "rule_evaluation_contract_sha256": "0" * 64,
        },
        "fail_closed": True,
    }
    canonical = json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    profile["detached_profile_sha256"] = hashlib.sha256(canonical).hexdigest()
    return bind_source_profile(profile)


def _setup(*, mode="authority", nonce="fixed-authority-nonce", capability="build", reader=None):
    policy = load_support_policy()
    profile = _profile()
    authority = RuntimeAuthority(policy_binding=policy, profile_binding=profile, mode=mode,
                                 authority_nonce=nonce, state_reader=reader)
    key = PropositionKey("test", "ready", ("village",), "run")
    proposition = Proposition(key)
    action_definition = {"identity": "build", "semantics": "test"}
    action = ActionRef("build", 1, RuntimeAuthority._definition_digest(action_definition))
    epre = EPreRef("ready", 1, RuntimeAuthority._definition_digest((proposition,)))
    authority.register_action_definition(action, action_definition)
    authority.register_epre_definition(epre, (proposition,))
    request = ExactRequest("candidate", "attempt", action,
                           (("count", 1),), target=[1, 2, 3])
    actor = ActorScope("actor", 1, ("village",))
    if reader is None:
        authority.put_provenance(ProvenanceRecord("prov", "observation"))
        authority._put_classified_root(EvidenceRoot("root", "direct_observation", proposition, "sensor", 1,
                                        ("actor",), "prov", source_lineage_id="lineage",
                                        upstream_origin_id="origin", mapping_rule_id="direct"))
    authority.register_candidate(request, actor=actor, epre_ref=epre,
        epre=(proposition,),
        capability_dependencies=(capability,))
    return authority, request, proposition


def _permit():
    authority, request, proposition = _setup()
    decision = authority.evaluate(request.candidate_id)
    assert decision.admissible, decision.reasons
    return authority, request, proposition, authority.issue_permit(request.candidate_id)


def test_manifest_binds_all_authority_dependencies_and_conflict_watch():
    authority, request, _, permit = _permit()
    manifest = permit.manifest
    assert manifest is not None
    assert manifest.request == request
    assert manifest.action == request.action
    assert manifest.actor == ActorScope("actor", 1, ("village",))
    assert manifest.epre.identity == "ready" and manifest.epre.digest
    assert manifest.policy == PolicyRef(authority.policy.identity, authority.policy.version, authority.policy.digest)
    assert manifest.profile == ProfileRef(authority.profile.identity, authority.profile.version, authority.profile.digest)
    assert manifest.witness_ids and any(
        item.dependency_id.startswith("conflict:") for item in manifest.expectations
    )
    assert manifest.authority_nonce == "fixed-authority-nonce"
    assert permit.fingerprint == manifest.fingerprint


@pytest.mark.parametrize("dependency", [
    "scope:actor", "capability:build",
])
def test_bound_dependency_mutation_stales_permit(dependency):
    authority, _, _, permit = _permit()
    authority.mutate_dependencies((dependency,), reason="test mutation")
    assert authority.permit(permit.permit_id).lifecycle.value == "stale"


def test_evidence_and_conflict_watch_mutations_stale_only_affected_permit():
    authority, request, proposition, permit = _permit()
    opposite = EvidenceRoot("opposite", "direct_observation", replace(proposition, polarity=False),
                             "sensor", 2, ("actor",), "prov", source_lineage_id="lineage2",
                             upstream_origin_id="origin2", mapping_rule_id="direct")
    authority._put_classified_root(opposite)
    assert authority.permit(permit.permit_id).lifecycle.value == "stale"
    other = _setup()[0]
    other_request = ExactRequest("other", "other-attempt", request.action)
    empty_ref = EPreRef("empty", 1, RuntimeAuthority._definition_digest(()))
    other.register_epre_definition(empty_ref, ())
    other.register_candidate(other_request, actor=ActorScope("other", 1),
                             epre_ref=empty_ref, epre=())
    unrelated = other.issue_permit("other")
    authority.mutate_dependencies(("unrelated",), reason="no-op for this permit")
    assert unrelated.lifecycle.value == "issued"


def test_exact_request_substitution_and_replay_are_rejected():
    authority, request, _, permit = _permit()
    with pytest.raises(AuthorityError) as mismatch:
        authority.validate_and_consume(replace(request, target="different"), permit)
    assert mismatch.value.reason == RejectionReason.MISMATCH.value
    token = authority.validate_and_consume(request, permit)
    authority.admit_effect(token, request)
    authority.complete_effect(token, "succeeded")
    with pytest.raises(AuthorityError, match="replay"):
        authority.validate_and_consume(request, permit)


def test_concurrent_consume_is_exactly_once():
    authority, request, _, permit = _permit()
    def consume():
        try:
            return authority.validate_and_consume(request, permit)
        except AuthorityError as exc:
            return exc.reason
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: consume(), range(8)))
    assert sum(not isinstance(x, str) for x in results) == 1
    assert sum(x == RejectionReason.REPLAY.value for x in results) == 7


def test_mutation_between_consume_and_admit_is_rejected():
    authority, request, _, permit = _permit()
    token = authority.validate_and_consume(request, permit)
    authority.mutate_dependencies(("capability:build",), reason="race")
    with pytest.raises(AuthorityError, match="stale"):
        authority.admit_effect(token, request)


def test_disappearing_external_dependency_stales_consumed_permit():
    proposition = Proposition(PropositionKey("test", "ready", ("village",), "run"))

    class Reader:
        versions = None

        def evidence_snapshot(self, unused_actor):
            from benchmarks.common.eac.witness import EvidenceSnapshot
            from benchmarks.common.eac.authority import _proposition_slot
            versions = self.versions or (("evidence:external-root", 1), ("provenance:prov", 1),
                                         (_proposition_slot(proposition), 1), ("scope:actor", 1))
            return EvidenceSnapshot(
                (EvidenceRoot("external-root", "direct_observation", proposition, "sensor", 1,
                              ("actor",), "prov", mapping_rule_id="direct"),), (),
                (ProvenanceRecord("prov", "observation"),), versions,
                _profile().digest_sha256, True)

    reader = Reader()
    authority, request, _ = _setup(reader=reader)
    permit = authority.issue_permit(request.candidate_id)
    reader.versions = (("provenance:prov", 1),)
    with pytest.raises(AuthorityError, match="stale"):
        authority.validate_and_consume(request, permit)


def test_external_conflict_watch_rejects_new_defeater():
    proposition = Proposition(PropositionKey("test", "ready", ("village",), "run"))

    class Reader:
        watch_revision = 1

        def evidence_snapshot(self, unused_actor):
            from benchmarks.common.eac.authority import _proposition_slot
            from benchmarks.common.eac.witness import EvidenceSnapshot
            return EvidenceSnapshot(
                (EvidenceRoot("external-root", "direct_observation", proposition, "sensor", 1,
                              ("actor",), "prov", mapping_rule_id="direct"),), (),
                (ProvenanceRecord("prov", "observation"),),
                (("evidence:external-root", 1), ("provenance:prov", 1),
                 (_proposition_slot(proposition), self.watch_revision), ("scope:actor", 1)),
                _profile().digest_sha256, True)

    reader = Reader()
    authority, request, _ = _setup(reader=reader)
    permit = authority.issue_permit(request.candidate_id)
    reader.watch_revision = 2
    with pytest.raises(AuthorityError, match="stale"):
        authority.validate_and_consume(request, permit)


def test_unrelated_dependency_does_not_change_manifest_on_reevaluation():
    authority, request, _, permit = _permit()
    authority.mutate_dependencies(("unrelated",), reason="irrelevant")
    authority.evaluate(request.candidate_id)
    assert authority.permit(permit.permit_id).lifecycle.value == "issued"
    assert authority.permit(permit.permit_id).fingerprint == permit.fingerprint


def test_resolution_issues_new_identity_and_fingerprint():
    authority, request, _, first = _permit()
    authority.mutate_dependencies(("capability:build",), reason="resolve")
    second = authority.issue_permit(request.candidate_id)
    assert second.permit_id != first.permit_id
    assert second.fingerprint != first.fingerprint
    with pytest.raises(AuthorityError):
        authority.validate_and_consume(request, first.permit_id)


def test_consumed_attempt_cannot_be_reissued_or_reused():
    authority, request, _, permit = _permit()
    token = authority.validate_and_consume(request, permit)
    authority.reject_pre_effect(token, "env_pre")
    with pytest.raises(AuthorityError, match="new_attempt_identity_required"):
        authority.issue_permit(request.candidate_id)
    with pytest.raises(ValueError, match="attempt identity already registered"):
        empty_ref = EPreRef("empty", 1, RuntimeAuthority._definition_digest(()))
        authority.register_epre_definition(empty_ref, ())
        authority.register_candidate(
            ExactRequest("retry", request.attempt_id, request.action),
            actor=ActorScope("actor", 1), epre_ref=empty_ref, epre=())


def test_nested_request_values_are_deeply_immutable():
    target = [1, {"x": [2]}]
    request = ExactRequest("c", "a", ActionRef("act", 1, "a" * 64), target=target)
    target[1]["x"].append(3)
    assert request.target == FrozenJSONArray((1, FrozenJSONObject((("x", FrozenJSONArray((2,))),))))


@pytest.mark.parametrize("original,replacement", [(True, 1), (False, 0)])
def test_typed_scalar_request_substitution_is_rejected(original, replacement):
    authority, request, _, permit = _permit()
    typed = replace(request, target=original)
    authority = _setup()[0]
    # Register a fresh candidate whose permit is bound to the typed target.
    candidate = authority._candidates[request.candidate_id]
    del authority._candidates[request.candidate_id]
    typed = replace(candidate.request, target=original)
    authority._reserved_attempt_ids.remove(typed.attempt_id)
    authority.register_candidate(typed, actor=candidate.actor, epre_ref=candidate.epre_ref,
                                 epre=candidate.epre, capability_dependencies=candidate.capability_dependencies)
    permit = authority.issue_permit(typed.candidate_id)
    with pytest.raises(AuthorityError, match="mismatch"):
        authority.validate_and_consume(replace(typed, target=replacement), permit)


def test_object_and_array_requests_remain_distinct_but_object_order_is_canonical():
    action = ActionRef("act", 1, "a" * 64)
    object_request = ExactRequest("c", "a", action, target={"x": 1, "y": 2})
    reordered = ExactRequest("c", "a", action, target={"y": 2, "x": 1})
    array_request = ExactRequest("c", "a", action, target=[["x", 1], ["y", 2]])
    assert object_request == reordered
    assert object_request != array_request
    assert object_request.identity_bytes() != array_request.identity_bytes()


def test_direct_frozen_wrappers_deep_copy_and_reject_duplicate_object_keys():
    nested = [1]
    request = ExactRequest("c", "a", ActionRef("act", 1, "a" * 64),
                           target=FrozenJSONArray((nested,)))
    nested.append(2)
    assert request.target == FrozenJSONArray((FrozenJSONArray((1,)),))
    with pytest.raises(TypeError, match="unique"):
        FrozenJSONObject((("x", 1), ("x", 2)))


def test_binding_slots_do_not_collide_on_delimiter_like_identity_versions():
    from benchmarks.common.eac.authority import _binding_slot
    digest = "a" * 64
    assert _binding_slot("action", ActionRef("a:b", "c", digest)) != _binding_slot(
        "action", ActionRef("a", "b:c", digest))


def test_parallel_definition_version_does_not_stale_but_retirement_obsoletes_candidate():
    authority, request, proposition, permit = _permit()
    v2_definition = {"identity": "build", "semantics": "v2"}
    v2 = ActionRef("build", 2, RuntimeAuthority._definition_digest(v2_definition))
    authority.register_action_definition(v2, v2_definition)
    assert authority.permit(permit.permit_id).lifecycle.value == "issued"
    authority.retire_definition("action", request.action)
    assert authority.permit(permit.permit_id).lifecycle.value == "stale"
    with pytest.raises(AuthorityError, match="semantic_binding_retired"):
        authority.issue_permit(request.candidate_id)


def test_retiring_authority_policy_profile_obsoletes_old_candidate():
    authority, request, _, permit = _permit()
    authority.retire_authority_semantics()
    assert authority.permit(permit.permit_id).lifecycle.value == "stale"
    with pytest.raises(AuthorityError, match="semantic_binding_retired"):
        authority.issue_permit(request.candidate_id)


def test_external_manifest_excludes_unrelated_snapshot_dependencies():
    proposition = Proposition(PropositionKey("test", "ready", ("village",), "run"))
    unrelated = Proposition(PropositionKey("test", "other", ("village",), "run"))
    from benchmarks.common.eac.authority import _proposition_slot

    class Reader:
        unrelated_revision = 1

        def evidence_snapshot(self, unused_actor):
            from benchmarks.common.eac.witness import EvidenceSnapshot
            roots = (
                EvidenceRoot("wanted", "direct_observation", proposition, "sensor", 1,
                             ("actor",), "prov-wanted", mapping_rule_id="direct"),
                EvidenceRoot("unrelated", "direct_observation", unrelated, "sensor", 1,
                             ("actor",), "prov-unrelated", mapping_rule_id="direct"),
            )
            return EvidenceSnapshot(
                roots, (), (ProvenanceRecord("prov-wanted", "sensor"),
                            ProvenanceRecord("prov-unrelated", "sensor")),
                (("evidence:wanted", 1), ("provenance:prov-wanted", 1),
                 (_proposition_slot(proposition), 1),
                 ("scope:actor", 1),
                 ("evidence:unrelated", self.unrelated_revision),
                 ("provenance:prov-unrelated", self.unrelated_revision),
                 (_proposition_slot(unrelated), self.unrelated_revision)),
                _profile().digest_sha256, True)

    reader = Reader()
    authority, request, _ = _setup(reader=reader)
    permit = authority.issue_permit(request.candidate_id)
    dependencies = {item.dependency_id for item in permit.manifest.expectations}
    assert "evidence:wanted" in dependencies and "evidence:unrelated" not in dependencies
    reader.unrelated_revision = 2
    token = authority.validate_and_consume(request, permit)
    authority.reject_pre_effect(token, "env_pre")


def test_external_scope_revision_stales_permit():
    proposition = Proposition(PropositionKey("test", "ready", ("village",), "run"))
    from benchmarks.common.eac.authority import _proposition_slot

    class Reader:
        scope_revision = 1

        def evidence_snapshot(self, unused_actor):
            from benchmarks.common.eac.witness import EvidenceSnapshot
            return EvidenceSnapshot(
                (EvidenceRoot("root", "direct_observation", proposition, "sensor", 1,
                              ("actor",), "prov", mapping_rule_id="direct"),), (),
                (ProvenanceRecord("prov", "sensor"),),
                (("evidence:root", 1), ("provenance:prov", 1),
                 (_proposition_slot(proposition), 1), ("scope:actor", self.scope_revision)),
                _profile().digest_sha256, True)

    reader = Reader()
    authority, request, _ = _setup(reader=reader)
    permit = authority.issue_permit(request.candidate_id)
    reader.scope_revision = 2
    with pytest.raises(AuthorityError, match="stale"):
        authority.validate_and_consume(request, permit)


def test_record_ingestion_requires_profile_bound_source_authentication():
    policy, profile = load_support_policy(), _profile()
    record = {
        "namespace": "test", "type": "observation", "visible_to": ["actor"],
        "lineage": "sensor", "origin": "sensor", "issuer": "sensor-issuer",
    }
    proposition = Proposition(PropositionKey("test", "ready", ("village",), "run"))
    from benchmarks.common.eac.authority import _plain
    record["proposition"] = _plain(proposition)
    denied = RuntimeAuthority(policy_binding=policy, profile_binding=profile)
    with pytest.raises(ValueError, match="source authentication failed"):
        denied.ingest_record(record, proposition=proposition, root_id="root", revision=1,
                             provenance_id="prov")
    allowed = RuntimeAuthority(
        policy_binding=policy, profile_binding=profile,
        source_authenticator=lambda observed, bound_proposition, binding, rule: (
            observed["issuer"] == "sensor-issuer"
            and bound_proposition == proposition
            and binding.digest_sha256 == profile.digest_sha256
            and rule["rule_id"] == "direct"),
    )
    root = allowed.ingest_record(record, proposition=proposition, root_id="root", revision=1,
                                 provenance_id="prov")
    assert root.mapping_rule_id == "direct" and root.issuer == "sensor-issuer"


def test_failed_supersession_is_atomic_and_evidence_collections_are_copied():
    authority, _, proposition = _setup()
    supersedes = ["root", "missing"]
    bad = EvidenceRoot(
        "new", "direct_observation", proposition, "sensor", 2, ["actor"], "prov",
        supersedes, "lineage", "origin", True, True, "stream", 2, "issuer", "direct")
    with pytest.raises(ValueError, match="supersession"):
        authority._put_classified_root(bad)
    assert "new" not in authority._roots and authority._roots["root"].current is True
    supersedes.append("later")
    assert bad.supersedes == ("root", "missing") and bad.visible_to == ("actor",)


def test_ingestion_uses_profile_declared_supersession_revision_field():
    raw = dict(_profile().profile)
    raw = {key: (list(value) if isinstance(value, tuple) else dict(value) if hasattr(value, "items") else value)
           for key, value in raw.items() if key != "detached_profile_sha256"}
    raw["mapping_rules"] = [dict(item) for item in raw["mapping_rules"]]
    raw["trusted_tools"] = [dict(item) for item in raw["trusted_tools"]]
    raw["derivation_rules"] = [dict(item) for item in raw["derivation_rules"]]
    raw["aging_rules"] = [dict(item) for item in raw["aging_rules"]]
    raw["integrity_contract"] = dict(raw["integrity_contract"])
    raw["supersession_streams"] = [{
        "source_stream_id": "sensor-stream", "authorized_issuer": "sensor-issuer",
        "revision_field": "sequence", "tracked_proposition_rule_id": "direct",
    }]
    from benchmarks.common.eac.canonical import canonical_bytes
    raw["detached_profile_sha256"] = hashlib.sha256(canonical_bytes(raw)).hexdigest()
    profile = bind_source_profile(raw)
    proposition = Proposition(PropositionKey("test", "ready", ("village",), "run"))
    from benchmarks.common.eac.authority import _plain
    base = {
        "namespace": "test", "type": "observation", "visible_to": ["actor"],
        "lineage": "sensor", "origin": "sensor", "issuer": "sensor-issuer",
        "source_stream_id": "sensor-stream", "proposition": _plain(proposition),
    }
    authority = RuntimeAuthority(
        policy_binding=load_support_policy(), profile_binding=profile,
        source_authenticator=lambda *unused: True)
    authority.ingest_record(dict(base, sequence=1), proposition=proposition, root_id="old",
                            revision=1, provenance_id="prov")
    new = authority.ingest_record(dict(base, sequence=2), proposition=proposition, root_id="new",
                                  revision=2, provenance_id="prov", supersedes=("old",))
    assert new.source_stream_revision == 2 and authority._roots["old"].current is False


def test_gateway_requires_permit_and_env_sec_prechecks_consume_independently():
    authority, request, _, permit = _permit()
    calls = []
    gateway = EffectGateway(authority, lambda _: "ok", env_pre=lambda _: False,
                            sec_pre=lambda _: calls.append("sec") or False)
    with pytest.raises(EffectRejected, match="missing_permit"):
        gateway.execute(request)
    with pytest.raises(EffectRejected, match="precheck_rejected"):
        gateway.execute(request, permit)
    assert calls == ["sec"]
    attempt = authority.attempt_snapshot()[0]
    assert (attempt.env_pre_result, attempt.sec_pre_result) == ("failed", "failed")


@pytest.mark.parametrize("identity,version", [(True, 1), ("act", {}), ("", 1), ("act", True)])
def test_versioned_refs_reject_malformed_typed_identity(identity, version):
    with pytest.raises(ValueError):
        ActionRef(identity, version, "a" * 64)


def test_precheck_exception_terminates_consumed_attempt():
    authority, request, _, permit = _permit()
    gateway = EffectGateway(authority, lambda _: "unreachable",
                            env_pre=lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        gateway.execute(request, permit)
    assert authority.attempt_snapshot()[0].outcome == "pre_effect_rejected"
    authority, request, _, permit = _permit()
    gateway = EffectGateway(authority, lambda _: "ok", sec_pre=lambda _: False)
    with pytest.raises(EffectRejected, match="precheck_rejected"):
        gateway.execute(request, permit)


def test_native_reentrant_mutation_is_blocked_and_explicit_failure_is_recorded():
    authority, request, _, permit = _permit()
    observed = []

    def native(_):
        with pytest.raises(AuthorityError, match="mutation_during_effect"):
            authority.mutate_dependencies(("capability:build",), reason="reentrant")
        observed.append("called")
        return NativeEffectResult("not-built", "effect_failed")

    assert EffectGateway(authority, native).execute(request, permit) == "not-built"
    assert observed == ["called"]
    assert authority.attempt_snapshot()[0].outcome == "effect_failed"


def test_advisory_and_authority_share_pre_gate_manifest_and_advisory_logs_would_block():
    advisory, request, _ = _setup(mode="advisory")
    shadow = advisory.shadow_permit(request.candidate_id)
    authority, request2, _ = _setup(nonce="fixed-authority-nonce")
    permit = authority.issue_permit(request2.candidate_id)
    assert shadow.manifest == permit.manifest
    assert shadow.fingerprint == permit.fingerprint
    gateway = EffectGateway(advisory, lambda _: "ok")
    gateway.execute_advisory(request)
    assert any(record.event == "advisory_only" and dict(record.details)["would_block"] is False
               for record in advisory.audit_snapshot())
    attempt = advisory.attempt_snapshot()[0]
    assert attempt.enforcement == "advisory_bypass"
    assert attempt.request_digest and attempt.manifest_fingerprint == shadow.fingerprint
    assert (attempt.env_pre_result, attempt.sec_pre_result) == ("passed", "passed")


def test_advisory_records_env_and_sec_prechecks_independently():
    advisory, request, _ = _setup(mode="advisory")
    gateway = EffectGateway(advisory, lambda _: "unreachable",
                            env_pre=lambda _: False, sec_pre=lambda _: False)
    with pytest.raises(EffectRejected, match="precheck_rejected"):
        gateway.execute_advisory(request)
    attempt = advisory.attempt_snapshot()[0]
    assert (attempt.env_pre_result, attempt.sec_pre_result, attempt.outcome) == (
        "failed", "failed", "pre_effect_rejected")


def test_advisory_rechecks_shadow_freshness_after_prechecks():
    advisory, request, _ = _setup(mode="advisory")
    gateway = EffectGateway(
        advisory, lambda _: "ok",
        env_pre=lambda _: (advisory.mutate_dependencies(("capability:build",), reason="precheck") or True),
    )
    assert gateway.execute_advisory(request) == "ok"
    assert advisory.attempt_snapshot()[0].would_block is True


def test_base_exception_terminalizes_authority_attempt():
    authority, request, _, permit = _permit()
    gateway = EffectGateway(authority, lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        gateway.execute(request, permit)
    assert authority.attempt_snapshot()[0].outcome == "effect_unknown"


def test_audit_is_bounded_read_only_and_limit_checked():
    authority, _, _, _ = _permit()
    records = authority.audit_snapshot(limit=256)
    assert len(records) <= 256
    with pytest.raises(ValueError):
        authority.audit_snapshot(limit=257)
    assert authority.audit_snapshot(limit=0) == ()
