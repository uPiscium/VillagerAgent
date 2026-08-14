"""Minecraft adapters for the shared EAC Runtime Authority.

This module owns no witness or permit semantics. It classifies Minecraft tool
calls and actor-visible records, then delegates those semantics to
``benchmarks.common.eac``.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from collections import deque
from dataclasses import dataclass, fields, is_dataclass, replace
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping

from benchmarks.common.eac import (
    ActionRef, ActorScope, AuthorityError, EPreRef, EffectGateway, EffectRejected,
    ExactRequest, Proposition, PropositionKey, ProvenanceRecord, RuntimeAuthority,
    bind_source_profile, load_support_policy,
)
from benchmarks.common.eac.model import NativeEffectResult
from benchmarks.common.eac.canonical import canonical_bytes, thaw_json
from env.eac_observation_adapter import sanitized_scan_rows, sanitized_visible_blocks
from benchmarks.common.eac.authority import _plain as _authority_plain
from env.runtime_paths import atomic_write_json

ROOT = Path(__file__).resolve().parents[2]
CLASSIFICATION_PATH = ROOT / "docs/eac/minecraft_preconditions_v1.json"
SOURCE_PROFILE_PATH = ROOT / "docs/eac/minecraft_source_profile_v1.json"
INGESTION_CONTRACT_PATH = ROOT / "docs/eac/minecraft_ingestion_contract_v1.json"
RUNTIME_ID = "minecraft-eac-runtime-v1"
SUPPORTED_MODES = frozenset(("dual_dag_advisory", "dual_dag_authority"))
FORBIDDEN_EVIDENCE_ORIGINS = frozenset((
    "score", "final_score", "progress", "evaluator", "meta_judger",
    "simulator_truth", "experiment_oracle", "post_hoc_action_log",
))


class MinecraftEACError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MinecraftPreparedAction:
    tool_name: str
    request: ExactRequest
    gateway: EffectGateway
    permit: Any = None
    arguments: tuple[tuple[str, Any], ...] = ()


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(_plain(value))).hexdigest()


def _minecraft_identifier(value: Any) -> Any:
    return value.lower().replace(" ", "_") if isinstance(value, str) else value


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise MinecraftEACError(f"EAC artifact is not an object: {path}")
    return value


def _authenticate_classification(value: Mapping[str, Any]) -> str:
    declared = value.get("detached_artifact_sha256")
    detached = dict(value)
    detached.pop("detached_artifact_sha256", None)
    observed = _digest(detached)
    if declared != observed:
        raise MinecraftEACError("Minecraft EPre classification digest mismatch")
    return observed


def _authenticate_ingestion_contract(value: Mapping[str, Any]) -> tuple[str, str]:
    detached = dict(value)
    declared = detached.pop("detached_artifact_sha256", None)
    observed = _digest(detached)
    if declared != observed:
        raise MinecraftEACError("Minecraft ingestion contract digest mismatch")
    adapter = value["trusted_observation_adapter"]
    implementation = ROOT / adapter["implementation_path"]
    if hashlib.sha256(implementation.read_bytes()).hexdigest() != adapter["implementation_sha256"]:
        raise MinecraftEACError("Minecraft observation adapter implementation digest mismatch")
    tool_digest = _digest(value["trusted_observation_adapter"])
    rule_digest = _digest(value["rule_evaluation"])
    return tool_digest, rule_digest


class MinecraftEACRuntime:
    """One immutable Minecraft Advisory or Authority runtime.

    Only wrappers installed by :meth:`VillagerBench.guard_tool_actions` are in
    scope. Direct ``Agent`` calls and direct bridge HTTP calls are excluded.
    """

    def __init__(self, *, mode: str, run_id: str,
                 env_prechecks: Mapping[str, Callable[[ExactRequest], bool]] | None = None,
                 sec_prechecks: Mapping[str, Callable[[ExactRequest], bool]] | None = None,
                 audit_path: str | Path | None = None,
                 identity_binding: Mapping[str, Any] | None = None):
        if mode not in SUPPORTED_MODES:
            raise ValueError(f"unsupported Minecraft EAC mode: {mode}")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("Minecraft EAC run_id is required")
        self.mode = mode
        self.run_id = run_id
        self.identity_binding = _plain(dict(identity_binding)) if identity_binding is not None else None
        self.classification = _load_json(CLASSIFICATION_PATH)
        self._classification_digest = _authenticate_classification(self.classification)
        self.profile_document = _load_json(SOURCE_PROFILE_PATH)
        self.ingestion_contract = _load_json(INGESTION_CONTRACT_PATH)
        tool_digest, rule_digest = _authenticate_ingestion_contract(self.ingestion_contract)
        trusted_tool = self.profile_document["trusted_tools"][0]
        integrity = self.profile_document["integrity_contract"]
        if (trusted_tool["integrity_contract_sha256"] != tool_digest
                or integrity["canonical_content_sha256"] != self.ingestion_contract["detached_artifact_sha256"]
                or integrity["rule_evaluation_contract_sha256"] != rule_digest):
            raise MinecraftEACError("Minecraft SourceProfile integrity contract mismatch")
        self.policy_binding = load_support_policy()
        self.profile_binding = bind_source_profile(self.profile_document)
        authority_mode = "authority" if mode == "dual_dag_authority" else "advisory"
        self.authority = RuntimeAuthority(
            policy_binding=self.policy_binding,
            profile_binding=self.profile_binding,
            mode=authority_mode,
            source_authenticator=self._authenticate_record,
            authority_nonce="minecraft-eac:" + run_id,
        )
        self._actions = {item["action_identity"]: item for item in self.classification["actions"]}
        self._env_prechecks = dict(env_prechecks or {})
        self._sec_prechecks = dict(sec_prechecks or {})
        for name, item in self._actions.items():
            if item["sec_pre"] and name not in self._sec_prechecks:
                raise MinecraftEACError(f"classified SecPre requires an explicit adapter: {name}")
        self._sequence = 0
        self._lock = RLock()
        self._records = deque(maxlen=256)
        self._evidence_total = 0
        self._current_roots: dict[tuple[str, PropositionKey], str] = {}
        self._fluent_revision = 0
        self._initial_state_ingested: set[str] = set()
        self._last_permit: dict[str, Any] = {}
        self._audit_path = Path(audit_path) if audit_path is not None else None
        self._persist_audit()

    @property
    def classification_identity(self) -> str:
        return self._classification_digest

    @property
    def source_profile_identity(self) -> str:
        return self.profile_binding.digest_sha256

    @staticmethod
    def _authenticate_record(record, proposition, binding, rule) -> bool:
        return (record.get("issuer") == "minecraft-eac-adapter"
                and binding.profile_id == "minecraft-eac-primary"
                and proposition.key.namespace == rule["record_namespace"])

    def classification_for(self, tool_name: str) -> Mapping[str, Any]:
        try:
            return self._actions[tool_name]
        except KeyError:
            raise MinecraftEACError(f"unclassified Minecraft tool: {tool_name}") from None

    def supports_tool(self, tool_name: str) -> bool:
        return tool_name in self._actions

    @staticmethod
    def bind_tool_arguments(function, args, kwargs) -> dict[str, Any]:
        if not args and kwargs and any(parameter.kind is inspect.Parameter.VAR_KEYWORD
                                       for parameter in inspect.signature(function).parameters.values()):
            return {key: value for key, value in kwargs.items() if key not in {"emotion", "murmur"}}
        signature = inspect.signature(function)
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        return {key: value for key, value in bound.arguments.items()
                if key not in {"emotion", "murmur"}}

    def _proposition(self, classification, arguments) -> Proposition:
        names = classification["proposition_argument_fields"]
        values = tuple(_minecraft_identifier(arguments[name]) for name in names if name in arguments)
        return Proposition(PropositionKey(
            classification["proposition_namespace"],
            classification["proposition_predicate"], values,
            classification["temporal_scope"],
        ))

    def _definitions(self, classification, proposition):
        action_definition = {
            "action_identity": classification["action_identity"],
            "action_version": classification["action_version"],
            "argument_fields": classification["argument_fields"],
            "effect_gateway_mapping": classification["effect_gateway_mapping"],
            "classification": {
                "identity": self.classification["artifact_id"],
                "version": self.classification["artifact_version"],
                "digest": self.classification_identity,
            },
        }
        action = ActionRef(classification["action_identity"], classification["action_version"],
                           _digest(action_definition))
        declared = (proposition,) if classification["epre"] else ()
        epre_definition = {
            "classification_identity": self.classification_identity,
            "action_identity": classification["action_identity"],
            "propositions": [_plain(item) for item in declared],
        }
        declared_digest = _digest(declared)
        epre = EPreRef("minecraft-epre:" + classification["action_identity"] + ":" + declared_digest,
                       1, declared_digest)
        return action_definition, action, epre_definition, epre, declared

    def mediate_tool(self, tool_name: str, function, args, kwargs):
        """Adapt one registered tool invocation and enter the shared gateway."""
        prepared = self.prepare_tool(tool_name, function, args, kwargs)
        return self.execute_prepared(prepared)

    def prepare_tool(self, tool_name: str, function, args, kwargs) -> MinecraftPreparedAction:
        """Create/evaluate a candidate without crossing the native effect boundary."""
        with self._lock:
            classification = self.classification_for(tool_name)
            arguments = self.bind_tool_arguments(function, args, kwargs)
            actor_id = arguments.pop("player_name", None)
            if not isinstance(actor_id, str) or not actor_id:
                raise MinecraftEACError("classified tool requires player_name")
            proposition = self._proposition(classification, arguments)
            action_definition, action, unused_epre_definition, epre, declared = self._definitions(
                classification, proposition)
            self.authority.register_action_definition(action, action_definition)
            self.authority.register_epre_definition(epre, declared)
            self._sequence += 1
            candidate_id = f"{self.run_id}:{self._sequence}:{tool_name}"
            request = ExactRequest(
                candidate_id, candidate_id + ":attempt", action,
                tuple((key, value) for key, value in arguments.items()),
                target={key: arguments[key] for key in classification["argument_fields"] if key in arguments},
            )
            actor = ActorScope(actor_id, self._sequence, ("minecraft", self.run_id))
            env_preconditions = tuple(
                Proposition(PropositionKey(
                    item["namespace"], item["predicate"],
                    tuple(_minecraft_identifier(arguments[name]) for name in item["argument_fields"]),
                    item["temporal_scope"],
                ))
                for item in classification.get("env_preconditions", ())
            )
            self.authority.register_candidate(
                request, actor=actor, epre_ref=epre, epre=declared,
                env_pre=env_preconditions,
                sec_pre=(classification["action_identity"],) if classification["sec_pre"] else (),
                capability_dependencies=(classification["capability_dependency"],),
            )
            env_pre = self._env_prechecks.get(tool_name)
            if env_pre is None:
                env_pre = lambda unused: self._native_preflight(actor_id, tool_name, arguments)
            sec_pre = self._sec_prechecks.get(tool_name, lambda unused: True)
            frozen_args = tuple(_plain(item) for item in args)
            frozen_kwargs = {key: _plain(value) for key, value in kwargs.items()}
            def native(unused):
                # execute_fenced has admitted the effect immediately before this
                # callback. Persist that state before crossing the HTTP boundary.
                self._persist_audit()
                result = function(*frozen_args, **frozen_kwargs)
                if isinstance(result, Mapping) and result.get("status") is not True:
                    return NativeEffectResult(result, "effect_failed")
                return result
            gateway = EffectGateway(self.authority, native, env_pre=env_pre, sec_pre=sec_pre)
            try:
                if self.mode == "dual_dag_authority":
                    permit = self.authority.issue_permit(candidate_id)
                    self._last_permit[tool_name] = permit
                else:
                    permit = None
            except (AuthorityError, EffectRejected) as exc:
                self._persist_audit()
                raise MinecraftEACError(str(exc)) from exc
            self._persist_audit()
            return MinecraftPreparedAction(tool_name, request, gateway, permit,
                                            tuple((key, _plain(value)) for key, value in arguments.items()))

    def execute_prepared(self, prepared: MinecraftPreparedAction):
        """Execute one previously prepared action through the shared gateway."""
        with self._lock:
            try:
                if self.mode == "dual_dag_authority":
                    result = prepared.gateway.execute(prepared.request, prepared.permit)
                else:
                    result = prepared.gateway.execute_advisory(prepared.request)
            except (AuthorityError, EffectRejected) as exc:
                self._persist_audit()
                raise MinecraftEACError(str(exc)) from exc
            finally:
                self._persist_audit()
            # ExactRequest intentionally has no actor field; recover the canonical candidate scope.
            actor_id = self.authority._candidates[prepared.request.candidate_id].actor.actor_id
            proposition = self.authority._candidates[prepared.request.candidate_id].epre
            if proposition and not (isinstance(result, Mapping) and result.get("status") is not True):
                self._ingest_visible_outcome(actor_id, prepared.tool_name, proposition[0], result)
            self._ingest_result_evidence(actor_id, prepared.tool_name, result,
                                         dict(prepared.arguments))
            self._persist_audit()
            return result

    def ingest_actor_record(self, *, actor_id: str, proposition: Proposition,
                            record_type: str, source: str, payload: Mapping[str, Any] | None = None,
                            visible_to: tuple[str, ...] | None = None,
                            root_id: str | None = None, revision: int | str = 1,
                            supersedes: tuple[str, ...] = ()):
        with self._lock:
            if source in FORBIDDEN_EVIDENCE_ORIGINS:
                raise MinecraftEACError("forbidden evaluator/oracle evidence origin")
            if record_type not in {"direct_observation", "trusted_tool_result",
                                   "visible_action_outcome", "peer_report"}:
                raise MinecraftEACError("unknown Minecraft evidence record type")
            visible = tuple(visible_to or (actor_id,))
            if visible != (actor_id,):
                raise MinecraftEACError("evidence must be private to its observing actor")
            current_slot = ((actor_id, proposition.key)
                            if record_type in {"direct_observation", "visible_action_outcome"} else None)
            tracked_current = self._current_roots.get(current_slot) if current_slot else None
            if current_slot is not None:
                if isinstance(revision, bool) or not isinstance(revision, int):
                    raise MinecraftEACError("current-fluent revision must be an integer")
                if tracked_current is None and supersedes:
                    raise MinecraftEACError("supersession requires a tracked current fluent")
                if tracked_current is not None:
                    if supersedes != (tracked_current,):
                        raise MinecraftEACError("new current evidence must supersede the tracked fluent")
                    previous_revision = self.authority._roots[tracked_current].source_stream_revision
                    if previous_revision is None or revision <= previous_revision:
                        raise MinecraftEACError("current-fluent revision must increase monotonically")
            self._sequence += 1
            rid = root_id or f"minecraft-root:{self.run_id}:{self._sequence}"
            provenance_id = "minecraft-prov:" + rid
            self.authority.put_provenance(ProvenanceRecord(provenance_id, source))
            record = {
                "namespace": "minecraft", "type": record_type,
                "visible_to": list(visible), "source_lineage_id": source,
                "upstream_origin_id": source, "issuer": "minecraft-eac-adapter",
                "source": source, "proposition": _authority_plain(proposition),
            }
            if record_type == "trusted_tool_result":
                record.update({
                    "tool_identity": "minecraft-observation-adapter", "tool_version": "1",
                    "integrity_contract_sha256": self.profile_document["trusted_tools"][0]["integrity_contract_sha256"],
                })
            if payload:
                record["sanitized_payload"] = _plain(dict(payload))
            if record_type in {"direct_observation", "visible_action_outcome"}:
                if not isinstance(revision, int):
                    raise MinecraftEACError("supersession requires a monotonic direct-observation revision")
                stream = ("minecraft-visible-state" if record_type == "direct_observation"
                          else "minecraft-visible-action-state")
                record.update({"source_stream_id": stream,
                               "source_stream_revision": revision})
            elif supersedes:
                raise MinecraftEACError("supersession requires a monotonic direct-observation revision")
            root = self.authority.ingest_record(
                record, proposition=proposition, root_id=rid, revision=revision,
                provenance_id=provenance_id, supersedes=supersedes)
            if current_slot is not None:
                self._current_roots[current_slot] = root.root_id
                self._fluent_revision = max(self._fluent_revision, revision)
            evidence_kind = payload.get("evidence_kind") if isinstance(payload, Mapping) else None
            self._records.append({"root_id": rid, "record_type": evidence_kind or record_type,
                                  "authority_record_type": record_type,
                                  "actor_id": actor_id, "source": source})
            self._evidence_total += 1
            self._persist_audit()
            return root

    def ingest_target_observation(self, actor_id: str, action_name: str,
                                  arguments: Mapping[str, Any], *, revision: int | str = 1):
        classification = self.classification_for(action_name)
        proposition = self._proposition(classification, arguments)
        return self._ingest_current_fluent(actor_id, proposition,
                                           source="minecraft-visible-observation")

    def _ingest_current_fluent(self, actor_id: str, proposition: Proposition, *, source: str,
                               evidence_kind: str = "direct_observation", payload=None,
                               record_type: str = "direct_observation"):
        with self._lock:
            slot = (actor_id, proposition.key)
            self._fluent_revision += 1
            previous = self._current_roots.get(slot)
            root = self.ingest_actor_record(
                actor_id=actor_id, proposition=proposition, record_type=record_type,
                source=source, payload={"evidence_kind": evidence_kind, **(payload or {})},
                revision=self._fluent_revision, supersedes=(previous,) if previous else (),
            )
            self._current_roots[slot] = root.root_id
            return root

    def ingest_initial_actor_state(self, actor_id: str, state: Mapping[str, Any]):
        with self._lock:
            if actor_id in self._initial_state_ingested:
                return ()
            if (not isinstance(state, Mapping) or state.get("status") is not True
                    or not isinstance(state.get("message"), Mapping)
                    or not isinstance(state["message"].get("blocks"), list)):
                return ()
            roots = []
            for block_name, coordinates in sanitized_visible_blocks(state):
                proposition = Proposition(PropositionKey(
                    "minecraft", "target_block_present", tuple(coordinates), "current"))
                roots.append(self._ingest_current_fluent(
                    actor_id, proposition, source="minecraft-initial-visible-state",
                    evidence_kind="initial_visible_block", payload={"block_name": block_name},
                ))
            self._initial_state_ingested.add(actor_id)
            return tuple(roots)

    def ingest_peer_report(self, actor_id: str, proposition: Proposition, sender: str):
        return self.ingest_actor_record(
            actor_id=actor_id, proposition=proposition, record_type="peer_report",
            source="minecraft-peer:" + sender)

    def _ingest_visible_outcome(self, actor_id, tool_name, proposition, result) -> None:
        if tool_name != "MineBlock":
            return
        self._ingest_current_fluent(
            actor_id, replace(proposition, polarity=False), source="minecraft-action:MineBlock",
            evidence_kind="action_derived_direct_observation",
            payload={"status": result.get("status") if isinstance(result, Mapping) else None})
        outcome = Proposition(PropositionKey(
            proposition.key.namespace, "mineblock_success_observed",
            proposition.key.arguments, proposition.key.temporal_scope))
        self._ingest_current_fluent(
            actor_id, outcome, source="minecraft-action:MineBlock",
            evidence_kind="visible_action_outcome", record_type="visible_action_outcome",
            payload={"status": result.get("status") if isinstance(result, Mapping) else None})

    def _ingest_result_evidence(self, actor_id: str, tool_name: str, result: Any,
                                request_arguments: Mapping[str, Any] | None = None) -> None:
        """Convert sanitized observation/message results at event time."""
        if not isinstance(result, Mapping) or result.get("status") is not True:
            return
        if tool_name == "scanNearbyEntities":
            rows = sanitized_scan_rows(result, request_arguments)
            for index, (name, position) in enumerate(rows):
                proposition = Proposition(PropositionKey(
                    "minecraft", "entity_observed", (_minecraft_identifier(name), position), "current"))
                self.ingest_actor_record(
                    actor_id=actor_id, proposition=proposition,
                    record_type="trusted_tool_result", source="minecraft-observation-adapter",
                    root_id=f"minecraft-scan:{self.run_id}:{self._sequence}:{index}")
        elif tool_name in {"talkTo", "waitForFeedback"}:
            events = result.get("new_events", ())
            if not isinstance(events, (list, tuple)):
                return
            for index, event in enumerate(events[:128]):
                proposition = Proposition(PropositionKey(
                    "minecraft", "peer_message_received", (_plain(event),), "current"))
                self.ingest_peer_report(actor_id, proposition, "peer")
        if tool_name == "scanNearbyEntities":
            for index, (name, position) in enumerate(sanitized_scan_rows(result, request_arguments)):
                observed_values = []
                if isinstance(position, (list, tuple)) and len(position) == 3:
                    coordinates = tuple(position)
                    observed_values.extend((("placement_target_observed", coordinates),
                                            ("destination_observed", coordinates)))
                if name:
                    normalized = (_minecraft_identifier(name),)
                    observed_values.extend((("entity_target_observed", normalized),
                                            ("recipient_observed", normalized)))
                for suffix, (predicate, values) in enumerate(observed_values):
                    proposition = Proposition(PropositionKey(
                        "minecraft", predicate, values, "current"))
                    self.ingest_actor_record(
                        actor_id=actor_id, proposition=proposition,
                        record_type="trusted_tool_result", source="minecraft-observation-adapter",
                        root_id=f"minecraft-target:{self.run_id}:{self._sequence}:{index}:{suffix}")

    def audit_artifact(self) -> dict[str, Any]:
        sequence = self.authority._sequence
        authority_audit = self.authority.audit_snapshot(
            limit=256, after_sequence=max(0, sequence - 256))
        attempts = self.authority.attempt_snapshot()
        return {
            "schema_version": "minecraft-eac-audit/1",
            "runtime_identity": RUNTIME_ID,
            "execution_identity": ({
                "execution_revision": self.identity_binding["execution_revision"],
                "runtime_digest": self.identity_binding["runtime_digest"],
                "premanifest_identity": self.identity_binding["premanifest_identity"],
            } if self.identity_binding is not None else None),
            "mode": self.mode,
            "support_policy": _plain(self.authority.policy),
            "source_profile": _plain(self.authority.profile),
            "classification": {
                "identity": self.classification["artifact_id"],
                "version": self.classification["artifact_version"],
                "digest": self.classification_identity,
            },
            "authority_audit": [_plain(item) for item in authority_audit],
            "audit_sequence": sequence,
            "audit_truncated": sequence > len(authority_audit),
            "attempts": [_plain(item) for item in attempts[-256:]],
            "attempts_truncated": len(attempts) > 256,
            "evidence_index": tuple(self._records),
            "evidence_total": self._evidence_total,
            "evidence_truncated": self._evidence_total > len(self._records),
            "read_only_projection": True,
            "oracle_state_included": False,
            "bounded": True,
            "audit_limit": 256,
        }

    def _persist_audit(self) -> None:
        if self._audit_path is not None:
            atomic_write_json(self._audit_path, self.audit_artifact())

    @staticmethod
    def _native_preflight(actor_id: str, tool_name: str, arguments: Mapping[str, Any]) -> bool:
        """Ask the trusted bridge for a read-only effect-time legality decision."""
        from env.minecraft_client import Agent, _minecraft_request
        response = _minecraft_request(
            "POST", Agent.get_agent_url(actor_id) + "/post_eac_preflight",
            data=json.dumps({"action": tool_name, "arguments": _plain(dict(arguments))}),
            headers=Agent.headers,
        )
        payload = response.json()
        return isinstance(payload, Mapping) and payload.get("status") is True


def install_minecraft_eac(environment, *, mode: str, run_id: str,
                          env_prechecks=None, sec_prechecks=None,
                          identity_binding=None) -> MinecraftEACRuntime:
    runtime = MinecraftEACRuntime(
        mode=mode, run_id=run_id, env_prechecks=env_prechecks, sec_prechecks=sec_prechecks,
        identity_binding=identity_binding,
        audit_path=environment.runtime_paths.data_dir / "minecraft_eac_audit.json")
    environment.configure_eac_runtime(runtime)
    return runtime
