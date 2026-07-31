from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any


VALIDATION_FILE = "matrix_run_validation.json"
SCANNER_ID = "villageragent.matrix-bundle-safety"
SCANNER_VERSION = "1"
SCANNER_SCHEMA_VERSION = 1
SCANNER_PATTERNS_VERSION = 1
_SCANNER_RULES = {
    "absolute_path_posix": r"(?<![A-Za-z0-9.])/(?:home|Users|tmp|var|opt|srv|root|mnt|media|workspace)(?:/[^\s\"'<>]*)?",
    "absolute_path_posix_quoted": r"[\"']/(?!/)(?:[^/\s\"'<>]+/)+[^\s\"'<>]*",
    "absolute_path_windows": r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/](?:[^\s\"'<>]+)",
    "absolute_path_unc": r"\\\\[^\\/\s]+[\\/][^\s\"'<>]+",
    "credential_assignment": r"(?i)(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret)\s*[:=]\s*[\"']?(?!\[REDACTED\]|<redacted>)[A-Za-z0-9_+./=-]{8,}",
    "credential_bearer": r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}=*",
    "credential_private_key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "credential_url_userinfo": r"(?i)https?://[^\s/@:]+:[^\s/@]+@",
    "credential_provider_token": r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b",
}
SCANNER_SHA256 = hashlib.sha256(
    json.dumps(_SCANNER_RULES, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


def scanner_implementation_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_COMPILED_RULES = {
    rule_id: re.compile(pattern) for rule_id, pattern in _SCANNER_RULES.items()
}
_SAFE_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


class MatrixRunValidationError(ValueError):
    """Raised when a judged run bundle cannot be safely inspected."""


def validate_matrix_run(
    run_or_experiment_dir: str | Path,
    *,
    run_name: str | None = None,
    parent_dir: str | Path | None = None,
    tolerance: float | None = None,
    expected_target: dict[str, float] | None = None,
    expected_completion_policy: str | None = None,
    expected_completion_semantics: str | None = None,
    expected_position_convention: str | None = None,
    expected_seed_contract: dict[str, Any] | None = None,
    write: bool = False,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate one successful judged run from a run or experiment directory.

    A finalized run is not modified by default. ``write=True`` supports a
    two-phase integration: validate the existing bundle, write the record, then
    re-finalize the run and parent manifests. ``output_path`` leaves the source
    bundle untouched.
    """
    run_dir, experiment_dir = _resolve_bundle(
        Path(run_or_experiment_dir), run_name=run_name, parent_dir=parent_dir
    )
    checks: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    def check(check_id: str, condition: bool, detail: str) -> None:
        checks.append({"id": check_id, "passed": bool(condition)})
        if not condition:
            errors.append({"check": check_id, "message": detail})

    summary = _read_object(run_dir / "summary.json")
    score = summary.get("final_score")
    score = score if isinstance(score, dict) else {}
    progress = summary.get("progress", score.get("progress", score.get("score")))
    check("score.success", score.get("status") == "success", "score status is not success")
    check("score.value_100", score.get("score") == 100, "score value is not 100")
    check(
        "score.progress_100",
        _is_progress_100(progress),
        "score progress is not 100 percent",
    )
    check("score.ownership", summary.get("score_ownership_verified") is True, "score ownership is not verified")

    dag = _read_object(run_dir / "runtime_dual_dag_snapshot.json")
    nodes = dag.get("nodes")
    dag_ok = (
        isinstance(nodes, list)
        and bool(nodes)
        and dag.get("summary", {}).get("terminal_state") == "success"
        and all(
            isinstance(node, dict)
            and node.get("lifecycle", {}).get("status") == "success"
            and not node.get("lifecycle", {}).get("active_agents")
            for node in nodes
        )
    )
    check("dag.success", dag_ok, "runtime DAG is not terminally successful")

    child = summary.get("child_protocol")
    child_ok = isinstance(child, dict) and child.get("status") == "completed" and child.get("result_valid") is True and child.get("result_written") is True
    check("child_protocol.completed_valid", child_ok, "child protocol is not completed and valid")

    actions = _read_object(run_dir / "action_log.json")
    action_entries = [
        action
        for agent, agent_actions in actions.items()
        if agent != "_attempt_id" and isinstance(agent_actions, list)
        for action in agent_actions
        if isinstance(action, dict)
    ]
    metrics = _read_object(run_dir / "metrics.json")
    failed_actions = sum(
        isinstance(action.get("result"), dict)
        and action["result"].get("status") is False
        for action in action_entries
    )
    check("actions.present", bool(action_entries), "action log contains no actions")
    check("actions.count", metrics.get("action_count") == len(action_entries), "action count does not match action log")
    check("actions.failed_count", metrics.get("failed_action_count") == failed_actions, "failed action count does not match action log")

    movement = _movement_evidence(action_entries, score)
    effective_tolerance = tolerance if tolerance is not None else movement.get("target_tolerance")
    movement_ok = (
        movement.get("completion_policy") == (expected_completion_policy or "strict_per_axis")
        and movement.get("completion_semantics") == (expected_completion_semantics or "all_axis_deltas_strictly_below_tolerance")
        and movement.get("target_reached") is True
        and _strict_axis_deltas(movement.get("axis_delta"), effective_tolerance)
    )
    check("movement.strict_per_axis", movement_ok, "movement completion is not strict per-axis success")
    if expected_position_convention is not None:
        check(
            "movement.position_convention",
            movement.get("position_convention") == expected_position_convention,
            "movement position convention does not match the matrix premanifest",
        )
    if expected_target is not None:
        score_target = score.get("expected_terminal_state", {}).get("player_position")
        check(
            "movement.expected_target",
            _same_target(score_target, expected_target),
            "judged target does not match the matrix premanifest",
        )

    seed_resolution = summary.get("seed_contract")
    if expected_seed_contract is not None:
        check(
            "seed.applied",
            _seed_contract_matches(seed_resolution, expected_seed_contract),
            "runtime seed contract does not match the matrix premanifest",
        )

    diagnostics = _read_object(run_dir / "judged_terminal_diagnostics.json")
    trace = _read_object(run_dir / "judged_iteration_trace.json")
    check("diagnostics.schema_2", diagnostics.get("schema_version") == 2, "terminal diagnostics schema is not 2")
    if expected_position_convention is not None:
        score_expected_convention = score.get("expected_terminal_state", {}).get(
            "position_convention"
        )
        score_actual_convention = score.get("actual_terminal_state", {}).get(
            "position_convention"
        )
        diagnostics_expected_convention = diagnostics.get(
            "expected_terminal_state", {}
        ).get("position_convention")
        diagnostics_actual_convention = diagnostics.get(
            "actual_terminal_state", {}
        ).get("position_convention")
        check(
            "score.position_convention",
            score_expected_convention == expected_position_convention
            and score_actual_convention == expected_position_convention,
            "score position convention evidence is missing or inconsistent",
        )
        check(
            "diagnostics.position_convention",
            diagnostics_expected_convention == expected_position_convention
            and diagnostics_actual_convention == expected_position_convention,
            "terminal diagnostics position convention is missing or inconsistent",
        )
        check(
            "position_convention.consistent",
            {
                movement.get("position_convention"),
                score_expected_convention,
                score_actual_convention,
                diagnostics_expected_convention,
                diagnostics_actual_convention,
            }
            == {expected_position_convention},
            "position convention differs across runtime evidence",
        )
    check(
        "diagnostics.terminal_success",
        diagnostics.get("score_status") in (None, "success")
        and (diagnostics.get("progress") is None or _is_progress_100(diagnostics.get("progress"))),
        "terminal diagnostics contradict successful score/progress",
    )
    diag_agent = diagnostics.get("agent_iteration")
    trace_agent = trace.get("agent_iteration")
    agent_ok = isinstance(diag_agent, dict) and diag_agent == trace_agent and _agent_iteration_valid(diag_agent)
    if agent_ok and diag_agent.get("available") is True and trace.get("outer_episode_count") is not None:
        agent_ok = diag_agent.get("used") == trace.get("outer_episode_count")
    check("iterations.agent_consistent", agent_ok, "agent iteration metadata is inconsistent")
    judger = diagnostics.get("judger_iteration")
    judger_ok = _judger_iteration_valid(judger)
    check("iterations.judger_available", judger_ok, "judger availability invariants are invalid")
    check(
        "iterations.owners_independent",
        not _cross_owner_inference(diag_agent, judger),
        "agent iteration metadata was used as judger evidence",
    )

    admission = summary.get("artifact_admission")
    admission_ok = (
        isinstance(admission, dict)
        and admission.get("passed") is True
        and admission.get("missing") in (None, [])
        and admission.get("invalid") in (None, [])
    )
    check("artifact_admission.passed", admission_ok, "artifact admission did not pass")

    cleanup = summary.get("bridge_cleanup")
    processes = cleanup.get("processes") if isinstance(cleanup, dict) else None
    cleanup_ok = (
        isinstance(cleanup, dict)
        and cleanup.get("cleanup_complete") is True
        and isinstance(processes, dict)
        and all(isinstance(value, dict) and value.get("alive_after_kill") is not True for value in processes.values())
    )
    check("cleanup.complete", cleanup_ok, "bridge cleanup is incomplete")
    check("target.reusable", summary.get("runtime_target_safe_to_reuse") is True, "runtime target is not safe to reuse")
    check(
        "target.not_quarantined",
        summary.get("runtime_target_quarantined") is False
        and summary.get("server_lock_quarantine_detected") is not True
        and not summary.get("runtime_target_quarantine"),
        "runtime target is quarantined",
    )

    run_manifest, run_manifest_errors = _validate_manifest(run_dir)
    if summary.get("attempt_id") != run_manifest.get("attempt_id"):
        run_manifest_errors.append("summary and run manifest attempt IDs differ")
    check("manifest.run", not run_manifest_errors, "; ".join(run_manifest_errors))
    parent_manifest: dict[str, Any] = {}
    parent_manifest_errors: list[str] = []
    if experiment_dir is not None:
        parent_manifest, parent_manifest_errors = _validate_manifest(experiment_dir)
    else:
        parent_manifest_errors.append("experiment directory was not provided or discovered")
    check("manifest.experiment", not parent_manifest_errors, "; ".join(parent_manifest_errors))

    findings = _scan_manifested_bundle(run_dir, run_manifest)
    files_scanned = len(run_manifest.get("artifacts", []))
    if experiment_dir is not None:
        findings.extend(_scan_manifested_bundle(experiment_dir, parent_manifest))
        files_scanned += len(parent_manifest.get("artifacts", []))
    findings = sorted(findings, key=lambda item: (item["bundle"], item["artifact"], item["rule"]))
    check("bundle_scan.clean", not findings, "manifested bundle contains unsafe content")

    attempt_id = str(summary.get("attempt_id") or run_manifest.get("attempt_id") or "")
    record_run_name = str(summary.get("run_name") or run_dir.name)
    if not _SAFE_IDENTITY.fullmatch(record_run_name) or not _SAFE_IDENTITY.fullmatch(attempt_id):
        raise MatrixRunValidationError("run_name and attempt_id must be safe portable identifiers")
    record = {
        "schema_version": 1,
        "record_type": "minecraft_matrix_run_validation",
        "run_name": record_run_name,
        "attempt_id": attempt_id,
        "passed": not errors,
        "checks": checks,
        "errors": errors,
        "observed": {
            "score": score.get("score"),
            "progress": progress,
            "end_reason": score.get("end_reason"),
            "action_count": len(action_entries),
            "failed_action_count": failed_actions,
            "agent_iteration": diag_agent,
            "judger_iteration": judger,
            "seed_contract": seed_resolution,
            "position_convention": movement.get("position_convention"),
        },
        "scanner": {
            "identity": SCANNER_ID,
            "version": SCANNER_VERSION,
            "schema_version": SCANNER_SCHEMA_VERSION,
            "implementation_sha256": scanner_implementation_sha256(),
            "patterns_version": SCANNER_PATTERNS_VERSION,
            "rules_sha256": SCANNER_SHA256,
            "files_scanned": files_scanned,
            "findings": findings,
        },
        "manifests": {
            "run": {
                "path": "artifact_manifest.json",
                "sha256": _sha256(run_dir / "artifact_manifest.json"),
                "valid": not run_manifest_errors,
            },
            "experiment": (
                {
                    "path": "artifact_manifest.json",
                    "sha256": _sha256(experiment_dir / "artifact_manifest.json"),
                    "valid": not parent_manifest_errors,
                }
                if experiment_dir is not None
                else None
            ),
        },
    }
    if write or output_path is not None:
        destination = Path(output_path) if output_path is not None else run_dir / VALIDATION_FILE
        _write_json(destination, record)
    return record


def write_matrix_run_validation(
    run_or_experiment_dir: str | Path,
    *,
    output_path: str | Path | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return validate_matrix_run(
        run_or_experiment_dir, write=output_path is None, output_path=output_path, **kwargs
    )


validate_run_bundle = validate_matrix_run
validate_matrix_run_bundle = validate_matrix_run


def _resolve_bundle(path: Path, *, run_name: str | None, parent_dir: str | Path | None) -> tuple[Path, Path | None]:
    judged_run = path / "minecraft_judged_meta"
    judged_parent = path / "minecraft_judged_smoke"
    if (judged_run / "summary.json").is_file() and (judged_parent / "artifact_manifest.json").is_file():
        return judged_run, judged_parent
    if run_name is not None:
        candidates = (path / "runs" / run_name, path / run_name)
        run_dir = next((item for item in candidates if (item / "summary.json").is_file()), candidates[0])
        return run_dir, path
    if (path / "summary.json").is_file():
        if parent_dir is not None:
            return path, Path(parent_dir)
        parent = path.parent.parent if path.parent.name == "runs" else path.parent
        return path, parent if (parent / "artifact_manifest.json").is_file() else None
    runs = path / "runs"
    found = sorted(item for item in runs.iterdir() if item.is_dir() and (item / "summary.json").is_file()) if runs.is_dir() else []
    if len(found) != 1:
        raise MatrixRunValidationError("experiment directory requires run_name unless it contains exactly one run")
    return found[0], path


def _validate_manifest(root: Path) -> tuple[dict[str, Any], list[str]]:
    path = root / "artifact_manifest.json"
    try:
        manifest = _read_object(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {}, [f"invalid artifact manifest: {type(exc).__name__}"]
    errors: list[str] = []
    entries = manifest.get("artifacts")
    if manifest.get("status") != "completed":
        errors.append("manifest status is not completed")
    try:
        attempt = _read_object(root / "attempt.json")
        if attempt.get("attempt_id") != manifest.get("attempt_id"):
            errors.append("attempt and manifest IDs differ")
    except (OSError, ValueError, json.JSONDecodeError):
        errors.append("attempt metadata is missing or invalid")
    if not isinstance(entries, list) or not entries:
        return manifest, errors + ["manifest has no artifacts"]
    seen: set[str] = set()
    for entry in entries:
        relative = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(relative, str) or not _safe_relative_path(relative) or relative in seen:
            errors.append("manifest contains an invalid or duplicate path")
            continue
        seen.add(relative)
        artifact = root / PurePosixPath(relative)
        try:
            if artifact.is_symlink() or not artifact.is_file() or not artifact.resolve().is_relative_to(root.resolve()):
                errors.append(f"invalid artifact: {relative}")
            elif artifact.stat().st_size != entry.get("size") or _sha256(artifact) != entry.get("sha256"):
                errors.append(f"artifact identity mismatch: {relative}")
        except OSError:
            errors.append(f"unreadable artifact: {relative}")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and path not in {root / "artifact_manifest.json", root / "_COMPLETED"}
    }
    if seen != actual:
        errors.append("manifest membership does not match bundle files")
    marker = root / "_COMPLETED"
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != str(manifest.get("attempt_id") or ""):
        errors.append("completion marker is missing or invalid")
    return manifest, errors


def _scan_manifested_bundle(root: Path, manifest: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for entry in manifest.get("artifacts", []) if isinstance(manifest, dict) else []:
        relative = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(relative, str) or not _safe_relative_path(relative):
            continue
        path = root / PurePosixPath(relative)
        try:
            text = path.read_bytes().decode("utf-8", errors="ignore")
        except OSError:
            continue
        for rule_id in scan_text(text):
            findings.append({"bundle": root.name, "artifact": relative, "rule": rule_id})
    return sorted(findings, key=lambda item: (item["artifact"], item["rule"]))


def scan_text(text: str) -> list[str]:
    """Return stable safety rule IDs without retaining sensitive matches."""
    return sorted(
        rule_id for rule_id, pattern in _COMPILED_RULES.items() if pattern.search(text)
    )


def _movement_evidence(actions: list[dict[str, Any]], score: dict[str, Any]) -> dict[str, Any]:
    for action in reversed(actions):
        result = action.get("result")
        if isinstance(result, dict) and any(key in result for key in ("completion_policy", "axis_delta", "target_reached")):
            return result
    for key in ("movement_completion", "actual_terminal_state"):
        candidate = score.get(key)
        if isinstance(candidate, dict):
            return candidate
    return {}


def _strict_axis_deltas(value: Any, tolerance: Any) -> bool:
    if not isinstance(value, dict) or isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance <= 0:
        return False
    return all(
        isinstance(value.get(axis), (int, float))
        and not isinstance(value.get(axis), bool)
        and math.isfinite(value[axis])
        and 0 <= value[axis] < tolerance
        for axis in ("x", "y", "z")
    )


def _same_target(actual: Any, expected: dict[str, float]) -> bool:
    return isinstance(actual, dict) and all(
        isinstance(actual.get(axis), (int, float))
        and not isinstance(actual.get(axis), bool)
        and actual[axis] == expected.get(axis)
        for axis in ("x", "y", "z")
    )


def _seed_contract_matches(actual: Any, expected: dict[str, Any]) -> bool:
    if not isinstance(actual, dict) or actual.get("seed") != expected.get("seed"):
        return False
    requested = expected.get("requested_scopes")
    if not isinstance(requested, list) or actual.get("requested_scopes") != requested:
        return False
    scopes = actual.get("scopes")
    return isinstance(scopes, dict) and all(
        isinstance(scopes.get(scope), dict)
        and scopes[scope].get("requested") is True
        and scopes[scope].get("supported") is True
        and scopes[scope].get("applied") is True
        for scope in requested
    )


def _iteration_valid(value: Any, *, usage_required: bool) -> bool:
    if not isinstance(value, dict) or value.get("available") is not True:
        return False
    used, limit = value.get("used"), value.get("limit")
    if usage_required and (not isinstance(used, int) or isinstance(used, bool) or used < 0):
        return False
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0 or used > limit):
        return False
    return isinstance(value.get("source"), str) and bool(value["source"])


def _agent_iteration_valid(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("available") is True:
        return _iteration_valid(value, usage_required=True)
    if value.get("available") is not False or value.get("used") is not None:
        return False
    reason = value.get("reason", value.get("usage_unavailable_reason"))
    reason_ok = (isinstance(reason, str) and bool(reason)) or (
        isinstance(reason, dict) and isinstance(reason.get("code"), str) and bool(reason["code"])
    )
    return reason_ok


def _judger_iteration_valid(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    used = value.get("used")
    usage_available = value.get("usage_available")
    reason = value.get("usage_unavailable_reason")
    if usage_available is True:
        if not isinstance(used, int) or isinstance(used, bool) or used < 0 or reason is not None:
            return False
    elif usage_available is False:
        if used is not None or not isinstance(reason, dict) or not reason.get("code"):
            return False
    else:
        return False
    for availability, field in (
        ("source_available", "source"),
        ("limit_available", "limit"),
        ("terminal_observations_available", "terminal_observations"),
    ):
        if value.get(availability) is not (value.get(field) is not None):
            return False
    if value.get("available") is not any(
        value.get(field) is not None
        for field in ("source", "limit", "used", "terminal_observations")
    ):
        return False
    limit = value.get("limit")
    if limit is not None and (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or limit <= 0
        or (used is not None and used > limit)
    ):
        return False
    return value.get("owner") == "external_meta_judger"


def _cross_owner_inference(agent: Any, judger: Any) -> bool:
    if not isinstance(agent, dict) or not isinstance(judger, dict):
        return False
    provenance_fields = (
        "usage_source", "usage_provenance", "provenance", "source_owner",
        "derived_from", "inferred_from",
    )
    values = [judger.get(field) for field in provenance_fields]
    if judger.get("usage_available") is True:
        values.append(judger.get("source"))
    encoded = json.dumps(values, sort_keys=True, default=str).lower()
    return re.search(r"\bagent\b|agent[_-]", encoded) is not None


def _is_progress_100(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value in (1, 100)


def _safe_relative_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and "\\" not in value


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise MatrixRunValidationError(f"expected JSON object: {path.name}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
