import json
import sys
from types import SimpleNamespace

import pytest

import start_with_config


def _install_harness(monkeypatch, run_minecraft_experiment):
    monkeypatch.setitem(
        sys.modules,
        "benchmarks.minecraft.experiment",
        SimpleNamespace(run_minecraft_experiment=run_minecraft_experiment),
    )


def test_main_defaults_to_dry_run_and_forwards_config_selection(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    calls = []

    def fake_run_minecraft_experiment(**kwargs):
        calls.append(kwargs)
        return {"mode": "dry_run", "error": None}

    _install_harness(monkeypatch, fake_run_minecraft_experiment)

    result = start_with_config.main([
        "--config", str(config_path),
        "--config-index", "2",
        "--output-root", str(tmp_path / "artifacts"),
    ])

    assert result == 0
    assert calls == [{
        "config_path": str(config_path),
        "config_index": 2,
        "output_root": str(tmp_path / "artifacts"),
        "execute": False,
        "execute_timeout_seconds": None,
    }]
    assert json.loads(capsys.readouterr().out) == {"mode": "dry_run", "error": None}


def test_main_execute_requires_and_forwards_positive_timeout(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    calls = []

    def fake_run_minecraft_experiment(**kwargs):
        calls.append(kwargs)
        return {"mode": "execute", "error": None}

    _install_harness(monkeypatch, fake_run_minecraft_experiment)

    assert start_with_config.main([
        "--config", str(config_path),
        "--execute",
        "--timeout", "12.5",
    ]) == 0
    assert calls[0]["execute"] is True
    assert calls[0]["execute_timeout_seconds"] == 12.5

    with pytest.raises(SystemExit, match="2"):
        start_with_config.main(["--config", str(config_path), "--execute"])
    with pytest.raises(SystemExit, match="2"):
        start_with_config.main([
            "--config", str(config_path),
            "--execute",
            "--timeout", "0",
        ])
    with pytest.raises(SystemExit, match="2"):
        start_with_config.main([
            "--config", str(config_path),
            "--execute",
            "--timeout", "nan",
        ])

    assert len(calls) == 1


def test_main_reports_missing_config_before_loading_harness(tmp_path, capsys):
    config_path = tmp_path / "missing.json"

    with pytest.raises(SystemExit, match="2"):
        start_with_config.main(["--config", str(config_path)])

    assert f"config file not found: {config_path}" in capsys.readouterr().err


def test_main_reports_harness_errors(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "invalid.json"
    config_path.write_text("{}", encoding="utf-8")

    def fake_run_minecraft_experiment(**_kwargs):
        raise ValueError("config missing required field(s): task_name")

    _install_harness(monkeypatch, fake_run_minecraft_experiment)

    assert start_with_config.main(["--config", str(config_path)]) == 1
    assert (
        "error: Minecraft experiment harness failed: "
        "config missing required field(s): task_name"
    ) in capsys.readouterr().err


def test_main_returns_failure_for_harness_error_summary(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    _install_harness(
        monkeypatch,
        lambda **_kwargs: {"mode": "execute", "error": "runtime failed"},
    )

    assert start_with_config.main(["--config", str(config_path)]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "runtime failed"


def test_run_preserves_required_meta_judger_settings(monkeypatch, tmp_path):
    class StopAfterMetaSetting(Exception):
        pass

    class FakeEnvironment:
        def agent_register(self, **_kwargs):
            pass

        def run(self, **_kwargs):
            raise StopAfterMetaSetting

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(start_with_config, "VillagerBench", lambda **_kwargs: FakeEnvironment())
    monkeypatch.setattr(start_with_config, "load_agent_api_key_list", lambda: [])
    monkeypatch.setattr(start_with_config, "configure_ollama_agent", lambda *_args, **_kwargs: None)

    evaluation_arg = {"action": "move", "x": 1, "y": 2, "z": 3}
    with pytest.raises(StopAfterMetaSetting):
        start_with_config.run(
            "model",
            "http://localhost:11434/v1",
            "meta",
            0,
            1,
            False,
            1,
            "move safely",
            "",
            "127.0.0.1",
            25565,
            "meta-smoke",
            document=evaluation_arg,
            task_scenario="move",
        )

    setting = json.loads((tmp_path / ".cache" / "meta_setting.json").read_text(encoding="utf-8"))
    assert setting["task_scenario"] == "move"
    assert setting["evaluation_arg"] == evaluation_arg


def test_runtime_result_binds_score_to_current_attempt():
    environment = type("Environment", (), {"get_score": lambda self: {"score": 100}, "get_action_log": lambda self: {}})()

    result = start_with_config._runtime_result(
        environment,
        attempt_id="attempt-a",
        task_name="runtime-task-a",
    )

    assert result["score"]["attempt_id"] == "attempt-a"
    assert result["score"]["task_name"] == "runtime-task-a"
