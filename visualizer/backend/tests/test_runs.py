import json
from pathlib import Path

from fastapi.testclient import TestClient

from villageragent_visualizer import RunRepository, create_app
from villageragent_visualizer.dto import RunState


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_attempt(run_dir: Path, *, attempt_id: str, status: str) -> None:
    _write_json(run_dir / "attempt.json", {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "producer": "benchmarks.minecraft.experiment",
        "status": status,
    })


def _write_terminal_run(
    root: Path,
    name: str,
    *,
    summary: dict[str, object],
    status: str,
) -> Path:
    run_dir = root / name
    attempt_id = f"attempt-{name}"
    _write_attempt(run_dir, attempt_id=attempt_id, status=status)
    _write_json(run_dir / "summary.json", {"attempt_id": attempt_id, "run_name": name, **summary})
    _write_json(run_dir / "artifact_manifest.json", {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "producer": "benchmarks.minecraft.experiment",
        "status": status,
        "artifacts": [],
    })
    if status == "completed":
        (run_dir / "_COMPLETED").write_text(attempt_id + "\n", encoding="utf-8")
    return run_dir


def test_run_repository_distinguishes_all_states_and_ignores_tmp_only_directory(tmp_path: Path) -> None:
    _write_terminal_run(
        tmp_path,
        "completed",
        summary={"started_at": "2026-07-17T12:00:00Z", "error": None, "timed_out": False},
        status="completed",
    )
    _write_terminal_run(
        tmp_path,
        "failed",
        summary={"started_at": "2026-07-17T11:00:00Z", "error": "boom", "timed_out": False},
        status="failed",
    )
    _write_terminal_run(
        tmp_path,
        "timed-out",
        summary={"started_at": "2026-07-17T10:00:00Z", "error": "timeout", "timed_out": True},
        status="failed",
    )
    live_dir = tmp_path / "live"
    _write_attempt(live_dir, attempt_id="attempt-live", status="running")
    _write_json(live_dir / ".runtime" / "runtime_result.json", {
        "runtime_task_dag_snapshot": {"source_of_truth": "runtime_task_dag"},
    })
    _write_json(tmp_path / "partial" / "action_log.json", {"Alice": []})
    invalid_dir = tmp_path / "invalid"
    invalid_dir.mkdir()
    (invalid_dir / "summary.json").write_text("{broken", encoding="utf-8")
    tmp_only = tmp_path / "tmp-only" / ".runtime"
    tmp_only.mkdir(parents=True)
    (tmp_only / "runtime_result.json.tmp").write_text("{}", encoding="utf-8")

    manifests = RunRepository(tmp_path).list_runs()
    states = {manifest.run_id: manifest.state for manifest in manifests}

    assert states == {
        "completed": RunState.COMPLETED,
        "failed": RunState.FAILED,
        "timed-out": RunState.TIMED_OUT,
        "live": RunState.LIVE,
        "partial": RunState.PARTIAL,
        "invalid": RunState.INVALID,
    }
    assert "tmp-only" not in states


def test_run_repository_orders_started_runs_descending_then_stably_by_id(tmp_path: Path) -> None:
    for name, started_at in (
        ("b", "2026-07-17T10:00:00Z"),
        ("a", "2026-07-17T10:00:00Z"),
        ("newest", "2026-07-17T12:00:00Z"),
        ("no-time", None),
    ):
        summary = {"error": None}
        if started_at is not None:
            summary["started_at"] = started_at
        _write_terminal_run(tmp_path, name, summary=summary, status="completed")

    manifests = RunRepository(tmp_path).list_runs()

    assert [manifest.run_id for manifest in manifests] == ["newest", "a", "b", "no-time"]


def test_run_manifest_contains_public_metadata_and_artifact_availability(tmp_path: Path) -> None:
    run_dir = _write_terminal_run(
        tmp_path,
        "metadata",
        summary={
            "started_at": "2026-07-17T12:00:00Z",
            "mode": "execute",
            "task_name": "Build a shelter",
            "task_type": "construction",
            "task_idx": 4,
            "runtime_selection_policy": "dual-dag",
            "task_state_source": "real_runtime",
            "snapshot_source": "runtime_result",
            "source_of_truth": "runtime_task_dag",
            "progress": 0.75,
            "error": None,
            "output_dir": "/private/absolute/path",
        },
        status="completed",
    )
    _write_json(run_dir / "action_log.json", {"Alice": []})
    _write_json(run_dir / "runtime_dual_dag_snapshot.json", {"schema_version": "1.0.0"})

    manifest = RunRepository(tmp_path).get_run("metadata")

    assert manifest is not None
    assert manifest.name == "metadata"
    assert manifest.task.name == "Build a shelter"
    assert manifest.task.task_type == "construction"
    assert manifest.task.index == 4
    assert manifest.policy == "dual-dag"
    assert manifest.source.task_state == "real_runtime"
    assert manifest.source.snapshot == "runtime_result"
    assert manifest.source.source_of_truth == "runtime_task_dag"
    assert manifest.progress == 0.75
    assert manifest.artifacts["action_log"] is True
    assert manifest.artifacts["runtime_graph"] is True
    assert all("/private/absolute/path" not in warning.message for warning in manifest.warnings)


def test_malformed_run_does_not_prevent_other_runs_from_being_listed(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed"
    malformed.mkdir()
    (malformed / "summary.json").write_bytes(b"\xff")
    _write_terminal_run(tmp_path, "healthy", summary={"error": None}, status="completed")

    manifests = RunRepository(tmp_path).list_runs()

    assert {manifest.run_id for manifest in manifests} == {"healthy", "malformed"}
    invalid = next(manifest for manifest in manifests if manifest.run_id == "malformed")
    assert invalid.state is RunState.INVALID
    assert any(warning.code == "invalid_encoding" for warning in invalid.warnings)


def test_matrix_container_is_not_duplicated_but_nested_run_is_discovered(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix"
    _write_attempt(matrix, attempt_id="matrix-attempt", status="completed")
    _write_json(matrix / "matrix_summary.json", {"runs": 1})
    _write_terminal_run(matrix / "runs", "child", summary={"error": None}, status="completed")

    manifests = RunRepository(tmp_path).list_runs()

    assert [manifest.run_id for manifest in manifests] == ["matrix/runs/child"]


def test_run_repository_rejects_traversal_and_external_directory_symlink(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside-run"
    _write_terminal_run(outside, "secret", summary={"task_name": "do not expose"}, status="completed")
    (tmp_path / "linked-run").symlink_to(outside / "secret", target_is_directory=True)
    repository = RunRepository(tmp_path)

    assert repository.get_run("../outside") is None
    assert repository.get_run(str(outside / "secret")) is None
    assert repository.get_run("linked-run") is None
    assert repository.list_runs() == ()


def test_symlinked_summary_is_invalid_without_exposing_external_metadata(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside-summary.json"
    outside.write_text('{"task_name": "secret task"}', encoding="utf-8")
    run_dir = tmp_path / "unsafe"
    _write_attempt(run_dir, attempt_id="unsafe-attempt", status="running")
    (run_dir / "summary.json").symlink_to(outside)

    manifest = RunRepository(tmp_path).get_run("unsafe")

    assert manifest is not None
    assert manifest.state is RunState.INVALID
    assert manifest.task.name == ""
    assert any(warning.code == "invalid_path" for warning in manifest.warnings)


def test_manifest_api_lists_and_returns_nested_runs(tmp_path: Path) -> None:
    _write_terminal_run(tmp_path / "group", "run-a", summary={"error": None}, status="completed")
    client = TestClient(create_app(result_root=tmp_path))

    list_response = client.get("/api/v1/runs")
    detail_response = client.get("/api/v1/runs/group/run-a")
    missing_response = client.get("/api/v1/runs/missing")

    assert list_response.status_code == 200
    assert list_response.json()["runs"][0]["run_id"] == "group/run-a"
    assert list_response.json()["runs"][0]["state"] == "completed"
    assert detail_response.status_code == 200
    assert detail_response.json()["run_id"] == "group/run-a"
    assert missing_response.status_code == 404
