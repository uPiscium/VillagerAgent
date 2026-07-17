import json
from pathlib import Path

from fastapi.testclient import TestClient

from villageragent_visualizer import AnalysisGraphService, create_app
from villageragent_visualizer.dto import AnalysisGraphFilters


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _make_run(root: Path, run_id: str = "analysis") -> Path:
    run_dir = root / run_id
    _write_json(run_dir / "summary.json", {"run_name": run_dir.name, "error": None})
    return run_dir


def _artifact() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "runtime": "minecraft_dual_dag_artifact",
        "artifact_generation_mutates_runtime": False,
        "task_state_source": "real_runtime",
        "summary": {"node_count": 6, "edge_count": 5},
        "schema": {
            "node_types": [
                "minecraft_task",
                "minecraft_action",
                "minecraft_observation",
                "minecraft_claim",
            ],
            "unknown_schema_field": True,
        },
        "mapping": {
            "boundaries": ["This is a post-hoc projection."],
            "unknown_mapping_field": "retained",
        },
        "nodes": [
            {
                "node_id": "minecraft:task:task-1",
                "node_type": "minecraft_task",
                "content": {
                    "description": "Find chest",
                    "status": "running",
                    "assigned_agents": ["Bob"],
                    "api_key": "hidden",
                },
                "provenance": {"source": "runtime_task_dag_snapshot"},
                "confidence": 1.0,
            },
            {
                "node_id": "minecraft:task:task-2",
                "node_type": "minecraft_task",
                "content": {"description": "Return home", "assigned_agents": ["Alice"]},
                "provenance": {},
                "confidence": 1.0,
            },
            {
                "node_id": "minecraft:action:Bob:0",
                "node_type": "minecraft_action",
                "content": {"agent": "Bob", "tool": "openContainer"},
                "provenance": {"agent": "Bob", "source": "data/action_log.json"},
                "confidence": 1.0,
            },
            {
                "node_id": "minecraft:observation:Bob:0",
                "node_type": "minecraft_observation",
                "content": {"result": {"status": True}},
                "provenance": {"agent": "Bob"},
                "confidence": 1.0,
            },
            {
                "node_id": "minecraft:claim:Bob:0",
                "node_type": "minecraft_claim",
                "content": {"message": "The chest is empty"},
                "provenance": {"agent": "Bob"},
                "confidence": 0.8,
            },
            {
                "node_id": "minecraft:future:1",
                "node_type": "minecraft_future_evidence",
                "content": {"future": True},
                "provenance": {},
                "confidence": "unknown",
                "unknown_node_field": "retained",
            },
        ],
        "edges": [
            {
                "source_id": "minecraft:task:task-1",
                "target_id": "minecraft:task:task-2",
                "edge_type": "precedes_task",
                "metadata": {},
            },
            {
                "source_id": "minecraft:task:task-1",
                "target_id": "minecraft:action:Bob:0",
                "edge_type": "task_invokes_action",
                "metadata": {"source": "agent_assignment"},
            },
            {
                "source_id": "minecraft:action:Bob:0",
                "target_id": "minecraft:observation:Bob:0",
                "edge_type": "produces_observation",
                "metadata": {},
            },
            {
                "source_id": "minecraft:action:Bob:0",
                "target_id": "minecraft:claim:Bob:0",
                "edge_type": "reports_claim",
                "metadata": {},
            },
            {
                "source_id": "minecraft:future:1",
                "target_id": "minecraft:claim:Bob:0",
                "edge_type": "future_edge_type",
                "metadata": {},
                "unknown_edge_field": 2,
            },
        ],
    }


def test_analysis_graph_api_preserves_projection_authority_and_unknown_types(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path, "group/run-a")
    _write_json(run_dir / "dual_dag_artifact.json", _artifact())
    client = TestClient(create_app(result_root=tmp_path))

    response = client.get("/api/v1/runs/group/run-a/analysis-graph")

    assert response.status_code == 200
    graph = response.json()
    assert graph["authority"] == "posthoc_analysis_projection"
    assert graph["task_state_source"] == "real_runtime"
    assert graph["schema"]["unknown_schema_field"] is True
    assert graph["mapping"]["unknown_mapping_field"] == "retained"
    assert {node["node_type"] for node in graph["nodes"]} >= {
        "minecraft_task",
        "minecraft_action",
        "minecraft_observation",
        "minecraft_claim",
        "minecraft_future_evidence",
    }
    task = next(node for node in graph["nodes"] if node["node_id"] == "minecraft:task:task-1")
    assert task["runtime_task_id"] == "runtime:task:task-1"
    assert "api_key" not in task["content"]
    claim = next(node for node in graph["nodes"] if node["node_type"] == "minecraft_claim")
    assert claim["content"] == {"message": "The chest is empty"}
    unknown = next(node for node in graph["nodes"] if node["node_type"] == "minecraft_future_evidence")
    assert unknown["extra"]["unknown_node_field"] == "retained"
    unknown_edge = next(edge for edge in graph["edges"] if edge["edge_type"] == "future_edge_type")
    assert unknown_edge["extra"]["unknown_edge_field"] == 2


def test_analysis_graph_filters_never_return_dangling_edges(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path)
    _write_json(run_dir / "dual_dag_artifact.json", _artifact())
    app = create_app(result_root=tmp_path)

    result = app.state.analysis_graphs.load("analysis", filters=AnalysisGraphFilters(
        node_types=frozenset({"minecraft_action", "minecraft_observation"}),
        agents=frozenset({"Bob"}),
    ))

    assert result.graph is not None
    node_ids = {node.node_id for node in result.graph.nodes}
    assert node_ids == {"minecraft:action:Bob:0", "minecraft:observation:Bob:0"}
    assert all(edge.source_id in node_ids and edge.target_id in node_ids for edge in result.graph.edges)
    assert [edge.edge_type for edge in result.graph.edges] == ["produces_observation"]


def test_analysis_graph_task_filter_keeps_explicit_action_subgraph_not_successor_tasks(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path)
    _write_json(run_dir / "dual_dag_artifact.json", _artifact())
    app = create_app(result_root=tmp_path)

    result = app.state.analysis_graphs.load("analysis", filters=AnalysisGraphFilters(
        task_ids=frozenset({"minecraft:task:task-1"}),
    ))

    assert result.graph is not None
    node_ids = {node.node_id for node in result.graph.nodes}
    assert node_ids == {
        "minecraft:task:task-1",
        "minecraft:action:Bob:0",
        "minecraft:observation:Bob:0",
        "minecraft:claim:Bob:0",
    }
    assert "minecraft:task:task-2" not in node_ids
    assert all(edge.source_id in node_ids and edge.target_id in node_ids for edge in result.graph.edges)


def test_analysis_graph_api_exposes_repeated_query_filters(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path)
    _write_json(run_dir / "dual_dag_artifact.json", _artifact())
    client = TestClient(create_app(result_root=tmp_path))

    response = client.get(
        "/api/v1/runs/analysis/analysis-graph",
        params=[("node_type", "minecraft_action"), ("agent", "Bob")],
    )

    assert response.status_code == 200
    assert [node["node_id"] for node in response.json()["nodes"]] == ["minecraft:action:Bob:0"]
    assert response.json()["edges"] == []
    assert response.json()["applied_filters"]["node_types"] == ["minecraft_action"]


def test_analysis_graph_edge_type_filter_preserves_nodes_and_filters_edges(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path)
    _write_json(run_dir / "dual_dag_artifact.json", _artifact())
    app = create_app(result_root=tmp_path)

    result = app.state.analysis_graphs.load("analysis", filters=AnalysisGraphFilters(
        edge_types=frozenset({"reports_claim"}),
    ))

    assert result.graph is not None
    assert len(result.graph.nodes) == 6
    assert [edge.edge_type for edge in result.graph.edges] == ["reports_claim"]


def test_analysis_graph_reports_missing_and_invalid_artifacts(tmp_path: Path) -> None:
    _make_run(tmp_path, "missing")
    invalid_dir = _make_run(tmp_path, "invalid")
    (invalid_dir / "dual_dag_artifact.json").write_text("{broken", encoding="utf-8")
    client = TestClient(create_app(result_root=tmp_path))

    missing = client.get("/api/v1/runs/missing/analysis-graph")
    invalid = client.get("/api/v1/runs/invalid/analysis-graph")

    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "analysis_artifact_missing"
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "analysis_artifact_invalid"


def test_analysis_graph_warns_when_task_state_source_is_missing(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path)
    artifact = _artifact()
    artifact.pop("task_state_source")
    _write_json(run_dir / "dual_dag_artifact.json", artifact)
    app = create_app(result_root=tmp_path)

    result = app.state.analysis_graphs.load("analysis")

    assert result.graph is not None
    assert result.graph.task_state_source == "unknown"
    assert any(warning.code == "task_state_source_missing" for warning in result.graph.warnings)
