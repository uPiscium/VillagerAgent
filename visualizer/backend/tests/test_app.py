from pathlib import Path

from fastapi.testclient import TestClient

from villageragent_visualizer import (
    AnalysisGraphService,
    ArtifactRepository,
    RunRepository,
    RuntimeGraphService,
    TimelineService,
    create_app,
)
from villageragent_visualizer.stream import SnapshotStreamManager


def test_health_endpoint_returns_fixed_dto(tmp_path: Path) -> None:
    app = create_app(result_root=tmp_path)

    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "villageragent-visualizer",
        "api_version": "v1",
    }
    assert app.state.result_root == tmp_path.resolve()
    assert isinstance(app.state.artifacts, ArtifactRepository)
    assert app.state.artifacts.root == tmp_path.resolve()
    assert isinstance(app.state.runs, RunRepository)
    assert app.state.runs.root == tmp_path.resolve()
    assert isinstance(app.state.runtime_graphs, RuntimeGraphService)
    assert isinstance(app.state.analysis_graphs, AnalysisGraphService)
    assert isinstance(app.state.timelines, TimelineService)
    assert isinstance(app.state.streams, SnapshotStreamManager)


def test_optional_frontend_serves_spa_without_catching_api_routes(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<h1>Visualizer shell</h1>", encoding="utf-8")
    (assets / "app.js").write_text("window.loaded = true", encoding="utf-8")
    client = TestClient(create_app(result_root=tmp_path / "runs", frontend_dist=dist))

    assert client.get("/").text == "<h1>Visualizer shell</h1>"
    assert client.get("/runs/example/runtime?entity=task-1").text == "<h1>Visualizer shell</h1>"
    assert client.get("/assets/app.js").text == "window.loaded = true"
    missing_api = client.get("/api/v2/not-a-route")
    assert missing_api.status_code == 404
    assert missing_api.json() == {"detail": "API route not found"}


def test_missing_optional_frontend_keeps_api_only_mode(tmp_path: Path) -> None:
    client = TestClient(create_app(result_root=tmp_path, frontend_dist=tmp_path / "missing"))

    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/runs/example").status_code == 404
