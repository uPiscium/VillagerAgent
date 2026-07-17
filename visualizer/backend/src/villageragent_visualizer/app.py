from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException

from villageragent_visualizer.artifacts import ArtifactRepository
from villageragent_visualizer.dto import RunManifest, RuntimeGraph, RuntimeGraphErrorCode
from villageragent_visualizer.runtime_graph import RuntimeGraphService
from villageragent_visualizer.runs import RunRepository


def create_app(*, result_root: str | Path = "result") -> FastAPI:
    app = FastAPI(title="VillagerAgent Visualizer", version="0.1.0")
    app.state.result_root = Path(result_root).expanduser().resolve()
    app.state.artifacts = ArtifactRepository(app.state.result_root)
    app.state.runs = RunRepository(app.state.result_root, artifacts=app.state.artifacts)
    app.state.runtime_graphs = RuntimeGraphService(artifacts=app.state.artifacts, runs=app.state.runs)

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

    @app.get("/api/v1/runs/{run_id:path}/runtime-graph")
    def get_runtime_graph(run_id: str) -> RuntimeGraph:
        result = app.state.runtime_graphs.load(run_id)
        if result.error is not None:
            status_code = 404 if result.error.code in {
                RuntimeGraphErrorCode.RUN_NOT_FOUND,
                RuntimeGraphErrorCode.SNAPSHOT_MISSING,
            } else 422
            raise HTTPException(status_code=status_code, detail={
                "code": result.error.code.value,
                "message": result.error.message,
                "warnings": [asdict(warning) for warning in result.error.warnings],
            })
        if result.graph is None:
            raise HTTPException(status_code=500, detail="Runtime graph result is empty")
        return result.graph

    @app.get("/api/v1/runs/{run_id:path}")
    def get_run(run_id: str) -> RunManifest:
        manifest = app.state.runs.get_run(run_id)
        if manifest is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return manifest

    return app
