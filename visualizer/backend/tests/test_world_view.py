from pathlib import Path

from fastapi.testclient import TestClient

from villageragent_visualizer import create_app


def test_world_view_is_disabled_by_default_and_invalid_dependency_is_graceful(tmp_path: Path) -> None:
    disabled = TestClient(create_app(result_root=tmp_path)).get("/api/v1/world-view/config").json()
    invalid = TestClient(create_app(result_root=tmp_path, world_view_url="file:///private/world")).get("/api/v1/world-view/config").json()

    assert disabled == {"enabled": False, "url": None, "remote": False, "reason": "not_configured"}
    assert invalid["enabled"] is False
    assert invalid["reason"] == "invalid_url"


def test_local_viewer_is_opt_in_and_remote_exposure_requires_second_opt_in(tmp_path: Path) -> None:
    local = TestClient(create_app(result_root=tmp_path, world_view_url="http://127.0.0.1:3007/")).get("/api/v1/world-view/config").json()
    blocked = TestClient(create_app(result_root=tmp_path, world_view_url="https://viewer.example.test")).get("/api/v1/world-view/config").json()
    remote = TestClient(create_app(result_root=tmp_path, world_view_url="https://viewer.example.test", allow_remote_world_view=True)).get("/api/v1/world-view/config").json()

    assert local == {"enabled": True, "url": "http://127.0.0.1:3007", "remote": False, "reason": None}
    assert blocked["enabled"] is False and blocked["reason"] == "remote_requires_explicit_opt_in"
    assert remote["enabled"] is True and remote["remote"] is True
