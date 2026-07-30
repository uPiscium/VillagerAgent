import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from benchmarks.minecraft.matrix import main
from benchmarks.minecraft.matrix_runner import run_finalized_matrix
from benchmarks.minecraft.matrix_spec import (
    finalize_matrix_spec,
    matrix_spec_to_dict,
    parse_matrix_spec,
    validate_matrix_spec,
    write_finalized_matrix_spec,
)
from benchmarks.minecraft.matrix_variants import VARIANT_ORDER, get_movement_variant
from benchmarks.minecraft.world_snapshot import RestoredWorld
from benchmarks.minecraft.matrix_validation import (
    SCANNER_ID,
    SCANNER_PATTERNS_VERSION,
    SCANNER_SCHEMA_VERSION,
    SCANNER_SHA256,
    scanner_implementation_sha256,
)


def test_runner_restores_world_and_passes_it_to_injected_executor(tmp_path):
    premanifest = _premanifest(tmp_path)
    calls = []

    def executor(*, run, restored_world, output_dir):
        assert isinstance(restored_world, RestoredWorld)
        assert restored_world.world_directory == output_dir / "world"
        assert restored_world.world_directory.joinpath("level.dat").read_bytes().startswith(run.baseline_id.encode())
        calls.append((run.run_id, restored_world.world_directory))
        return _bundle(output_dir / "bundle", run.run_id)

    _attach_identity(executor, premanifest)

    result = run_finalized_matrix(
        premanifest, tmp_path / "matrix", executor=executor, repo_root=tmp_path
    )

    assert result["gate_passed"] is True
    assert result["planned"] == result["started"] == result["completed"] == result["passed"] == 12
    assert len(calls) == 12
    assert len({path for _, path in calls}) == 12
    assert (tmp_path / "matrix" / "_MATRIX_COMPLETED").is_file()
    assert not (tmp_path / "matrix" / "_MATRIX_FAILED").exists()
    rows = [json.loads(line) for line in (tmp_path / "matrix" / "matrix_runs.jsonl").read_text().splitlines()]
    assert [row["matrix_index"] for row in rows] == list(range(12))
    assert all(row["attempts"] == 1 and row["cleanup"]["passed"] for row in rows)
    manifest = json.loads((tmp_path / "matrix" / "matrix_manifest.json").read_text())
    assert "self_sha256" not in manifest
    assert len(manifest["references"]["validations"]) == 12


def test_runner_stops_on_first_failure_and_marks_remaining_skipped(tmp_path):
    premanifest = _premanifest(tmp_path)
    calls = []

    def executor(*, run, restored_world, output_dir):
        calls.append(run.run_id)
        if len(calls) == 2:
            raise RuntimeError("injected failure")
        return _bundle(output_dir / "bundle", run.run_id)

    _attach_identity(executor, premanifest)

    result = run_finalized_matrix(
        premanifest, tmp_path / "matrix", executor=executor, repo_root=tmp_path
    )

    assert len(calls) == 2
    assert result["started"] == 2
    assert result["passed"] == 1
    assert result["failed"] == 1
    assert result["skipped"] == 10
    assert [row["status"] for row in result["runs"]][1:] == ["failed"] + ["skipped"] * 10
    marker = json.loads((tmp_path / "matrix" / "_MATRIX_FAILED").read_text())
    assert marker["run"] == result["runs"][1]["run_name"]
    assert marker["skipped_count"] == 10


def test_premanifest_cli_validates_serializes_and_run_fails_closed(tmp_path, capsys):
    source = _premanifest(tmp_path)
    output = tmp_path / "serialized.json"

    assert main(["validate", str(source), "--repo-root", str(tmp_path)]) == 0
    assert main([
        "premanifest", str(source), "--output", str(output), "--repo-root", str(tmp_path)
    ]) == 0

    assert output.is_file()
    assert output.stat().st_mode & 0o222 == 0
    with pytest.raises(SystemExit):
        main([
            "run", str(output), "--output-dir", str(tmp_path / "matrix"),
            "--runtime-adapter", "external-server", "--repo-root", str(tmp_path),
        ])
    assert not (tmp_path / "matrix").exists()
    capsys.readouterr()


def _premanifest(tmp_path):
    baselines = []
    for baseline_id in ("a", "b"):
        archive = tmp_path / f"baseline-{baseline_id}.tar.gz"
        _snapshot(archive, baseline_id.encode() + b" level")
        baselines.append({
            "baseline_id": baseline_id,
            "path": archive.name,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        })
    revision = _init_git(tmp_path)
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
                    "expected_completion_policy": variant.completion_policy,
                    "expected_completion_semantics": variant.completion_semantics,
                    "target_tolerance": variant.tolerance,
                    "variant_definition_sha256": variant.definition_sha256,
                    "seed_scopes": {"requested": ["meta_judger"], "supported": ["meta_judger"], "applied": ["meta_judger"]},
                })
    payload = {
        "schema_version": 1, "matrix_id": "minecraft-judged-test-matrix",
        "lifecycle_state": "draft", "premanifest_sha256": None,
        "revision": revision, "seeds": [17, 29], "baselines": baselines,
        "runtime": {"name": "runtime", "image": "runtime:fixed", "digest": "sha256:" + "1" * 64},
        "model": {"provider": "test", "name": "fixed", "digest": "2" * 64},
        "scanner": {
            "name": SCANNER_ID,
            "schema_version": SCANNER_SCHEMA_VERSION,
            "implementation_sha256": scanner_implementation_sha256(),
            "patterns_version": SCANNER_PATTERNS_VERSION,
            "patterns_sha256": SCANNER_SHA256,
        },
        "generation": {"temperature": 0, "top_p": 1, "max_tokens": 10, "timeout_seconds": 10, "max_iterations": 2},
        "execution": {"mode": "sequential", "stop_on_first_failure": True, "retry_policy": "none"},
        "runs": runs,
    }
    spec = finalize_matrix_spec(
        validate_matrix_spec(parse_matrix_spec(payload), repo_root=tmp_path),
        repo_root=tmp_path,
    )
    path = tmp_path / "premanifest.json"
    write_finalized_matrix_spec(spec, path)
    assert json.loads(path.read_text()) == matrix_spec_to_dict(spec)
    return path


def _attach_identity(executor, premanifest: Path) -> None:
    payload = json.loads(premanifest.read_text())
    executor.matrix_identity = {
        "runtime": payload["runtime"],
        "model": payload["model"],
        "generation": payload["generation"],
    }


def _init_git(root: Path) -> str:
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


def _snapshot(path, level_data):
    with tarfile.open(path, "w:gz") as archive:
        for name, data in (("world/level.dat", level_data), ("world/region/r.0.0.mca", b"region")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def _bundle(root: Path, run_name: str) -> Path:
    run = root / "runs" / run_name
    run.mkdir(parents=True)
    movement = {
        "status": True, "completion_policy": "strict_per_axis",
        "completion_semantics": "all_axis_deltas_strictly_below_tolerance",
        "target_reached": True, "target_tolerance": 1.0,
        "axis_delta": {"x": 0.1, "y": 0.1, "z": 0.1},
    }
    agent = {"available": True, "source": "runtime counter", "limit": 2, "used": 1}
    judger = {
        "available": True, "owner": "external_meta_judger", "source": "judger counter",
        "source_available": True, "limit": 2, "limit_available": True, "used": 1,
        "usage_available": True, "usage_unavailable_reason": None,
        "terminal_observations": 1, "terminal_observations_available": True,
    }
    files = {
        "summary.json": {"attempt_id": "attempt-" + run_name, "run_name": run_name, "progress": 100,
            "final_score": {"status": "success", "score": 100, "progress": 100,
                "expected_terminal_state": {"player_position": get_movement_variant(run_name.split("-")[0]).target.as_dict()}},
            "seed_contract": _seed_resolution(int(run_name.split("-")[1])),
            "score_ownership_verified": True,
            "child_protocol": {"status": "completed", "result_valid": True, "result_written": True},
            "artifact_admission": {"passed": True, "missing": [], "invalid": []},
            "bridge_cleanup": {"cleanup_complete": True, "processes": {}},
            "runtime_target_safe_to_reuse": True, "runtime_target_quarantined": False,
            "runtime_target_quarantine": {}, "server_lock_quarantine_detected": False},
        "runtime_dual_dag_snapshot.json": {"summary": {"terminal_state": "success"}, "nodes": [{"lifecycle": {"status": "success", "active_agents": []}}]},
        "action_log.json": {"Alice": [{"action": "navigateTo", "result": movement}]},
        "metrics.json": {"action_count": 1, "failed_action_count": 0},
        "judged_iteration_trace.json": {"outer_episode_count": 1, "agent_iteration": agent, "entries": []},
        "judged_terminal_diagnostics.json": {"schema_version": 2, "agent_iteration": agent, "judger_iteration": judger},
        "attempt.json": {"attempt_id": "attempt-" + run_name},
    }
    for name, value in files.items():
        (run / name).write_text(json.dumps(value))
    _manifest(run, "attempt-" + run_name)
    (root / "attempt.json").write_text(json.dumps({"attempt_id": "parent-" + run_name}))
    _manifest(root, "parent-" + run_name)
    return root


def _seed_resolution(seed: int) -> dict:
    scopes = {
        name: {
            "requested": name == "meta_judger",
            "supported": name == "meta_judger",
            "applied": name == "meta_judger",
            **({} if name == "meta_judger" else {"reason": "not implemented"}),
        }
        for name in (
            "python_random", "meta_judger", "task_generation", "model_sampling",
            "world_generation", "agent_ordering",
        )
    }
    return {
        "schema_version": 1,
        "seed": seed,
        "requested_scopes": ["meta_judger"],
        "scopes": scopes,
    }


def _manifest(root: Path, attempt_id: str):
    artifacts = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path not in {root / "artifact_manifest.json", root / "_COMPLETED"}:
            artifacts.append({"path": path.relative_to(root).as_posix(), "size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    (root / "artifact_manifest.json").write_text(json.dumps({"status": "completed", "attempt_id": attempt_id, "artifacts": artifacts}))
    (root / "_COMPLETED").write_text(attempt_id + "\n")
