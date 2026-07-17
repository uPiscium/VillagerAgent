import json
from pathlib import Path

from fastapi.testclient import TestClient

from villageragent_visualizer import RuntimeGraphService, create_app
from villageragent_visualizer.dto import RuntimeGraphErrorCode


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _make_run(root: Path, run_id: str) -> Path:
    run_dir = root / run_id
    _write_json(run_dir / "summary.json", {"run_name": run_dir.name, "error": None})
    return run_dir


def _snapshot() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "runtime": "runtime_task_dag_store",
        "source_of_truth": "runtime_task_dag",
        "snapshot_source": "real_runtime",
        "summary": {"task_node_count": 2, "unknown_summary": "retained"},
        "nodes": [
            {
                "node_id": "runtime:task:1",
                "node_type": "runtime_task",
                "content": {
                    "description": "Collect wood",
                    "metadata": {"unknown_content": 3, "api_key": "hidden"},
                    "milestones": ["wood collected"],
                    "reflect": None,
                },
                "lifecycle": {
                    "status": "running",
                    "candidate_agents": ["Alice", "Bob"],
                    "active_agents": ["Alice", "Bob"],
                    "last_assigned_agents": ["Alice", "Bob"],
                    "required_agent_count": 2,
                    "available": True,
                },
                "derived": {
                    "dependency_ready": False,
                    "blocked_by_tasks": ["runtime:task:0"],
                    "dependency_blockers": [{
                        "task_id": "runtime:task:0",
                        "description": "Prepare tools",
                        "status": "failure",
                        "relation": "direct",
                    }],
                },
                "provenance": {"source": "TaskManager.decomposition"},
                "unknown_node_field": "retained",
            },
            {
                "node_id": "runtime:task:2",
                "node_type": "future_runtime_task",
                "content": {},
                "lifecycle": {"status": "future_status"},
                "derived": {"dependency_ready": True},
                "provenance": {},
            },
        ],
        "edges": [{
            "source_id": "runtime:task:1",
            "target_id": "runtime:task:2",
            "edge_type": "precedes_task",
            "metadata": {"source": "runtime_task_dag_store"},
            "unknown_edge_field": True,
        }],
        "mutation_history": [{"revision": 1, "operation": "insert"}],
    }


def test_runtime_graph_api_preserves_canonical_semantics_and_sanitizes_data(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path, "group/run-a")
    _write_json(run_dir / "runtime_dual_dag_snapshot.json", _snapshot())
    client = TestClient(create_app(result_root=tmp_path))

    response = client.get("/api/v1/runs/group/run-a/runtime-graph")

    assert response.status_code == 200
    graph = response.json()
    assert graph["authority"] == "canonical_runtime_state"
    assert graph["snapshot_source"] == "real_runtime"
    assert graph["source_of_truth"] == "runtime_task_dag"
    assert graph["summary"]["unknown_summary"] == "retained"
    node = graph["nodes"][0]
    assert node["content"]["metadata"] == {"unknown_content": 3}
    assert node["lifecycle"] == {
        "status": "running",
        "candidate_agents": ["Alice", "Bob"],
        "active_agents": ["Alice", "Bob"],
        "last_assigned_agents": ["Alice", "Bob"],
        "required_agent_count": 2,
    }
    assert node["derived"]["dependency_ready"] is False
    assert "runnable" not in node["derived"]
    assert node["derived"]["blocked_by_tasks"] == ["runtime:task:0"]
    assert node["derived"]["dependency_blockers"][0]["status"] == "failure"
    assert node["provenance"]["source"] == "TaskManager.decomposition"
    assert node["extra"]["unknown_node_field"] == "retained"
    assert graph["edges"][0]["edge_id"].startswith("runtime:edge:")
    assert graph["edges"][0]["extra"]["unknown_edge_field"] is True
    assert graph["mutation_history"] == [{"revision": 1, "operation": "insert"}]
    assert any(warning["code"] == "deprecated_available_ignored" for warning in graph["warnings"])


def test_runtime_graph_edge_ids_are_stable(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path, "stable")
    _write_json(run_dir / "runtime_dual_dag_snapshot.json", _snapshot())
    app = create_app(result_root=tmp_path)

    first = app.state.runtime_graphs.load("stable")
    second = app.state.runtime_graphs.load("stable")

    assert first.graph is not None
    assert second.graph is not None
    assert first.graph.edges[0].edge_id == second.graph.edges[0].edge_id


def test_runtime_graph_uses_sanitized_checkpoint_fallback(tmp_path: Path) -> None:
    run_dir = tmp_path / "live"
    _write_json(run_dir / "attempt.json", {
        "schema_version": 1,
        "attempt_id": "live-attempt",
        "producer": "benchmarks.minecraft.experiment",
        "status": "running",
    })
    snapshot = _snapshot()
    snapshot.pop("snapshot_source")
    snapshot["nodes"][0]["content"]["secret"] = "hidden"
    _write_json(run_dir / ".runtime" / "runtime_result.json", {
        "runtime_task_dag_snapshot": snapshot,
        "api_key": "hidden",
    })
    client = TestClient(create_app(result_root=tmp_path))

    response = client.get("/api/v1/runs/live/runtime-graph")

    assert response.status_code == 200
    graph = response.json()
    assert graph["snapshot_source"] == "runtime_checkpoint"
    assert "secret" not in graph["nodes"][0]["content"]
    assert any(warning["code"] == "runtime_checkpoint_fallback" for warning in graph["warnings"])


def test_runtime_graph_falls_back_when_normalized_snapshot_is_malformed(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path, "fallback")
    (run_dir / "runtime_dual_dag_snapshot.json").write_text("{broken", encoding="utf-8")
    _write_json(run_dir / ".runtime" / "runtime_result.json", {
        "runtime_task_dag_snapshot": _snapshot(),
    })
    client = TestClient(create_app(result_root=tmp_path))

    response = client.get("/api/v1/runs/fallback/runtime-graph")

    assert response.status_code == 200
    warning_codes = {warning["code"] for warning in response.json()["warnings"]}
    assert "malformed" in warning_codes
    assert "runtime_checkpoint_fallback" in warning_codes


def test_runtime_graph_does_not_use_legacy_task_graph_snapshot(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path, "legacy-only")
    _write_json(run_dir / "task_graph_snapshot.json", {
        "tasks": [{"description": "not canonical", "available": True}],
        "edges": [],
    })
    client = TestClient(create_app(result_root=tmp_path))

    response = client.get("/api/v1/runs/legacy-only/runtime-graph")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "runtime_snapshot_missing"


def test_runtime_graph_reports_invalid_snapshot_shape(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path, "invalid")
    _write_json(run_dir / "runtime_dual_dag_snapshot.json", {
        "schema_version": "1.0.0",
        "nodes": {},
        "edges": [],
    })
    client = TestClient(create_app(result_root=tmp_path))

    response = client.get("/api/v1/runs/invalid/runtime-graph")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "runtime_snapshot_invalid"


def test_runtime_graph_reports_run_not_found(tmp_path: Path) -> None:
    app = create_app(result_root=tmp_path)

    result = app.state.runtime_graphs.load("missing")

    assert result.error is not None
    assert result.error.code is RuntimeGraphErrorCode.RUN_NOT_FOUND
