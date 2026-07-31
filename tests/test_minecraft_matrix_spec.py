import copy
import hashlib
import json
import subprocess
from dataclasses import FrozenInstanceError, replace

import pytest

from benchmarks.minecraft.matrix_spec import (
    finalize_matrix_spec,
    matrix_spec_sha256,
    matrix_spec_to_dict,
    parse_matrix_spec,
    validate_matrix_spec,
)
from benchmarks.minecraft.matrix_variants import (
    MOVEMENT_VARIANTS,
    VARIANT_ORDER,
    get_movement_variant,
)
from benchmarks.minecraft.matrix_validation import (
    SCANNER_ID,
    SCANNER_PATTERNS_VERSION,
    SCANNER_SCHEMA_VERSION,
    SCANNER_SHA256,
    scanner_implementation_sha256,
)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _payload(tmp_path):
    first = tmp_path / "baseline-a.tar.gz"
    second = tmp_path / "baseline-b.tar.gz"
    first.write_bytes(b"approved baseline A")
    second.write_bytes(b"approved baseline B")
    revision = _init_git(tmp_path)
    baselines = [
        {"baseline_id": "a", "path": first.name, "sha256": _sha(first)},
        {"baseline_id": "b", "path": second.name, "sha256": _sha(second)},
    ]
    runs = []
    for variant_id in VARIANT_ORDER:
        variant = get_movement_variant(variant_id)
        for seed in (17, 29):
            for baseline in baselines:
                order = len(runs)
                runs.append({
                    "order": order,
                    "run_id": f"{variant_id}-{seed}-{baseline['baseline_id']}",
                    "variant": variant_id,
                    "seed": seed,
                    "baseline_id": baseline["baseline_id"],
                    "snapshot_path": baseline["path"],
                    "snapshot_sha256": baseline["sha256"],
                    "prompt": variant.prompt,
                    "initial_state": variant.initial_position.as_dict(),
                    "evaluation_target": variant.target.as_dict(),
                    "position_convention": variant.position_convention,
                    "expected_completion_policy": variant.completion_policy,
                    "expected_completion_semantics": variant.completion_semantics,
                    "target_tolerance": variant.tolerance,
                    "variant_definition_sha256": variant.definition_sha256,
                    "seed_scopes": {
                        "requested": ["meta_judger"],
                        "supported": ["meta_judger"],
                        "applied": ["meta_judger"],
                    },
                })
    return {
        "schema_version": 2,
        "matrix_id": "minecraft-judged-test-matrix",
        "lifecycle_state": "draft",
        "premanifest_sha256": None,
        "revision": revision,
        "seeds": [17, 29],
        "baselines": baselines,
        "runtime": {
            "name": "minecraft-runtime",
            "image": "registry.example/runtime:matrix",
            "digest": "sha256:" + "1" * 64,
        },
        "model": {"provider": "ollama", "name": "model:tag", "digest": "2" * 64},
        "scanner": {
            "name": SCANNER_ID,
            "schema_version": SCANNER_SCHEMA_VERSION,
            "implementation_sha256": scanner_implementation_sha256(),
            "patterns_version": SCANNER_PATTERNS_VERSION,
            "patterns_sha256": SCANNER_SHA256,
        },
        "generation": {
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 2048,
            "timeout_seconds": 600.0,
            "max_iterations": 20,
        },
        "execution": {
            "mode": "sequential",
            "stop_on_first_failure": True,
            "retry_policy": "none",
        },
        "runs": runs,
    }


def _validate(payload, _tmp_path):
    return validate_matrix_spec(parse_matrix_spec(payload), repo_root=_tmp_path)


def _init_git(root):
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, check=True, capture_output=True, text=True
    ).stdout
    if status:
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixtures"],
            cwd=root,
            check=True,
        )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_parse_validate_finalize_and_verify_canonical_self_hash(tmp_path):
    payload = _payload(tmp_path)

    parsed = parse_matrix_spec(json.dumps(payload))
    validated = validate_matrix_spec(parsed, repo_root=tmp_path)
    finalized = finalize_matrix_spec(validated, repo_root=tmp_path)

    assert parsed.lifecycle_state == "draft"
    assert validated.lifecycle_state == "validated"
    assert finalized.lifecycle_state == "finalized"
    assert finalized.premanifest_sha256 == matrix_spec_sha256(finalized)
    assert validate_matrix_spec(finalized, repo_root=tmp_path) is finalized
    with pytest.raises(FrozenInstanceError):
        finalized.revision = "0" * 40
    with pytest.raises(TypeError):
        MOVEMENT_VARIANTS["near"] = MOVEMENT_VARIANTS["diagonal"]


def test_finalized_matrix_rejects_drift_and_draft_cannot_finalize(tmp_path):
    draft = parse_matrix_spec(_payload(tmp_path))
    with pytest.raises(ValueError, match="validated before finalization"):
        finalize_matrix_spec(draft, repo_root=tmp_path)
    finalized = finalize_matrix_spec(
        validate_matrix_spec(draft, repo_root=tmp_path), repo_root=tmp_path
    )
    drifted = matrix_spec_to_dict(finalized)
    drifted["generation"]["max_tokens"] += 1

    with pytest.raises(ValueError, match="hash mismatch|drifted"):
        _validate(drifted, tmp_path)


def test_missing_second_baseline_fails_closed(tmp_path):
    payload = _payload(tmp_path)
    payload["baselines"] = payload["baselines"][:1]

    with pytest.raises(ValueError, match="exactly two baselines"):
        _validate(payload, tmp_path)


def test_baselines_with_same_sha_identity_fail_closed(tmp_path):
    payload = _payload(tmp_path)
    payload["baselines"][1]["sha256"] = payload["baselines"][0]["sha256"]

    with pytest.raises(ValueError, match="genuinely distinct"):
        _validate(payload, tmp_path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p.update(schema_version=1), "schema version"),
        (lambda p: p["runs"][0].pop("position_convention"), "schema mismatch"),
        (lambda p: p["runs"][0].update(position_convention="support_block"), "movement contract"),
        (lambda p: p["runs"].pop(), "exactly 12 runs"),
        (lambda p: p["runs"][1].update(run_id=p["runs"][0]["run_id"]), "IDs must be unique"),
        (lambda p: p["runs"].reverse(), "explicitly ordered"),
        (lambda p: p["runs"][0].update(variant="multi_step"), "explicitly ordered"),
        (lambda p: p["runs"][0]["evaluation_target"].update(x=float("inf")), "finite"),
        (lambda p: p["runs"][0].update(prompt="Move somewhere else"), "movement contract"),
        (lambda p: p["runs"][0].update(variant_definition_sha256="0" * 64), "definition hash"),
        (lambda p: p["runs"][0]["seed_scopes"].update(applied=[]), "exactly the applied"),
        (lambda p: p["execution"].update(retry_policy="automatic"), "retry policy none"),
        (lambda p: p["execution"].update(stop_on_first_failure=False), "stop on first failure"),
        (lambda p: p["runtime"].update(digest=p["runtime"]["image"]), "SHA-256 digest"),
        (lambda p: p["model"].update(digest=""), "model digest"),
        (lambda p: p["scanner"].update(implementation_sha256=""), "scanner implementation sha256"),
    ],
)
def test_matrix_contract_rejects_invalid_specs(tmp_path, mutate, message):
    payload = _payload(tmp_path)
    mutate(payload)

    with pytest.raises(ValueError, match=message):
        _validate(payload, tmp_path)


def test_snapshot_and_revision_identity_are_verified(tmp_path):
    missing = _payload(tmp_path)
    missing_path = "missing.tar.gz"
    missing["baselines"][0]["path"] = missing_path
    missing["runs"][0]["snapshot_path"] = missing_path
    missing["runs"][1]["snapshot_path"] = missing_path
    missing["runs"][4]["snapshot_path"] = missing_path
    missing["runs"][5]["snapshot_path"] = missing_path
    missing["runs"][8]["snapshot_path"] = missing_path
    missing["runs"][9]["snapshot_path"] = missing_path
    with pytest.raises(ValueError, match="does not exist"):
        _validate(missing, tmp_path)

    stale = _payload(tmp_path)
    stale["revision"] = "0" * 40
    with pytest.raises(ValueError, match="does not match current HEAD"):
        _validate(stale, tmp_path)


def test_unknown_variant_is_rejected_directly():
    with pytest.raises(ValueError, match="unknown movement variant"):
        get_movement_variant("multi_step")


def test_variants_have_fixed_in_arena_targets_and_final_target_semantics():
    assert MOVEMENT_VARIANTS["near"].target.as_dict() == {"x": 10, "y": -59, "z": 5}
    assert MOVEMENT_VARIANTS["diagonal"].target.as_dict() == {"x": 5, "y": -60, "z": 5}
    assert MOVEMENT_VARIANTS["long_distance"].target.as_dict() == {"x": 20, "y": -60, "z": 18}
    assert all(
        variant.evaluation == "final_position_strict_per_axis"
        and variant.completion_policy == "strict_per_axis"
        and variant.position_convention == "entity_feet"
        for variant in MOVEMENT_VARIANTS.values()
    )
    diagonal = MOVEMENT_VARIANTS["diagonal"]
    assert replace(
        diagonal, position_convention="support_block"
    ).definition_sha256 != diagonal.definition_sha256


def test_input_payload_is_not_retained_mutably(tmp_path):
    payload = _payload(tmp_path)
    spec = parse_matrix_spec(payload)
    original = copy.deepcopy(matrix_spec_to_dict(spec))
    payload["runs"][0]["seed_scopes"]["applied"].clear()

    assert matrix_spec_to_dict(spec) == original
