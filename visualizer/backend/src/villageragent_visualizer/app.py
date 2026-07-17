from pathlib import Path

from fastapi import FastAPI

from villageragent_visualizer.artifacts import ArtifactRepository


def create_app(*, result_root: str | Path = "result") -> FastAPI:
    app = FastAPI(title="VillagerAgent Visualizer", version="0.1.0")
    app.state.result_root = Path(result_root).expanduser().resolve()
    app.state.artifacts = ArtifactRepository(app.state.result_root)

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "villageragent-visualizer",
            "api_version": "v1",
        }

    return app
