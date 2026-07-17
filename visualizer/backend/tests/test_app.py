from pathlib import Path

from fastapi.testclient import TestClient

from villageragent_visualizer import ArtifactRepository, RunRepository, create_app


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
