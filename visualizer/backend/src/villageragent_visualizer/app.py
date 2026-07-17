from pathlib import Path

from fastapi import FastAPI, HTTPException

from villageragent_visualizer.artifacts import ArtifactRepository
from villageragent_visualizer.dto import RunManifest
from villageragent_visualizer.runs import RunRepository


def create_app(*, result_root: str | Path = "result") -> FastAPI:
    app = FastAPI(title="VillagerAgent Visualizer", version="0.1.0")
    app.state.result_root = Path(result_root).expanduser().resolve()
    app.state.artifacts = ArtifactRepository(app.state.result_root)
    app.state.runs = RunRepository(app.state.result_root, artifacts=app.state.artifacts)

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "villageragent-visualizer",
            "api_version": "v1",
        }

    @app.get("/api/v1/runs")
    def list_runs() -> dict[str, tuple[RunManifest, ...]]:
        return {"runs": app.state.runs.list_runs()}

    @app.get("/api/v1/runs/{run_id:path}")
    def get_run(run_id: str) -> RunManifest:
        manifest = app.state.runs.get_run(run_id)
        if manifest is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return manifest

    return app
