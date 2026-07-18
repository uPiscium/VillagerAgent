from pathlib import Path

from fastapi.testclient import TestClient

from villageragent_visualizer import create_app


FIXTURE_ROOT = Path(__file__).parents[2] / "fixtures" / "runs"


def test_offline_fixture_matrix_exposes_all_supported_run_states() -> None:
    client = TestClient(create_app(result_root=FIXTURE_ROOT))

    response = client.get("/api/v1/runs")

    assert response.status_code == 200
    states = {run["run_id"]: run["state"] for run in response.json()["runs"]}
    assert states == {
        "successful": "completed",
        "failed": "failed",
        "timed-out": "timed_out",
        "partial": "partial",
        "malformed": "invalid",
        "schema-version": "completed",
    }


def test_complete_fixture_opens_every_offline_mvp_view() -> None:
    client = TestClient(create_app(result_root=FIXTURE_ROOT))

    assert client.get("/api/v1/runs/successful").status_code == 200
    runtime = client.get("/api/v1/runs/successful/runtime-graph")
    analysis = client.get("/api/v1/runs/successful/analysis-graph")
    timeline = client.get("/api/v1/runs/successful/timeline")
    replay = client.get("/api/v1/runs/successful/replay-state", params={"seq": 4})

    assert runtime.status_code == 200
    assert runtime.json()["authority"] == "canonical_runtime_state"
    assert analysis.status_code == 200
    assert analysis.json()["authority"] == "posthoc_analysis_projection"
    assert timeline.status_code == 200
    assert {lane["agent"] for lane in timeline.json()["lanes"]} == {"Alice", "Bob"}
    assert replay.status_code == 200
    assert replay.json()["graph"]["nodes"][0]["lifecycle"]["status"] == "success"
    assert replay.json()["timeline"][0]["event_type"] == "action_recorded"


def test_malformed_and_future_schema_fixtures_do_not_break_healthy_runs() -> None:
    client = TestClient(create_app(result_root=FIXTURE_ROOT))

    assert client.get("/api/v1/runs/malformed").json()["state"] == "invalid"
    future = client.get("/api/v1/runs/schema-version/runtime-graph")
    assert future.status_code == 422
    assert future.json()["detail"]["code"] == "runtime_snapshot_invalid"
    assert {warning["code"] for warning in future.json()["detail"]["warnings"]} == {"unsupported_schema_major"}
    assert client.get("/api/v1/runs/successful").status_code == 200
