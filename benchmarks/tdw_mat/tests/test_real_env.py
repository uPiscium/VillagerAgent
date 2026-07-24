import json
import sys
from pathlib import Path
from types import SimpleNamespace

from benchmarks.tdw_mat.adapter import TDWMATConfig
from benchmarks.tdw_mat.real_env import (
    CoELATDWMATEnvFactory,
    CoELATDWRuntimeConfig,
    inspect_real_preflight,
)
from benchmarks.tdw_mat.real_smoke import main


def _source_tree(root: Path) -> None:
    files = (
        root / "tdw-gym" / "tdw_gym.py",
        root / "dataset" / "dataset_test" / "test_env.json",
        root / "dataset" / "name_map.json",
        root / "dataset" / "room_types.json",
        root / "LICENSE",
    )
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")


def test_preflight_reports_source_packages_and_display_separately(tmp_path):
    root = tmp_path / "tdw_mat"
    _source_tree(root)

    payload = inspect_real_preflight(
        coela_root=root,
        environ={},
        module_available=lambda name: name == "gym",
        python_version=(3, 10),
    )

    assert payload["checks"] == {
        "source_tree": True,
        "python_version_3_10_or_newer": True,
        "python_gym_package": True,
        "python_tdw_package": False,
        "display": False,
    }
    assert payload["ready"] is False
    assert payload["missing"] == ["python_tdw_package", "display"]
    assert payload["asset_cache"]["required_before_launch"] is False
    assert payload["coela_root"] == str(root)


def test_preflight_rejects_upstream_python_3_9_for_project_bridge(tmp_path):
    root = tmp_path / "tdw_mat"
    _source_tree(root)

    payload = inspect_real_preflight(
        coela_root=root,
        environ={"DISPLAY": ":0"},
        module_available=lambda _name: True,
        python_version=(3, 9),
    )

    assert payload["ready"] is False
    assert payload["missing"] == ["python_version_3_10_or_newer"]


def test_real_environment_bridge_uses_source_cwd_and_restores_process_cwd(tmp_path, monkeypatch):
    root = tmp_path / "tdw_mat"
    output = tmp_path / "output"
    _source_tree(root)
    calls = {}

    class FakeTDW:
        def __init__(self, **kwargs):
            calls["init_cwd"] = Path.cwd()
            calls["kwargs"] = kwargs
            self.closed = False
            self.f = SimpleNamespace(closed=True)

        def reset(self, **kwargs):
            calls["reset_cwd"] = Path.cwd()
            calls["reset_kwargs"] = kwargs
            return {"0": {}, "1": {}}, {"goal_description": {}}, [{}, {}]

        def step(self, actions):
            calls["step_cwd"] = Path.cwd()
            return {"0": {}, "1": {}}, 0.0, False, {"num_frames_for_step": 1}

        def check_goal(self):
            return 0, 1, False

        def close(self):
            self.closed = True

    monkeypatch.setitem(sys.modules, "tdw_gym", SimpleNamespace(TDW=FakeTDW))
    original_cwd = Path.cwd()
    factory = CoELATDWMATEnvFactory(CoELATDWRuntimeConfig(coela_root=root, output_dir=output))

    env = factory(TDWMATConfig())
    env.reset(seed=2824, options={"scene": "5a", "layout": "0_0", "task": "food"})
    env.step({"0": {"type": 6, "message": "hello"}, "1": {"type": "ongoing"}})
    env.close()

    assert calls["init_cwd"] == output.resolve()
    assert calls["reset_cwd"] == root.resolve()
    assert calls["step_cwd"] == root.resolve()
    assert calls["kwargs"]["data_prefix"].endswith("dataset/dataset_test/")
    assert Path.cwd() == original_cwd


def test_preflight_cli_writes_blocker_report_without_failing_inspection(tmp_path, monkeypatch):
    output = tmp_path / "preflight.json"
    monkeypatch.setattr(
        "benchmarks.tdw_mat.real_smoke.inspect_real_preflight",
        lambda **_kwargs: {"ready": False, "missing": ["display"]},
    )

    assert main(["--preflight-only", "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["missing"] == ["display"]
    assert main([
        "--preflight-only", "--require-ready", "--output", str(output)
    ]) == 2
