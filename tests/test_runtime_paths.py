import json
import os
from types import SimpleNamespace

from env.env import VillagerBench, env_type
from env.runtime_paths import RuntimePaths, atomic_write_json, read_json_artifact
from pipeline.agent import BaseAgent
from start_with_config import _with_runtime_paths


def test_default_runtime_paths_preserve_legacy_layout(tmp_path):
    paths = RuntimePaths.legacy(tmp_path)

    assert paths.meta_setting == tmp_path / ".cache" / "meta_setting.json"
    assert paths.load_status == tmp_path / ".cache" / "load_status.cache"
    assert paths.score == tmp_path / "data" / "score.json"
    assert paths.run_result_dir("run-a") == tmp_path / "result" / "run-a"


def test_isolated_runtime_paths_stay_under_attempt_root(tmp_path):
    paths = RuntimePaths.isolated(tmp_path / "attempt-a")

    assert paths.cache_dir == tmp_path / "attempt-a" / "cache"
    assert paths.score == tmp_path / "attempt-a" / "data" / "score.json"
    assert paths.meta_judger_phase == tmp_path / "attempt-a" / "cache" / "meta_judger_phase.cache"


def test_atomic_json_reader_ignores_temporary_file(tmp_path):
    target = tmp_path / "status.json"
    temporary = tmp_path / ".status.json.123.tmp"
    temporary.write_text('{"status": "end"}', encoding="utf-8")

    assert read_json_artifact(target).state == "absent"

    atomic_write_json(target, {"status": "loaded"})
    result = read_json_artifact(target)
    assert result.state == "valid"
    assert result.value == {"status": "loaded"}


def test_runtime_path_environment_is_restored(tmp_path, monkeypatch):
    monkeypatch.setenv("VILLAGER_RUNTIME_ROOT", "/previous")
    monkeypatch.setenv("VILLAGER_RUNTIME_LAYOUT", "legacy")
    paths = RuntimePaths.isolated(tmp_path / "attempt")

    with paths.activated():
        assert os.environ["VILLAGER_RUNTIME_ROOT"] == str(paths.root.resolve())
        assert os.environ["VILLAGER_RUNTIME_LAYOUT"] == "isolated"

    assert os.environ["VILLAGER_RUNTIME_ROOT"] == "/previous"
    assert os.environ["VILLAGER_RUNTIME_LAYOUT"] == "legacy"


def test_runtime_path_wrapper_accepts_positional_runtime_paths(tmp_path):
    paths = RuntimePaths.isolated(tmp_path / "attempt")

    @_with_runtime_paths
    def wrapped(value, runtime_paths=None):
        return value, runtime_paths, os.environ["VILLAGER_RUNTIME_LAYOUT"]

    assert wrapped("value", paths) == ("value", paths, "isolated")


def test_environment_reads_injected_score_and_status_paths(tmp_path):
    paths = RuntimePaths.isolated(tmp_path / "attempt")
    paths.ensure_directories()
    atomic_write_json(paths.score, {"score": 100})
    atomic_write_json(paths.load_status, {"status": "end"})
    environment = object.__new__(VillagerBench)
    environment.env_type = env_type.meta
    environment.runtime_paths = paths
    environment._invalid_status_reads = 0

    assert environment.get_score() == {"score": 100}
    assert environment.is_task_complete() is True


def test_environment_escalates_persistently_invalid_status(tmp_path):
    paths = RuntimePaths.isolated(tmp_path / "attempt")
    paths.ensure_directories()
    paths.load_status.write_text("{", encoding="utf-8")
    environment = object.__new__(VillagerBench)
    environment.env_type = env_type.meta
    environment.runtime_paths = paths
    environment._invalid_status_reads = 0

    assert environment.is_task_complete() is False
    assert environment.is_task_complete() is False
    try:
        environment.is_task_complete()
    except RuntimeError as exc:
        assert "load status remained invalid" in str(exc)
    else:
        raise AssertionError("persistently invalid status must become a diagnostic error")


def test_base_agent_reflection_does_not_read_global_meta_setting(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output_dir = tmp_path / "attempt" / "result" / "run-a"
    agent = object.__new__(BaseAgent)
    agent.name = "Alice"
    agent.reflect_info = {"prompt": [], "response": []}
    agent.reflection_output_dir = output_dir

    agent.update_reflect("system", "user", "response")

    payload = json.loads((output_dir / "Alice_reflect.json").read_text(encoding="utf-8"))
    assert payload["response"] == ["response"]


def test_meta_judger_command_receives_absolute_runtime_root(tmp_path, monkeypatch):
    paths = RuntimePaths.isolated(tmp_path / "attempt")
    paths.ensure_directories()
    commands = []

    class FakeProcess:
        pid = 123

        @staticmethod
        def poll():
            return 1

    environment = object.__new__(VillagerBench)
    environment.running = True
    environment._virtual_debug = False
    environment.logger = SimpleNamespace(info=lambda *_: None, debug=lambda *_: None)
    environment.agent_pool = []
    environment.env_type = env_type.meta
    environment.task_id = 0
    environment.host = "127.0.0.1"
    environment.port = 25565
    environment.task_name = "meta-smoke"
    environment.runtime_paths = paths
    environment.meta_diagnostics_dir = None
    monkeypatch.setattr("env.env.time.sleep", lambda _seconds: None)

    def popen(command, **_kwargs):
        commands.append(command)
        return FakeProcess()

    monkeypatch.setattr("env.env.subprocess.Popen", popen)

    try:
        environment.reset()
    except RuntimeError:
        pass

    assert "--runtime-root" in commands[0]
    root_index = commands[0].index("--runtime-root") + 1
    assert commands[0][root_index] == str(paths.root.resolve())
