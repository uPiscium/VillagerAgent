"""Frozen, non-executing contracts for the K6 confirmatory benchmark.

This module validates the checked-in inventory/protocol/schema, constructs the
finite deterministic cell census, validates completed cell traces, and computes
exact numerator/denominator summaries.  It never starts a Minecraft runtime.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from benchmarks.common.eac import ActionRef, ExactRequest
from benchmarks.common.eac.canonical import canonical_bytes

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
INVENTORY_PATH = HERE / "k6_inventory_v1.json"
PROTOCOL_PATH = HERE / "k6_protocol_v1.json"
RESULT_SCHEMA_PATH = HERE / "k6_result_schema_v1.json"
CLASSIFICATION_PATH = ROOT / "docs/eac/minecraft_preconditions_v1.json"

RUNTIME_BASE_REVISION = "2361588ba22e6da386cf2092cb9b92845b9c98c4"
CLASSIFICATION_DIGEST = "7c8bf97b80c96f1d05e8250cb9d89bb21b35c073f49979501090d72f13b56001"
SUPPORT_POLICY = {
    "identity": "eac-primary-support",
    "version": 1,
    "digest": "ef34b67ef618ed4b34a9c2720d854e02d8fb6af917a0cbe472daef8cc5603d51",
}
SOURCE_PROFILE = {
    "identity": "minecraft-eac-primary",
    "version": 1,
    "digest": "01f65a8fd4bb68b1631e81d3c8d50f073747b5179995eeb60be3a55fdb6979be",
}
INGESTION_CONTRACT = {
    "identity": "minecraft-eac-ingestion-contract",
    "version": 1,
    "digest": "33c9fd27a70ab3f6edffad14c07f9f66dc04b795363e7b1b55518a4d1a1ef42f",
}
RUNTIME_CONTENT_BINDINGS = {
    "benchmarks/minecraft/eac_runtime.py": "9e99b372f1447cb4e9ca03250f27dc88558fd8394b54660d0a82adc1fd0b7a49",
    "benchmarks/common/eac/authority.py": "f02465438e7e8584a2aee141d9ab1d8df23076c777ea7f1faec7b00e911a1649",
    "benchmarks/common/eac/gateway.py": "ff70f534014251c5664b85085a9ce7d8fcf462b941aedd83c1c4aed95c9a67ee",
    "benchmarks/common/eac/witness.py": "bcf0b0e763f0254a808bf4ea70cd5bdbb7ec672ae1009fecd11a33a4e7977e52",
    "benchmarks/common/eac/policy.py": "cb2e15c843678964366d3d118672c516ea22c66b94a1bd043c1a2767368e235a",
    "docs/eac/support_policy_v1.json": "7652a0cba3f81cf0931a16a69adb4918e0b459d80a41a7b2768496a8787a0a6f",
    "docs/eac/minecraft_source_profile_v1.json": "01414650511b2ded27a5025fbc7797426b02d874cea468316fbce3711e797abe",
    "docs/eac/minecraft_preconditions_v1.json": "c4ac4268663cd5b9d7d5900f65d05b233d85f45906d6206ed73cbb6915ba46ad",
    "docs/eac/minecraft_ingestion_contract_v1.json": "ddeb257dbf3f6e042d7faf2c94fc230818260cc4fa9d97d39c0c428bd952462d",
}

PRIMARY_FAMILIES = ("S1", "S2", "S3")
CONTROL_FAMILIES = ("C1", "C2")
CONDITIONS = ("dual_dag_advisory", "dual_dag_authority")
ACTORS = ("Alice", "Bob")
EXACT_FIELDS = (
    "candidate_id", "attempt_id", "exact_request_digest", "action", "arguments", "target",
)
C2_EVALUATOR_FIELDS = (
    "evaluator_truth_before", "evaluator_truth_after",
    "evaluator_truth_before_digest", "evaluator_truth_after_digest",
    "evaluator_truth_changed", "evaluator_truth_authority_input",
    "evaluator_truth_precondition_input",
)
EXPECTED_INVENTORY = (
    ("I1", "MineBlock", "target_block_present"),
    ("I2", "placeBlock", "placement_target_observed"),
    ("I3", "navigateTo", "destination_observed"),
    ("I4", "attackTarget", "entity_target_observed"),
    ("I5", "handoverBlock", "recipient_observed"),
)


class K6ContractError(ValueError):
    """A checked-in K6 artifact or candidate trace violates the frozen contract."""


def _load_json(path: Path) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise K6ContractError(f"duplicate JSON key in {path}: {key}")
            result[key] = value
        return result

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise K6ContractError(f"K6 artifact must be an object: {path}")
    return value


def detached_digest(value: Mapping[str, Any]) -> str:
    """Return the lowercase SHA-256 of canonical content excluding its digest field."""
    detached = dict(value)
    detached.pop("detached_artifact_sha256", None)
    return hashlib.sha256(canonical_bytes(detached)).hexdigest()


def validate_detached_digest(value: Mapping[str, Any], label: str) -> str:
    declared = value.get("detached_artifact_sha256")
    if (not isinstance(declared, str) or len(declared) != 64
            or any(character not in "0123456789abcdef" for character in declared)):
        raise K6ContractError(f"{label} digest is missing or malformed")
    if declared != detached_digest(value):
        raise K6ContractError(f"{label} detached digest mismatch")
    return declared


def _validate_runtime_content() -> None:
    for relative_path, expected in RUNTIME_CONTENT_BINDINGS.items():
        path = ROOT / relative_path
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise K6ContractError(f"K6 runtime content binding mismatch: {relative_path}")


@dataclass(frozen=True, slots=True)
class K6InventoryItem:
    inventory_id: str
    action_identity: str
    action_version: int
    request_arguments: tuple[tuple[str, Any], ...]
    proposition_namespace: str
    proposition_predicate: str
    proposition_argument_fields: tuple[str, ...]
    proposition_arguments: tuple[tuple[str, Any], ...]
    temporal_scope: str
    declared_env_pre: bool
    declared_sec_pre: bool

    def request(self) -> dict[str, Any]:
        return dict(self.request_arguments)

    def proposition_values(self) -> dict[str, Any]:
        return dict(self.proposition_arguments)


@dataclass(frozen=True, slots=True)
class K6CellSpec:
    cell_id: str
    scenario_family: str
    inventory_id: str
    condition: str
    affected_actor: str
    matrix: str


def _authenticated_classification(path: Path = CLASSIFICATION_PATH) -> dict[str, Any]:
    value = _load_json(path)
    declared = value.get("detached_artifact_sha256")
    if (value.get("artifact_id"), value.get("artifact_version"), declared) != (
        "minecraft-preconditions", 1, CLASSIFICATION_DIGEST,
    ):
        raise K6ContractError("EPre classification identity mismatch")
    if detached_digest(value) != declared:
        raise K6ContractError("EPre classification content digest mismatch")
    return value


def load_k6_inventory(
    path: str | Path = INVENTORY_PATH,
    *,
    classification_path: str | Path = CLASSIFICATION_PATH,
) -> tuple[K6InventoryItem, ...]:
    value = _load_json(Path(path))
    validate_detached_digest(value, "K6 inventory")
    if set(value) != {
        "artifact_id", "artifact_version", "detached_artifact_sha256",
        "classification_binding", "iid_samples", "inventory_census", "items",
    }:
        raise K6ContractError("K6 inventory schema mismatch")
    if (value["artifact_id"], value["artifact_version"], value["iid_samples"],
            value["inventory_census"]) != ("minecraft-k6-action-inventory", 1, False, True):
        raise K6ContractError("K6 inventory identity mismatch")

    classification = _authenticated_classification(Path(classification_path))
    binding = value["classification_binding"]
    if binding != {
        "artifact_id": "minecraft-preconditions",
        "artifact_version": 1,
        "detached_artifact_sha256": CLASSIFICATION_DIGEST,
    }:
        raise K6ContractError("K6 inventory classification binding mismatch")
    definitions = {item["action_identity"]: item for item in classification["actions"]}
    raw_items = value["items"]
    if not isinstance(raw_items, list) or len(raw_items) != len(EXPECTED_INVENTORY):
        raise K6ContractError("K6 inventory must contain exactly five action strata")

    items: list[K6InventoryItem] = []
    for raw, expected in zip(raw_items, EXPECTED_INVENTORY):
        if set(raw) != {
            "inventory_id", "action_identity", "action_version", "request_arguments",
            "proposition", "declared_env_pre", "declared_sec_pre", "actor_scope_rule",
        }:
            raise K6ContractError("K6 inventory item schema mismatch")
        inventory_id, action_identity, predicate = expected
        if (raw["inventory_id"], raw["action_identity"], raw["proposition"].get("predicate")) != expected:
            raise K6ContractError("K6 inventory order or identity mismatch")
        definition = definitions.get(action_identity)
        if definition is None:
            raise K6ContractError(f"inventory action is absent from classification: {action_identity}")
        if not all((definition["epre"] is True, definition["env_pre"] is True,
                    definition["sec_pre"] is False,
                    definition["actor_scope_rule"] == "acting-player-visible")):
            raise K6ContractError(f"inventory action is not eligible: {action_identity}")
        proposition = raw["proposition"]
        if set(proposition) != {"namespace", "predicate", "argument_fields", "arguments", "temporal_scope"}:
            raise K6ContractError("K6 proposition schema mismatch")
        request_arguments = raw["request_arguments"]
        if (raw["action_version"] != definition["action_version"]
                or tuple(request_arguments) != tuple(definition["argument_fields"])
                or proposition["namespace"] != definition["proposition_namespace"]
                or predicate != definition["proposition_predicate"]
                or tuple(proposition["argument_fields"]) != tuple(definition["proposition_argument_fields"])
                or proposition["temporal_scope"] != definition["temporal_scope"]
                or tuple(proposition["arguments"]) != tuple(definition["proposition_argument_fields"])
                or any(proposition["arguments"][name] != request_arguments[name]
                       for name in definition["proposition_argument_fields"])
                or raw["declared_env_pre"] is not definition["env_pre"]
                or raw["declared_sec_pre"] is not definition["sec_pre"]
                or raw["actor_scope_rule"] != definition["actor_scope_rule"]):
            raise K6ContractError(f"inventory/classification mismatch: {action_identity}")
        items.append(K6InventoryItem(
            inventory_id=inventory_id,
            action_identity=action_identity,
            action_version=raw["action_version"],
            request_arguments=tuple(request_arguments.items()),
            proposition_namespace=proposition["namespace"],
            proposition_predicate=predicate,
            proposition_argument_fields=tuple(proposition["argument_fields"]),
            proposition_arguments=tuple(proposition["arguments"].items()),
            temporal_scope=proposition["temporal_scope"],
            declared_env_pre=raw["declared_env_pre"],
            declared_sec_pre=raw["declared_sec_pre"],
        ))
    if len({item.action_identity for item in items}) != len(items):
        raise K6ContractError("K6 inventory action identities must be unique")
    return tuple(items)


def build_primary_cells(inventory: Iterable[K6InventoryItem] | None = None) -> tuple[K6CellSpec, ...]:
    items = tuple(inventory or load_k6_inventory())
    cells: list[K6CellSpec] = []
    for family in ("S1", "S2"):
        for item in items:
            for condition in CONDITIONS:
                cells.append(K6CellSpec(
                    f"K6-{family}-{item.inventory_id}-{condition}", family,
                    item.inventory_id, condition, "Alice", "primary",
                ))
    for item in items:
        for affected_actor in ACTORS:
            for condition in CONDITIONS:
                cells.append(K6CellSpec(
                    f"K6-S3-{item.inventory_id}-{affected_actor}-{condition}", "S3",
                    item.inventory_id, condition, affected_actor, "primary",
                ))
    if len(cells) != 40 or len({cell.cell_id for cell in cells}) != 40:
        raise K6ContractError("K6 primary matrix is not the frozen 40-cell census")
    return tuple(cells)


def build_control_cells(inventory: Iterable[K6InventoryItem] | None = None) -> tuple[K6CellSpec, ...]:
    items = tuple(inventory or load_k6_inventory())
    cells = tuple(
        K6CellSpec(
            f"K6-{family}-{item.inventory_id}-{condition}", family,
            item.inventory_id, condition, "Alice", "control",
        )
        for family in CONTROL_FAMILIES
        for item in items
        for condition in CONDITIONS
    )
    if len(cells) != 20 or len({cell.cell_id for cell in cells}) != 20:
        raise K6ContractError("K6 control matrix is not the frozen 20-cell census")
    return cells


def build_k6_cells(inventory: Iterable[K6InventoryItem] | None = None) -> tuple[K6CellSpec, ...]:
    items = tuple(inventory or load_k6_inventory())
    return build_primary_cells(items) + build_control_cells(items)


def load_k6_protocol(path: str | Path = PROTOCOL_PATH) -> dict[str, Any]:
    protocol = _load_json(Path(path))
    protocol_digest = validate_detached_digest(protocol, "K6 protocol")
    inventory_document = _load_json(INVENTORY_PATH)
    schema_document = _load_json(RESULT_SCHEMA_PATH)
    inventory_digest = validate_detached_digest(inventory_document, "K6 inventory")
    schema_digest = validate_detached_digest(schema_document, "K6 result schema")
    inventory = load_k6_inventory()
    if set(schema_document) != {
        "artifact_id", "artifact_version", "detached_artifact_sha256", "schema_version",
        "required_sections", "exact_action_fields", "phase_fields", "s3_fields",
        "c2_evaluator_truth_fields", "ratio_encoding", "statistical_fields_forbidden",
    } or (schema_document["artifact_id"], schema_document["artifact_version"],
          schema_document["schema_version"]) != (
        "minecraft-k6-cell-trace-schema", 1, "minecraft-k6-cell-trace/1",
    ):
        raise K6ContractError("K6 result schema identity mismatch")
    expected_keys = {
        "artifact_id", "artifact_version", "detached_artifact_sha256", "protocol_id",
        "protocol_version", "runtime_base_revision", "inventory_binding", "result_schema_binding",
        "semantic_bindings", "runtime_content_bindings", "study_design", "pre_run_exposure",
        "scenario_families", "controls", "conditions",
        "cell_construction",
        "exact_action_invariant", "no_reconsideration_invariant", "semantic_world_separation",
        "primary_estimands", "exclusion_criteria", "mechanism_isolation",
    }
    if set(protocol) != expected_keys:
        raise K6ContractError("K6 protocol schema mismatch")
    if (protocol["artifact_id"], protocol["artifact_version"], protocol["protocol_id"],
            protocol["protocol_version"], protocol["runtime_base_revision"]) != (
        "minecraft-k6-confirmatory-protocol", 1, "minecraft-eac-k6-confirmatory", 1,
        RUNTIME_BASE_REVISION,
    ):
        raise K6ContractError("K6 protocol identity mismatch")
    if protocol["inventory_binding"] != {
        "artifact_id": inventory_document["artifact_id"],
        "artifact_version": inventory_document["artifact_version"],
        "detached_artifact_sha256": inventory_digest,
    } or protocol["result_schema_binding"] != {
        "artifact_id": schema_document["artifact_id"],
        "artifact_version": schema_document["artifact_version"],
        "detached_artifact_sha256": schema_digest,
    }:
        raise K6ContractError("K6 protocol artifact binding mismatch")
    if protocol["semantic_bindings"] != {
        "support_policy": SUPPORT_POLICY,
        "source_profile": SOURCE_PROFILE,
        "epre_classification": {
            "identity": "minecraft-preconditions", "version": 1,
            "digest": CLASSIFICATION_DIGEST,
        },
        "ingestion_contract": INGESTION_CONTRACT,
    }:
        raise K6ContractError("K6 semantic binding mismatch")
    if protocol["runtime_content_bindings"] != RUNTIME_CONTENT_BINDINGS:
        raise K6ContractError("K6 runtime content manifest mismatch")
    _validate_runtime_content()
    if protocol["study_design"] != {
        "iid_samples": False, "inventory_census": True,
        "primary_cell_count": 40, "control_cell_count": 20,
        "engineering_validation_executed": True,
        "full_census_executed": False,
        "aggregate_scientific_result_artifact_generated": False,
    }:
        raise K6ContractError("K6 study-design declaration mismatch")
    if protocol["pre_run_exposure"] != {
        "construction_validation": {
            "scope": "all_60_cells_through_pre_enforcement_construction",
            "primary_cell_count": 40,
            "control_cell_count": 20,
            "native_submission_performed": False,
        },
        "representative_submission_validation": {
            "scope": "bounded_engineering_pilot",
            "cell_count": 7,
            "cells": [
                "K6-S1-I1-dual_dag_advisory",
                "K6-S1-I1-dual_dag_authority",
                "K6-S2-I4-dual_dag_authority",
                "K6-S3-I5-Alice-dual_dag_authority",
                "K6-S3-I5-Bob-dual_dag_authority",
                "K6-C1-I1-dual_dag_authority",
                "K6-C2-I1-dual_dag_authority",
            ],
            "aggregate_scientific_result_artifact_generated": False,
        },
    }:
        raise K6ContractError("K6 pre-run exposure declaration mismatch")
    if tuple(protocol["conditions"]) != CONDITIONS:
        raise K6ContractError("K6 condition order mismatch")
    if protocol["cell_construction"] != {
        "inventory_order": [item.inventory_id for item in inventory],
        "primary_family_order": ["S1", "S2", "S3"],
        "s3_affected_actor_order": ["Alice", "Bob"],
        "condition_order": list(CONDITIONS),
        "control_family_order": ["C1", "C2"],
    }:
        raise K6ContractError("K6 cell-construction order mismatch")
    if tuple(protocol["exact_action_invariant"]["compared_fields"]) != EXACT_FIELDS:
        raise K6ContractError("K6 exact-action field contract mismatch")
    if tuple(schema_document["exact_action_fields"]) != EXACT_FIELDS:
        raise K6ContractError("K6 result schema exact-action contract mismatch")
    if set(schema_document["required_sections"]) != {
        "cell", "semantic_bindings", "r_p", "r_d", "r_e", "actor_scope", "mutation",
        "exact_action", "no_reconsideration", "s3", "mechanism_analysis",
    }:
        raise K6ContractError("K6 result schema section contract mismatch")
    if (set(schema_document["phase_fields"]) != set(_PHASE_KEYS)
            or any(set(schema_document["phase_fields"].get(key, ())) != values
                   for key, values in _PHASE_KEYS.items())):
        raise K6ContractError("K6 result schema phase contract mismatch")
    if set(schema_document["s3_fields"]) != {
        "affected_actor", "unaffected_actor", "unaffected_current_EAdm",
        "unaffected_r_p", "unaffected_r_d", "unaffected_r_e",
        "unaffected_same_prepared_object", "unaffected_exact_action_preserved",
        "unaffected_mechanism_analysis", "cross_actor_dependency_leak",
        "cross_actor_state_change_leak",
    }:
        raise K6ContractError("K6 result schema S3 contract mismatch")
    if tuple(schema_document["c2_evaluator_truth_fields"]) != C2_EVALUATOR_FIELDS:
        raise K6ContractError("K6 result schema C2 evaluator-truth contract mismatch")
    if schema_document["ratio_encoding"] != {
        "numerator": "integer", "denominator": "integer",
    } or schema_document["statistical_fields_forbidden"] != [
        "confidence_interval", "p_value", "iid_standard_error",
    ]:
        raise K6ContractError("K6 result schema statistical contract mismatch")
    if any(protocol["no_reconsideration_invariant"].get(name) != 0 for name in (
        "planner_calls", "model_calls", "controller_redecisions", "action_regenerations",
    )):
        raise K6ContractError("K6 no-reconsideration contract mismatch")
    if len(build_primary_cells(inventory)) != 40 or len(build_control_cells(inventory)) != 20:
        raise K6ContractError("K6 cell census mismatch")
    protocol["validated_protocol_digest"] = protocol_digest
    protocol["validated_inventory_digest"] = inventory_digest
    protocol["validated_result_schema_digest"] = schema_digest
    return protocol


_PHASE_KEYS = {
    "r_p": set(EXACT_FIELDS) | {"EAdm", "authority_epoch", "witness_root_ids", "dependency_ids"},
    "r_d": {
        "current_EAdm", "authority_epoch", "reasons", "mutation_type",
        "mutation_dependency_ids", "intersecting_dependency_ids",
        "relevant_action_dependency_changed", "permit_or_shadow_fresh",
    },
    "r_e": set(EXACT_FIELDS) | {
        "current_EAdm", "authority_epoch_before_execution", "exact_action_submitted",
        "EnvPre_oracle", "SecPre_oracle", "execution_allowed", "rejection_reason",
        "native_callable_reached", "permit_or_shadow_fresh",
    },
}


def expected_action_digest(item: K6InventoryItem) -> str:
    classification = _authenticated_classification()
    definition = next(row for row in classification["actions"]
                      if row["action_identity"] == item.action_identity)
    action_definition = {
        "action_identity": definition["action_identity"],
        "action_version": definition["action_version"],
        "argument_fields": definition["argument_fields"],
        "effect_gateway_mapping": definition["effect_gateway_mapping"],
        "classification": {
            "identity": classification["artifact_id"],
            "version": classification["artifact_version"],
            "digest": CLASSIFICATION_DIGEST,
        },
    }
    return hashlib.sha256(canonical_bytes(action_definition)).hexdigest()


def _validate_exact_request_digest(phase: Mapping[str, Any]) -> None:
    action = phase["action"]
    try:
        request = ExactRequest(
            phase["candidate_id"], phase["attempt_id"],
            ActionRef(action["identity"], action["version"], action["digest"]),
            tuple(phase["arguments"].items()), phase["target"],
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise K6ContractError("K6 exact request fields are malformed") from exc
    observed = "sha256:" + hashlib.sha256(request.identity_bytes()).hexdigest()
    if phase["exact_request_digest"] != observed:
        raise K6ContractError("K6 exact request digest mismatch")


def _validate_mechanism_artifacts(models: Mapping[str, Any]) -> None:
    if set(models) != {"M0", "M1", "M2", "M3", "M4"}:
        raise K6ContractError("K6 mechanism-isolation result schema mismatch")
    for name, artifact in models.items():
        if not isinstance(artifact, Mapping) or set(artifact) != {
            "decision", "reason", "inputs_used", "relevant_action_dependency_changed",
        }:
            raise K6ContractError(f"K6 {name} decision artifact schema mismatch")
        allowed = {"allow", "reject"} if name != "M4" else {"allow", "reject", "not_applicable"}
        if artifact["decision"] not in allowed or not isinstance(artifact["inputs_used"], list):
            raise K6ContractError(f"K6 {name} decision artifact is invalid")


def _validate_mechanism_semantics(models, rp, rd, re, condition) -> None:
    _validate_mechanism_artifacts(models)
    exact_match = all(rp[field] == re[field] for field in EXACT_FIELDS)
    epoch_changed = rp["authority_epoch"] != re["authority_epoch_before_execution"]
    dependency_changed = rd["relevant_action_dependency_changed"]
    expected = {
        "M0": {
            "decision": "allow" if rp["EAdm"] else "reject",
            "reason": ("admission_epistemically_admissible" if rp["EAdm"]
                       else "admission_not_admissible"),
            "inputs_used": ["r_p.EAdm"],
            "relevant_action_dependency_changed": None,
        },
        "M1": {
            "decision": "allow" if exact_match else "reject",
            "reason": "exact_request_unchanged" if exact_match else "exact_request_changed",
            "inputs_used": [f"r_p/r_e.{field}" for field in EXACT_FIELDS],
            "relevant_action_dependency_changed": None,
        },
        "M2": {
            "decision": "reject" if epoch_changed else "allow",
            "reason": ("global_authority_revision_changed" if epoch_changed
                       else "global_authority_revision_unchanged"),
            "inputs_used": ["r_p.authority_epoch", "r_e.authority_epoch_before_execution"],
            "relevant_action_dependency_changed": None,
        },
        "M3": {
            "decision": "reject" if dependency_changed else "allow",
            "reason": ("relevant_action_dependency_changed" if dependency_changed
                       else "relevant_action_dependencies_unchanged"),
            "inputs_used": ["r_d.relevant_action_dependency_changed"],
            "relevant_action_dependency_changed": dependency_changed,
        },
    }
    if condition == "dual_dag_advisory":
        expected["M4"] = {
            "decision": "not_applicable",
            "reason": "existing_authority_not_run_in_advisory_mode",
            "inputs_used": [],
            "relevant_action_dependency_changed": None,
        }
    else:
        allowed = re["execution_allowed"]
        expected["M4"] = {
            "decision": "allow" if allowed else "reject",
            "reason": ("existing_authority_allowed" if allowed
                       else "existing_authority_" + str(re["rejection_reason"])),
            "inputs_used": ["existing_authority_gateway_outcome"],
            "relevant_action_dependency_changed": None,
        }
    if models != expected:
        raise K6ContractError("K6 mechanism-isolation decision does not match its information boundary")


def trace_pairing_digest(trace: Mapping[str, Any]) -> str:
    cell = trace["cell"]
    actor = cell["affected_actor"]
    actor_rp = {actor: trace["r_p"]}
    actor_rd = {actor: trace["r_d"]}
    if trace["s3"] is not None:
        other = trace["s3"]["unaffected_actor"]
        actor_rp[other] = trace["s3"]["unaffected_r_p"]
        actor_rd[other] = trace["s3"]["unaffected_r_d"]
    mutation = trace["mutation"]
    projection = {
        "scenario_family": cell["scenario_family"],
        "inventory_id": cell["inventory_id"],
        "affected_actor": actor,
        "matrix": cell["matrix"],
        "r_p": actor_rp,
        "r_d": actor_rd,
        "mutation": {
            key: mutation.get(key) for key in (
                "mutation_type", "superseded_root_id", "replacement_root_id",
                "contradiction", "supersession", "actor_current_EAdm", "cross_actor_dependency_leak",
                "cross_actor_state_change_leak", "hidden_truth_ingested",
                "evaluator_truth_before", "evaluator_truth_after",
                "evaluator_truth_before_digest", "evaluator_truth_after_digest",
                "evaluator_truth_changed", "evaluator_truth_authority_input",
                "evaluator_truth_precondition_input",
            )
        },
    }
    return hashlib.sha256(canonical_bytes(projection)).hexdigest()


def _valid_supersession(value: Any, actor: str) -> bool:
    return isinstance(value, Mapping) and set(value) == {
        "actor_id", "old_root_id", "new_root_id", "old_polarity", "new_polarity",
        "same_tracked_proposition", "old_revision", "new_revision", "supersedes",
        "old_root_current_after", "new_root_current", "visibility",
    } and all((
        value["actor_id"] == actor,
        isinstance(value["old_root_id"], str) and bool(value["old_root_id"]),
        isinstance(value["new_root_id"], str) and bool(value["new_root_id"]),
        value["old_polarity"] is True,
        value["new_polarity"] is False,
        value["same_tracked_proposition"] is True,
        isinstance(value["old_revision"], int),
        isinstance(value["new_revision"], int),
        value["new_revision"] > value["old_revision"],
        value["supersedes"] == [value["old_root_id"]],
        value["old_root_current_after"] is False,
        value["new_root_current"] is True,
        value["visibility"] == [actor],
    ))


def validate_k6_trace(trace: Mapping[str, Any], *, cell: K6CellSpec | None = None) -> dict[str, Any]:
    required = {
        "schema_version", "protocol_digest", "inventory_digest", "pairing_digest",
        "cell", "semantic_bindings",
        "r_p", "r_d", "r_e", "actor_scope", "mutation", "exact_action",
        "no_reconsideration", "s3", "mechanism_analysis",
    }
    if set(trace) != required:
        raise K6ContractError("K6 trace top-level schema mismatch")
    protocol = load_k6_protocol()
    if trace["schema_version"] != "minecraft-k6-cell-trace/1":
        raise K6ContractError("K6 trace schema version mismatch")
    if (not isinstance(trace["pairing_digest"], str) or len(trace["pairing_digest"]) != 64
            or any(character not in "0123456789abcdef" for character in trace["pairing_digest"])):
        raise K6ContractError("K6 trace pairing digest is malformed")
    if (trace["protocol_digest"] != protocol["validated_protocol_digest"]
            or trace["inventory_digest"] != protocol["validated_inventory_digest"]
            or trace["semantic_bindings"] != protocol["semantic_bindings"]):
        raise K6ContractError("K6 trace binding mismatch")
    cell_record = trace["cell"]
    if set(cell_record) != {
        "cell_id", "scenario_family", "inventory_id", "condition", "affected_actor", "matrix",
    }:
        raise K6ContractError("K6 trace cell schema mismatch")
    all_cells = {candidate.cell_id: candidate for candidate in build_k6_cells()}
    expected_cell = cell or all_cells.get(cell_record["cell_id"])
    if expected_cell is None or cell_record != {
        "cell_id": expected_cell.cell_id,
        "scenario_family": expected_cell.scenario_family,
        "inventory_id": expected_cell.inventory_id,
        "condition": expected_cell.condition,
        "affected_actor": expected_cell.affected_actor,
        "matrix": expected_cell.matrix,
    }:
        raise K6ContractError("K6 trace cell identity mismatch")
    inventory = {item.inventory_id: item for item in load_k6_inventory()}
    item = inventory[expected_cell.inventory_id]
    for phase, keys in _PHASE_KEYS.items():
        if not isinstance(trace[phase], Mapping) or set(trace[phase]) != keys:
            raise K6ContractError(f"K6 trace {phase} schema mismatch")
    if trace["r_p"]["EAdm"] is not True:
        raise K6ContractError("K6 action was not admissible at preparation")
    if any(trace["r_p"][name] != trace["r_e"][name] for name in EXACT_FIELDS):
        raise K6ContractError("K6 retained exact request identity changed")
    action = trace["r_p"]["action"]
    if action.get("identity") != item.action_identity or action.get("version") != item.action_version:
        raise K6ContractError("K6 trace action is outside the frozen inventory")
    if action.get("digest") != expected_action_digest(item):
        raise K6ContractError("K6 trace action digest does not match frozen classification")
    _validate_exact_request_digest(trace["r_p"])
    _validate_exact_request_digest(trace["r_e"])
    exact = trace["exact_action"]
    if exact != {"same_prepared_object": True, "exact_action_preserved": True}:
        raise K6ContractError("K6 prepared action was reconstructed or substituted")
    freeze = trace["no_reconsideration"]
    if set(freeze) != {
        "planner_instantiated", "model_instantiated", "controller_instantiated",
        "planner_calls", "model_calls", "controller_redecisions", "action_regenerations",
    } or any(freeze[name] is not False for name in (
        "planner_instantiated", "model_instantiated", "controller_instantiated",
    )) or any(freeze[name] != 0 for name in (
        "planner_calls", "model_calls", "controller_redecisions", "action_regenerations",
    )):
        raise K6ContractError("K6 no-reconsideration invariant failed")
    if trace["r_e"]["EnvPre_oracle"] is not True or trace["r_e"]["SecPre_oracle"] is not True:
        raise K6ContractError("K6 detached precondition oracle failed")
    mutation = trace["mutation"]
    if mutation.get("hidden_truth_ingested") is not False:
        raise K6ContractError("K6 hidden evaluator truth entered runtime evidence")
    if trace["actor_scope"] != {
        "actor_id": expected_cell.affected_actor,
        "visible_to": [expected_cell.affected_actor],
        "private_actor_scope": True,
    }:
        raise K6ContractError("K6 actor scope mismatch")
    if (trace["r_e"]["current_EAdm"] is not trace["r_d"]["current_EAdm"]
            or trace["r_e"]["exact_action_submitted"] is not True):
        raise K6ContractError("K6 effect-submission phase is inconsistent")
    family = expected_cell.scenario_family
    if family == "S1":
        if (mutation.get("mutation_type") != "opposite_polarity_explicit_supersession"
                or mutation.get("superseded_root_id") is None
                or mutation.get("replacement_root_id") is None
                or mutation.get("contradiction") is not None
                or not _valid_supersession(mutation.get("supersession"), expected_cell.affected_actor)
                or mutation["supersession"]["old_root_id"] != mutation["superseded_root_id"]
                or mutation["supersession"]["new_root_id"] != mutation["replacement_root_id"]):
            raise K6ContractError("K6 S1 supersession contract mismatch")
    elif family == "S2":
        contradiction = mutation.get("contradiction")
        if (mutation.get("mutation_type") != "independent_opposite_trusted_tool_result"
                or mutation.get("superseded_root_id") is not None
                or not isinstance(contradiction, Mapping)
                or contradiction != {
                    "positive_current": True, "negative_current": True,
                    "positive_supersedes": [], "negative_supersedes": [],
                    "non_defeated": False,
                }
                ):
            raise K6ContractError("K6 S2 unresolved contradiction contract mismatch")
    elif family == "S3" and (
        mutation.get("mutation_type") != "affected_actor_explicit_supersession"
        or not _valid_supersession(mutation.get("supersession"), expected_cell.affected_actor)
        or mutation["supersession"]["old_root_id"] != mutation.get("superseded_root_id")
        or mutation["supersession"]["new_root_id"] != mutation.get("replacement_root_id")
    ):
        raise K6ContractError("K6 S3 selective-revision contract mismatch")
    elif family == "C1":
        if (mutation.get("mutation_type") != "unrelated_weather_visible_update"
                or trace["r_d"]["authority_epoch"] <= trace["r_p"]["authority_epoch"]):
            raise K6ContractError("K6 C1 unrelated-revision contract mismatch")
    elif family == "C2":
        before = mutation.get("evaluator_truth_before")
        after = mutation.get("evaluator_truth_after")
        evidence_before = mutation.get("evidence_total_before")
        evidence_after = mutation.get("evidence_total_after")
        epochs = (
            mutation.get("authority_epoch_before"),
            mutation.get("authority_epoch_after"),
            trace["r_p"]["authority_epoch"],
            trace["r_d"]["authority_epoch"],
            trace["r_e"]["authority_epoch_before_execution"],
        )
        if (mutation.get("mutation_type") != "evaluator_only_hidden_truth_mutation"
                or not isinstance(before, Mapping)
                or not isinstance(after, Mapping)
                or canonical_bytes(before) == canonical_bytes(after)
                or mutation.get("evaluator_truth_changed") is not True
                or mutation.get("evaluator_truth_before_digest")
                != hashlib.sha256(canonical_bytes(before)).hexdigest()
                or mutation.get("evaluator_truth_after_digest")
                != hashlib.sha256(canonical_bytes(after)).hexdigest()
                or mutation.get("evaluator_truth_authority_input") is not False
                or mutation.get("evaluator_truth_precondition_input") is not False
                or any(isinstance(epoch, bool) or not isinstance(epoch, int) for epoch in epochs)
                or len(set(epochs)) != 1
                or isinstance(evidence_before, bool) or not isinstance(evidence_before, int)
                or isinstance(evidence_after, bool) or not isinstance(evidence_after, int)
                or evidence_before != evidence_after
                or trace["r_d"]["current_EAdm"] is not trace["r_p"]["EAdm"]
                or trace["r_d"]["permit_or_shadow_fresh"] is not True
                or trace["r_e"]["permit_or_shadow_fresh"] is not True):
            raise K6ContractError("K6 C2 hidden-truth contract mismatch")

    if family != "C2" and (
        any(mutation.get(field) is not None for field in C2_EVALUATOR_FIELDS[:4])
        or mutation.get("evaluator_truth_changed") is not False
        or mutation.get("evaluator_truth_authority_input") is not False
        or mutation.get("evaluator_truth_precondition_input") is not False
    ):
        raise K6ContractError("K6 non-C2 trace contains evaluator-only truth state")

    s3 = trace["s3"]
    if expected_cell.scenario_family == "S3":
        if not isinstance(s3, Mapping) or set(s3) != {
            "affected_actor", "unaffected_actor", "unaffected_current_EAdm",
            "unaffected_r_p", "unaffected_r_d", "unaffected_r_e",
            "unaffected_same_prepared_object", "unaffected_exact_action_preserved",
            "unaffected_mechanism_analysis",
            "cross_actor_dependency_leak", "cross_actor_state_change_leak",
        } or s3["affected_actor"] != expected_cell.affected_actor:
            raise K6ContractError("K6 S3 actor-isolation schema mismatch")
        expected_unaffected = "Bob" if expected_cell.affected_actor == "Alice" else "Alice"
        if s3["unaffected_actor"] != expected_unaffected:
            raise K6ContractError("K6 S3 unaffected actor identity mismatch")
        if set(s3["unaffected_r_p"]) != _PHASE_KEYS["r_p"]:
            raise K6ContractError("K6 S3 unaffected r_p schema mismatch")
        if set(s3["unaffected_r_d"]) != _PHASE_KEYS["r_d"]:
            raise K6ContractError("K6 S3 unaffected r_d schema mismatch")
        if set(s3["unaffected_r_e"]) != _PHASE_KEYS["r_e"]:
            raise K6ContractError("K6 S3 unaffected r_e schema mismatch")
        if (any(s3["unaffected_r_p"][name] != s3["unaffected_r_e"][name]
                for name in EXACT_FIELDS)
                or s3["unaffected_same_prepared_object"] is not True
                or s3["unaffected_exact_action_preserved"] is not True):
            raise K6ContractError("K6 S3 unaffected exact action changed")
        _validate_exact_request_digest(s3["unaffected_r_p"])
        _validate_exact_request_digest(s3["unaffected_r_e"])
        if (s3["unaffected_current_EAdm"] is not s3["unaffected_r_d"]["current_EAdm"]
                or s3["unaffected_r_e"]["current_EAdm"] is not s3["unaffected_r_d"]["current_EAdm"]
                or s3["unaffected_r_e"]["exact_action_submitted"] is not True
                or s3["unaffected_r_e"]["EnvPre_oracle"] is not True
                or s3["unaffected_r_e"]["SecPre_oracle"] is not True):
            raise K6ContractError("K6 S3 unaffected phase records are inconsistent")
        actor_states = mutation.get("actor_current_EAdm")
        if (not isinstance(actor_states, Mapping)
                or actor_states.get(expected_cell.affected_actor) is not trace["r_d"]["current_EAdm"]
                or actor_states.get(expected_unaffected) is not s3["unaffected_current_EAdm"]
                or mutation.get("cross_actor_dependency_leak")
                is not s3["cross_actor_dependency_leak"]
                or mutation.get("cross_actor_state_change_leak")
                is not s3["cross_actor_state_change_leak"]):
            raise K6ContractError("K6 S3 mutation and actor-isolation records disagree")
        _validate_mechanism_semantics(
            s3["unaffected_mechanism_analysis"], s3["unaffected_r_p"],
            s3["unaffected_r_d"], s3["unaffected_r_e"], expected_cell.condition)
    elif s3 is not None:
        raise K6ContractError("K6 non-S3 trace contains actor-isolation fields")
    _validate_mechanism_semantics(
        trace["mechanism_analysis"], trace["r_p"], trace["r_d"], trace["r_e"],
        expected_cell.condition)
    if trace["pairing_digest"] != trace_pairing_digest(trace):
        raise K6ContractError("K6 trace pairing digest does not match pre-enforcement construction")
    return dict(trace)


def _fraction(numerator: int, denominator: int) -> dict[str, int]:
    return {"numerator": numerator, "denominator": denominator}


def _metric_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    invalid_submissions = [
        row for row in rows
        if row["cell"]["scenario_family"] in PRIMARY_FAMILIES
        and row["r_d"]["current_EAdm"] is False
        and row["r_e"]["exact_action_submitted"] is True
    ]
    relevant = [row for row in rows if row["cell"]["scenario_family"] in PRIMARY_FAMILIES]
    unrelated = [row for row in rows if row["cell"]["scenario_family"] == "C1"]
    actor_scope = [row for row in rows if row["cell"]["scenario_family"] == "S3"]
    return {
        "post_admission_invalid_action_execution_rate": _fraction(
            sum(row["r_e"]["native_callable_reached"] is True for row in invalid_submissions),
            len(invalid_submissions),
        ),
        "relevant_revision_detection": _fraction(
            sum(row["r_p"]["EAdm"] is True and row["r_d"]["current_EAdm"] is False
                and row["r_d"]["permit_or_shadow_fresh"] is False for row in relevant),
            len(relevant),
        ),
        "unrelated_retention": _fraction(
            sum(row["r_d"]["current_EAdm"] is True
                and row["r_d"]["permit_or_shadow_fresh"] is True
                and row["r_e"]["permit_or_shadow_fresh"] is True
                and row["r_e"]["execution_allowed"] is True
                and row["r_e"]["native_callable_reached"] is True for row in unrelated),
            len(unrelated),
        ),
        "actor_scope_isolation": _fraction(
            sum(row["r_d"]["current_EAdm"] is False
                and row["s3"]["unaffected_current_EAdm"] is True
                and row["s3"]["cross_actor_dependency_leak"] is False
                and row["s3"]["cross_actor_state_change_leak"] is False
                for row in actor_scope),
            len(actor_scope),
        ),
        "cross_actor_dependency_leakage": _fraction(
            sum(row["s3"]["cross_actor_dependency_leak"] is True for row in actor_scope),
            len(actor_scope),
        ),
        "cross_actor_state_change_leakage": _fraction(
            sum(row["s3"]["cross_actor_state_change_leak"] is True for row in actor_scope),
            len(actor_scope),
        ),
    }


def _mechanism_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    records: list[tuple[str, str, str]] = []
    for row in rows:
        role = ("affected" if row["cell"]["scenario_family"] in PRIMARY_FAMILIES
                else "control")
        for model, artifact in row["mechanism_analysis"].items():
            records.append((model, role, artifact["decision"]))
        if row["s3"] is not None:
            for model, artifact in row["s3"]["unaffected_mechanism_analysis"].items():
                records.append((model, "unaffected", artifact["decision"]))
    result = {}
    for model in ("M0", "M1", "M2", "M3", "M4"):
        result[model] = {}
        for role in ("affected", "control", "unaffected"):
            decisions = [decision for observed_model, observed_role, decision in records
                         if observed_model == model and observed_role == role]
            result[model][role] = {
                "allow": _fraction(sum(decision == "allow" for decision in decisions), len(decisions)),
                "reject": _fraction(sum(decision == "reject" for decision in decisions), len(decisions)),
                "not_applicable": _fraction(
                    sum(decision == "not_applicable" for decision in decisions), len(decisions)),
            }
        affected = [decision for observed_model, observed_role, decision in records
                    if observed_model == model and observed_role == "affected"
                    and decision != "not_applicable"]
        c1 = [row["mechanism_analysis"][model]["decision"] for row in rows
              if row["cell"]["scenario_family"] == "C1"
              and row["mechanism_analysis"][model]["decision"] != "not_applicable"]
        unaffected = [decision for observed_model, observed_role, decision in records
                      if observed_model == model and observed_role == "unaffected"
                      and decision != "not_applicable"]
        result[model]["estimands"] = {
            "relevant_revision_detection": _fraction(
                sum(decision == "reject" for decision in affected), len(affected)),
            "unrelated_retention": _fraction(
                sum(decision == "allow" for decision in c1), len(c1)),
            "s3_unaffected_retention": _fraction(
                sum(decision == "allow" for decision in unaffected), len(unaffected)),
        }
    return result


def aggregate_k6_results(traces: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for trace in traces:
        row = validate_k6_trace(trace)
        cell_id = row["cell"]["cell_id"]
        if cell_id in seen:
            raise K6ContractError(f"duplicate K6 cell trace: {cell_id}")
        seen.add(cell_id)
        rows.append(row)
    rows.sort(key=lambda row: row["cell"]["cell_id"])

    pairs: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row["cell"]["scenario_family"], row["cell"]["inventory_id"],
               row["cell"]["affected_actor"], row["cell"]["matrix"])
        pairs.setdefault(key, []).append(row)
    for pair in pairs.values():
        if len(pair) == 2:
            if ({row["cell"]["condition"] for row in pair} != set(CONDITIONS)
                    or len({row["pairing_digest"] for row in pair}) != 1):
                raise K6ContractError("K6 Advisory/Authority pair construction mismatch")
        elif len(pair) > 2:
            raise K6ContractError("K6 condition pair contains duplicate traces")

    def grouped(field: str) -> dict[str, Any]:
        values = sorted({row["cell"][field] for row in rows})
        return {value: _metric_rows([row for row in rows if row["cell"][field] == value])
                for value in values}

    def mechanism_grouped(field: str) -> dict[str, Any]:
        values = sorted({row["cell"][field] for row in rows})
        return {value: _mechanism_rows([row for row in rows if row["cell"][field] == value])
                for value in values}

    observed_primary = sum(row["cell"]["matrix"] == "primary" for row in rows)
    observed_control = sum(row["cell"]["matrix"] == "control" for row in rows)
    return {
        "schema_version": "minecraft-k6-aggregate/1",
        "iid_samples": False,
        "inventory_census": True,
        "confidence_intervals_added": False,
        "p_values_added": False,
        "expected_primary_cells": 40,
        "observed_primary_cells": observed_primary,
        "expected_control_cells": 20,
        "observed_control_cells": observed_control,
        "complete": observed_primary == 40 and observed_control == 20 and len(rows) == 60,
        "verdict": None,
        "overall": _metric_rows(rows),
        "by_family": grouped("scenario_family"),
        "by_inventory": grouped("inventory_id"),
        "by_condition": grouped("condition"),
        "mechanism_isolation": {
            "overall": _mechanism_rows(rows),
            "by_family": mechanism_grouped("scenario_family"),
            "by_inventory": mechanism_grouped("inventory_id"),
            "by_condition": mechanism_grouped("condition"),
        },
    }
