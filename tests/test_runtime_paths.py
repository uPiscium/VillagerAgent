import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain.agents import tool

from env.env import VillagerBench, env_type
from env.judger_artifacts import ScoreOwnershipError, TerminalArtifactWriter
from env.minecraft_client import Agent as MinecraftAgent, ToolActionBlockedError
from env.runtime_paths import RuntimePaths, atomic_write_json, read_json_artifact
from pipeline.agent import BaseAgent
from start_with_config import _resolve_runtime_document_path, _with_runtime_paths


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
    assert paths.task_list_log == tmp_path / "attempt-a" / "logs" / "task_list.json"
    assert paths.recipe_hint == tmp_path / "attempt-a" / "data" / "recipe_hint.json"
    assert paths.build_map == tmp_path / "attempt-a" / "data" / "map.json"
    assert paths.map_description == tmp_path / "attempt-a" / "data" / "map_description.json"


def test_runtime_generated_documents_do_not_cross_attempt_roots(tmp_path):
    first = RuntimePaths.isolated(tmp_path / "attempt-a")
    second = RuntimePaths.isolated(tmp_path / "attempt-b")

    atomic_write_json(first.recipe_hint, [{"result": "first"}])
    atomic_write_json(second.recipe_hint, [{"result": "second"}])
    atomic_write_json(first.task_list_log, {"task_list": ["first"]})
    atomic_write_json(second.task_list_log, {"task_list": ["second"]})

    assert read_json_artifact(first.recipe_hint).value == [{"result": "first"}]
    assert read_json_artifact(second.recipe_hint).value == [{"result": "second"}]
    assert read_json_artifact(first.task_list_log).value == {"task_list": ["first"]}
    assert read_json_artifact(second.task_list_log).value == {"task_list": ["second"]}


def test_generated_document_paths_resolve_under_runtime_root(tmp_path):
    paths = RuntimePaths.isolated(tmp_path / "attempt")

    assert _resolve_runtime_document_path("data\\recipe_hint.json", paths) == paths.recipe_hint
    assert _resolve_runtime_document_path("data/map_description.json", paths) == paths.map_description
    assert _resolve_runtime_document_path("data/recipes.json", paths) == Path("data/recipes.json")


def test_runtime_write_audit_has_no_known_global_judger_outputs():
    repository_root = Path(__file__).resolve().parents[1]
    sources = [
        repository_root / "pipeline" / "controller_tiny.py",
        repository_root / "pipeline" / "controller.py",
        repository_root / "env" / "meta_judger.py",
        repository_root / "env" / "farm_craft_judger.py",
        repository_root / "env" / "build_judger.py",
        repository_root / "env" / "env.py",
    ]
    forbidden = (
        'open("logs/task_list.json", "w")',
        'open("data/recipe_hint.json", "w")',
        "open('data/blueprint_description_all.json', 'w')",
        'open("data/map.json", \'w\')',
        "open('data/map_description.json', 'w')",
    )

    combined = "\n".join(path.read_text(encoding="utf-8") for path in sources)
    assert all(pattern not in combined for pattern in forbidden)
    assert 'open("data/recipes.json", "r")' in combined


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


def test_environment_reads_construction_metadata_from_runtime_root(tmp_path):
    paths = RuntimePaths.isolated(tmp_path / "attempt")
    atomic_write_json(paths.map_description, ["isolated map"])
    environment = object.__new__(VillagerBench)
    environment.env_type = env_type.construction
    environment.runtime_paths = paths

    assert environment.get_metadata() == ["isolated map"]


def test_minecraft_interaction_history_uses_injected_runtime_path(tmp_path):
    paths = RuntimePaths.isolated(tmp_path / "attempt")
    agent = object.__new__(MinecraftAgent)
    agent.runtime_paths = paths

    agent._save_interaction_history(
        {"input": "move to target"},
        [{"action": {"tool": "navigateTo"}, "feedback": {"status": True}}],
        "arrived",
    )

    history_files = list(paths.history_dir.iterdir())
    assert len(history_files) == 1
    assert read_json_artifact(history_files[0]).value == {
        "input": "move to target",
        "action_list": [
            {"action": {"tool": "navigateTo"}, "feedback": {"status": True}}
        ],
        "final_answer": "arrived",
    }
    assert not (tmp_path / "data" / "history").exists()


def test_minecraft_url_prefix_uses_injected_runtime_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    paths = RuntimePaths.isolated(tmp_path / "attempt")

    with paths.activated():
        MinecraftAgent("Alice", runtime_paths=paths)
        assert MinecraftAgent.get_url_prefix() == {"Alice": "http://localhost:5000"}

    assert read_json_artifact(paths.url_prefix).value == {"Alice": "http://localhost:5000"}
    assert not (tmp_path / "data" / "url_prefix.json").exists()


def test_judger_terminal_artifact_cannot_be_overwritten(tmp_path):
    paths = RuntimePaths.isolated(tmp_path / "attempt")
    writer = TerminalArtifactWriter(paths, paths.run_result_dir("run-a"))
    success = {
        "attempt_id": "attempt-a",
        "task_name": "run-a",
        "status": "success",
        "score": 100,
    }

    config = {"attempt_id": "attempt-a", "task_name": "run-a"}
    assert writer.write(success, config) is True
    assert writer.write({"status": "failure"}, config) is False

    assert read_json_artifact(paths.score).value == success
    assert read_json_artifact(paths.load_status).value == {"status": "end"}
    assert read_json_artifact(paths.run_result_dir("run-a") / "score.json").value == success


def test_judger_terminal_writer_rejects_missing_identity(tmp_path):
    paths = RuntimePaths.isolated(tmp_path / "attempt")
    writer = TerminalArtifactWriter(paths, paths.run_result_dir("run-a"))

    try:
        writer.write(
            {"task_name": "run-a", "status": "success", "score": 100},
            {"attempt_id": "attempt-a", "task_name": "run-a"},
        )
    except ScoreOwnershipError as exc:
        assert "missing: attempt_id" in str(exc)
    else:
        raise AssertionError("terminal score without explicit ownership must be rejected")


def test_environment_guarded_tool_balances_action_barrier():
    environment = object.__new__(VillagerBench)
    calls = []
    environment._tool_action_enter = lambda: calls.append("enter")
    environment._tool_action_exit = lambda: calls.append("exit")

    @tool
    def sample_action(value: int) -> dict:
        """Return the supplied value."""
        calls.append(("action", value))
        return {"message": str(value), "status": True}

    guarded = environment.guard_tool_actions([sample_action])[0]

    assert guarded.invoke({"value": 3}) == {"message": "3", "status": True}
    assert calls == ["enter", ("action", 3), "exit"]


def test_environment_guarded_tool_does_not_run_after_barrier_rejection():
    environment = object.__new__(VillagerBench)
    calls = []
    environment._tool_action_enter = lambda: (_ for _ in ()).throw(
        RuntimeError("barrier closed")
    )
    environment._tool_action_exit = lambda: calls.append("exit")

    @tool
    def sample_action(value: int) -> dict:
        """Return the supplied value."""
        calls.append(("action", value))
        return {"message": str(value), "status": True}

    guarded = environment.guard_tool_actions([sample_action])[0]

    with pytest.raises(RuntimeError, match="barrier closed"):
        guarded.invoke({"value": 3})
    assert calls == []


def test_minecraft_agent_does_not_retry_terminal_blocked_tool(monkeypatch):
    attempts = []
    agent = object.__new__(MinecraftAgent)
    agent.name = "Alice"
    agent.model = "test"
    agent.api_key_list = ["test-key"]
    agent.llm = object()
    agent.tools = []
    monkeypatch.setattr(MinecraftAgent, "provider", "ollama")
    monkeypatch.setattr(MinecraftAgent, "api_key_list", ["test-key"])
    monkeypatch.setattr(
        "env.minecraft_client.OllamaReasoningChatOpenAI",
        lambda **_kwargs: object(),
    )

    class BlockedExecutor:
        handle_parsing_errors = False

        def __call__(self, _input):
            attempts.append(True)
            raise ToolActionBlockedError("terminal barrier closed")

    monkeypatch.setattr(
        "env.minecraft_client.initialize_agent",
        lambda **_kwargs: BlockedExecutor(),
    )

    with pytest.raises(ToolActionBlockedError, match="terminal barrier closed"):
        agent.run("move", max_try_turn=10)
    assert attempts == [True]


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
