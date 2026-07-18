from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from villageragent_visualizer.analysis_graph import AnalysisGraphService
from villageragent_visualizer.artifacts import ArtifactRepository
from villageragent_visualizer.dto import (
    AnalysisGraph,
    AnalysisGraphErrorCode,
    AnalysisGraphFilters,
    RunManifest,
    RuntimeGraph,
    RuntimeGraphErrorCode,
    Timeline,
    TimelineErrorCode,
)
from villageragent_visualizer.runtime_graph import RuntimeGraphService
from villageragent_visualizer.runs import RunRepository
from villageragent_visualizer.replay import ReplayService
from villageragent_visualizer.stream import SnapshotStreamManager
from villageragent_visualizer.timeline import TimelineService


def create_app(
    *,
    result_root: str | Path = "result",
    frontend_dist: str | Path | None = None,
    stream_poll_interval: float = 0.5,
    stream_heartbeat_interval: float = 15.0,
) -> FastAPI:
    app = FastAPI(title="VillagerAgent Visualizer", version="0.1.0")
    app.state.result_root = Path(result_root).expanduser().resolve()
    app.state.artifacts = ArtifactRepository(app.state.result_root)
    app.state.runs = RunRepository(app.state.result_root, artifacts=app.state.artifacts)
    app.state.runtime_graphs = RuntimeGraphService(artifacts=app.state.artifacts, runs=app.state.runs)
    app.state.analysis_graphs = AnalysisGraphService(artifacts=app.state.artifacts, runs=app.state.runs)
    app.state.timelines = TimelineService(
        artifacts=app.state.artifacts,
        runs=app.state.runs,
        analysis_graphs=app.state.analysis_graphs,
    )
    app.state.streams = SnapshotStreamManager(
        result_root=app.state.result_root,
        runs=app.state.runs,
        runtime_graphs=app.state.runtime_graphs,
        poll_interval=stream_poll_interval,
        heartbeat_interval=stream_heartbeat_interval,
    )
    app.state.replay = ReplayService(artifacts=app.state.artifacts, runs=app.state.runs)

    @app.websocket("/api/v1/runs/{run_id:path}/stream")
    async def stream_run(websocket: WebSocket, run_id: str) -> None:
        await websocket.accept()
        subscription = app.state.streams.subscribe(run_id)
        try:
            await websocket.send_json(subscription.initial)
            if subscription.initial["type"] == "run_unavailable":
                await websocket.close(code=1000)
                return
            while True:
                await websocket.send_json(await subscription.queue.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            await subscription.close()

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

    @app.get("/api/v1/runs/{run_id:path}/events")
    def get_events(run_id: str, start_seq: int = Query(default=1, ge=1), limit: int = Query(default=200, ge=1, le=1000)) -> dict:
        result = app.state.replay.events(run_id, start_seq=start_seq, limit=limit)
        if result is None:
            raise HTTPException(status_code=404, detail="Event journal unavailable")
        return result

    @app.get("/api/v1/runs/{run_id:path}/replay-state")
    def get_replay_state(run_id: str, seq: int | None = Query(default=None, ge=0)) -> dict:
        result = app.state.replay.state(run_id, seq=seq)
        if result is None:
            raise HTTPException(status_code=404, detail="Event replay unavailable")
        return result

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

    @app.get("/api/v1/runs/{run_id:path}/analysis-graph")
    def get_analysis_graph(
        run_id: str,
        node_type: list[str] | None = Query(default=None),
        edge_type: list[str] | None = Query(default=None),
        agent: list[str] | None = Query(default=None),
        task: list[str] | None = Query(default=None),
    ) -> AnalysisGraph:
        result = app.state.analysis_graphs.load(run_id, filters=AnalysisGraphFilters(
            node_types=frozenset(node_type or ()),
            edge_types=frozenset(edge_type or ()),
            agents=frozenset(agent or ()),
            task_ids=frozenset(task or ()),
        ))
        if result.error is not None:
            status_code = 404 if result.error.code in {
                AnalysisGraphErrorCode.RUN_NOT_FOUND,
                AnalysisGraphErrorCode.ARTIFACT_MISSING,
            } else 422
            raise HTTPException(status_code=status_code, detail={
                "code": result.error.code.value,
                "message": result.error.message,
                "warnings": [asdict(warning) for warning in result.error.warnings],
            })
        if result.graph is None:
            raise HTTPException(status_code=500, detail="Analysis graph result is empty")
        return result.graph

    @app.get("/api/v1/runs/{run_id:path}/timeline")
    def get_timeline(run_id: str) -> Timeline:
        result = app.state.timelines.load(run_id)
        if result.error is not None:
            status_code = 404 if result.error.code in {
                TimelineErrorCode.RUN_NOT_FOUND,
                TimelineErrorCode.ACTION_LOG_MISSING,
            } else 422
            raise HTTPException(status_code=status_code, detail={
                "code": result.error.code.value,
                "message": result.error.message,
                "warnings": [asdict(warning) for warning in result.error.warnings],
            })
        if result.timeline is None:
            raise HTTPException(status_code=500, detail="Timeline result is empty")
        return result.timeline

    @app.get("/api/v1/runs/{run_id:path}")
    def get_run(run_id: str) -> RunManifest:
        manifest = app.state.runs.get_run(run_id)
        if manifest is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return manifest

    if frontend_dist is not None:
        dist = Path(frontend_dist).expanduser().resolve()
        index = dist / "index.html"
        assets = dist / "assets"
        if index.is_file():
            if assets.is_dir():
                app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

            @app.get("/{path:path}", include_in_schema=False)
            def frontend(path: str) -> FileResponse:
                if path == "api" or path.startswith("api/"):
                    raise HTTPException(status_code=404, detail="API route not found")
                return FileResponse(index)

    return app
