import hashlib
import json
from pathlib import Path
import shutil

import pytest

from benchmarks.minecraft.matrix_validation import (
    SCANNER_ID,
    SCANNER_SHA256,
    validate_matrix_run,
)


def test_validates_successful_manifested_bundle(tmp_path):
    matrix, run = _bundle(tmp_path)

    record = validate_matrix_run(matrix, run_name="run-b")

    assert record["passed"] is True
    assert record["observed"]["score"] == 100
    assert record["observed"]["progress"] == 100
    assert record["observed"]["action_count"] == 1
    assert record["observed"]["failed_action_count"] == 0
    assert record["scanner"]["identity"] == SCANNER_ID
    assert record["scanner"]["rules_sha256"] == SCANNER_SHA256
    assert not (run / "matrix_run_validation.json").exists()


@pytest.mark.parametrize(
    ("mutate", "failed_check"),
    [
        (lambda files: files["summary"]["final_score"].update(status="failed"), "score.success"),
        (lambda files: files["summary"].update(progress=99), "score.progress_100"),
        (lambda files: files["dag"]["summary"].update(terminal_state="failed"), "dag.success"),
        (lambda files: files["summary"]["child_protocol"].update(result_valid=False), "child_protocol.completed_valid"),
        (lambda files: files["metrics"].update(action_count=2), "actions.count"),
        (lambda files: files["movement"].update(completion_policy="euclidean_distance"), "movement.strict_per_axis"),
        (lambda files: files["movement"]["axis_delta"].update(x=1.0), "movement.strict_per_axis"),
        (lambda files: files["terminal"].update(schema_version=1), "diagnostics.schema_2"),
        (lambda files: files["terminal"]["agent_iteration"].update(used=2), "iterations.agent_consistent"),
        (lambda files: files["summary"]["artifact_admission"].update(passed=False), "artifact_admission.passed"),
        (lambda files: files["summary"]["bridge_cleanup"].update(cleanup_complete=False), "cleanup.complete"),
        (lambda files: files["summary"].update(runtime_target_quarantined=True), "target.not_quarantined"),
    ],
)
def test_reports_required_failure_cases(tmp_path, mutate, failed_check):
    matrix, _ = _bundle(tmp_path, mutate=mutate)

    record = validate_matrix_run(matrix, run_name="run-b")

    assert record["passed"] is False
    assert failed_check in {error["check"] for error in record["errors"]}


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "/home/alice/private/result.json",
        r"C:\\Users\\alice\\result.json",
        r"\\\\server\\share\\result.json",
        "api_key=supersecretvalue",
        "Authorization: Bearer abcdefghijklmnop",
    ],
)
def test_scans_entire_parent_manifest_without_leaking_match(tmp_path, unsafe_text):
    matrix, _ = _bundle(tmp_path, extra_text=unsafe_text)

    record = validate_matrix_run(matrix, run_name="run-b")

    assert record["passed"] is False
    assert record["scanner"]["findings"]
    serialized = json.dumps(record)
    assert unsafe_text not in serialized
    assert str(tmp_path) not in serialized


def test_writes_record_for_two_phase_manifest_refinalization(tmp_path):
    matrix, run = _bundle(tmp_path)

    record = validate_matrix_run(matrix, run_name="run-b", write=True)

    assert json.loads((run / "matrix_run_validation.json").read_text()) == record


def test_validates_real_smoke_sibling_bundles_independently(tmp_path):
    _, run = _bundle(tmp_path)
    root = tmp_path / "judged-output"
    shutil.copytree(run, root / "minecraft_judged_meta")
    parent = root / "minecraft_judged_smoke"
    parent.mkdir()
    (parent / "attempt.json").write_text(
        json.dumps({"attempt_id": "smoke-attempt"}), encoding="utf-8"
    )
    (parent / "verification.json").write_text("{}", encoding="utf-8")
    _finalize_manifest(parent, "smoke-attempt")

    record = validate_matrix_run(root)

    assert record["passed"] is True
    assert record["scanner"]["files_scanned"] > 0
    assert record["manifests"]["run"]["valid"] is True
    assert record["manifests"]["experiment"]["valid"] is True


def test_can_write_deterministic_record_outside_finalized_run(tmp_path):
    matrix, _ = _bundle(tmp_path)
    output = tmp_path / "validation.json"

    first = validate_matrix_run(matrix, run_name="run-b", output_path=output)
    first_bytes = output.read_bytes()
    second = validate_matrix_run(matrix, run_name="run-b", output_path=output)

    assert first == second
    assert output.read_bytes() == first_bytes


def test_agent_iteration_may_be_explicitly_unavailable(tmp_path):
    def unavailable(files):
        value = {"available": False, "used": None, "reason": {"code": "not_recorded"}}
        files["terminal"]["agent_iteration"] = value
        files["trace"]["agent_iteration"] = value
        files["trace"]["outer_episode_count"] = None

    matrix, _ = _bundle(tmp_path, mutate=unavailable)

    record = validate_matrix_run(matrix, run_name="run-b")

    assert record["passed"] is True


def test_unavailable_judger_usage_does_not_infer_from_known_agent(tmp_path):
    def unavailable(files):
        judger = files["terminal"]["judger_iteration"]
        judger.update(
            used=None,
            usage_available=False,
            usage_unavailable_reason={"code": "not_observed"},
            source=files["terminal"]["agent_iteration"]["source"],
        )

    matrix, _ = _bundle(tmp_path, mutate=unavailable)

    record = validate_matrix_run(matrix, run_name="run-b")

    assert record["passed"] is True


def test_rejects_explicit_agent_derived_judger_usage(tmp_path):
    def inferred(files):
        files["terminal"]["judger_iteration"]["usage_provenance"] = "agent_iteration"

    matrix, _ = _bundle(tmp_path, mutate=inferred)

    record = validate_matrix_run(matrix, run_name="run-b")

    assert "iterations.owners_independent" in {item["check"] for item in record["errors"]}


@pytest.mark.parametrize(
    ("mutate", "check_id"),
    [
        (lambda files: files["movement"].pop("position_convention"), "movement.position_convention"),
        (
            lambda files: files["summary"]["final_score"][
                "actual_terminal_state"
            ].update(position_convention="support_block"),
            "score.position_convention",
        ),
        (
            lambda files: files["terminal"]["actual_terminal_state"].pop(
                "position_convention"
            ),
            "diagnostics.position_convention",
        ),
    ],
)
def test_matrix_position_convention_evidence_fails_closed(tmp_path, mutate, check_id):
    matrix, _ = _bundle(tmp_path, mutate=mutate)

    record = validate_matrix_run(
        matrix,
        run_name="run-b",
        expected_position_convention="entity_feet",
    )

    assert check_id in {item["check"] for item in record["errors"]}


def _bundle(tmp_path, *, mutate=None, extra_text=None):
    matrix = tmp_path / "matrix"
    run = matrix / "runs" / "run-b"
    run.mkdir(parents=True)
    movement = {
        "status": True,
        "completion_policy": "strict_per_axis",
        "completion_semantics": "all_axis_deltas_strictly_below_tolerance",
        "position_convention": "entity_feet",
        "target_reached": True,
        "target_tolerance": 1.0,
        "axis_delta": {"x": 0.1, "y": 0.2, "z": 0.3},
    }
    agent_iteration = {
        "available": True,
        "source": "Alice_history.json outer dict episodes",
        "limit": 10,
        "limit_source": "runtime",
        "limit_available": True,
        "used": 1,
    }
    judger_iteration = {
        "available": True,
        "owner": "external_meta_judger",
        "source": "Alice_history.json outer episode count",
        "source_available": True,
        "limit": 2,
        "limit_available": True,
        "used": 1,
        "usage_available": True,
        "usage_unavailable_reason": None,
        "terminal_observations": 1,
        "terminal_observations_available": True,
    }
    files = {
        "summary": {
            "attempt_id": "attempt-b",
            "run_name": "run-b",
            "progress": 100,
            "final_score": {
                "status": "success",
                "score": 100,
                "progress": 100,
                "expected_terminal_state": {"position_convention": "entity_feet"},
                "actual_terminal_state": {"position_convention": "entity_feet"},
            },
            "score_ownership_verified": True,
            "child_protocol": {"status": "completed", "result_valid": True, "result_written": True},
            "artifact_admission": {"passed": True, "missing": [], "invalid": []},
            "bridge_cleanup": {"cleanup_complete": True, "processes": {}},
            "runtime_target_safe_to_reuse": True,
            "runtime_target_quarantined": False,
            "runtime_target_quarantine": {},
            "server_lock_quarantine_detected": False,
        },
        "dag": {"summary": {"terminal_state": "success"}, "nodes": [{"lifecycle": {"status": "success", "active_agents": []}}]},
        "actions": {"Alice": [{"action": "navigateTo", "result": movement}]},
        "metrics": {"action_count": 1, "failed_action_count": 0},
        "movement": movement,
        "trace": {"schema_version": 1, "outer_episode_count": 1, "agent_iteration": agent_iteration, "entries": []},
        "terminal": {
            "schema_version": 2,
            "agent_iteration": agent_iteration,
            "judger_iteration": judger_iteration,
            "expected_terminal_state": {"position_convention": "entity_feet"},
            "actual_terminal_state": {"position_convention": "entity_feet"},
        },
    }
    if mutate:
        mutate(files)
    names = {
        "summary": "summary.json",
        "dag": "runtime_dual_dag_snapshot.json",
        "actions": "action_log.json",
        "metrics": "metrics.json",
        "trace": "judged_iteration_trace.json",
        "terminal": "judged_terminal_diagnostics.json",
    }
    for key, name in names.items():
        (run / name).write_text(json.dumps(files[key]), encoding="utf-8")
    (run / "attempt.json").write_text(json.dumps({"attempt_id": "attempt-b"}), encoding="utf-8")
    _finalize_manifest(run, "attempt-b")
    if extra_text is not None:
        (matrix / "operator-note.txt").write_text(extra_text, encoding="utf-8")
    (matrix / "attempt.json").write_text(json.dumps({"attempt_id": "matrix-attempt"}), encoding="utf-8")
    _finalize_manifest(matrix, "matrix-attempt")
    return matrix, run


def _finalize_manifest(root: Path, attempt_id: str):
    artifacts = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path not in {root / "artifact_manifest.json", root / "_COMPLETED"}:
            artifacts.append({
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
    (root / "artifact_manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "attempt_id": attempt_id,
        "producer": "test",
        "status": "completed",
        "artifacts": artifacts,
    }), encoding="utf-8")
    (root / "_COMPLETED").write_text(attempt_id + "\n", encoding="utf-8")
