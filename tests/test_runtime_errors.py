import json
from types import SimpleNamespace

import pytest

import env.env as env_module
from env.env import VillagerBench, env_type
from pipeline.task_manager import TaskManager
from pipeline.utils import dict2document
from type_define.graph import Graph, Task


def test_graph_status_rejects_unsupported_task_status():
    task = Task("invalid task", {})
    task.status = "cancelled"
    graph = Graph()
    graph.add_node(task)

    with pytest.raises(ValueError, match="unsupported status 'cancelled'"):
        graph.get_graph_status_with_id()


def test_graph_common_parents_requires_parents_on_both_nodes():
    task_a = Task("A", {})
    task_b = Task("B", {})

    with pytest.raises(ValueError, match="both nodes must have at least one parent"):
        Graph.get_co_parent_list(task_a, task_b)


def test_task_manager_honors_method_argument():
    manager = TaskManager(silent=True, method="merge")

    assert manager.method == "merge"
    assert manager.manage_method == "merge"


def test_task_manager_rejects_unsupported_method():
    with pytest.raises(ValueError, match="Unsupported task manager method 'replace'"):
        TaskManager(silent=True, method="replace")


def test_environment_initial_state_requires_running_environment():
    environment = object.__new__(VillagerBench)
    environment.running = False
    environment._virtual_debug = False

    with pytest.raises(RuntimeError, match=r"call '\.launch\(\)' first"):
        environment.get_init_state()


def test_environment_reset_requires_running_environment():
    environment = object.__new__(VillagerBench)
    environment.running = False
    environment._virtual_debug = False
    environment.logger = SimpleNamespace(info=lambda *_: None)
    environment.agent_pool = []

    with pytest.raises(RuntimeError, match=r"call '\.launch\(\)' before '\.reset\(\)'"):
        environment.reset()


def test_environment_reset_rejects_unsupported_type(monkeypatch):
    environment = object.__new__(VillagerBench)
    environment.running = True
    environment._virtual_debug = False
    environment.logger = SimpleNamespace(info=lambda *_: None)
    environment.agent_pool = []
    environment.env_type = 999
    monkeypatch.setattr(env_module.os.path, "exists", lambda *_: False)

    with pytest.raises(ValueError, match="Unsupported environment type: 999"):
        environment.reset()


def test_meta_reset_bounds_missing_load_status_and_persists_diagnostics(monkeypatch, tmp_path):
    class FakeProcess:
        pid = 4321

        @staticmethod
        def poll():
            return None

    (tmp_path / "data").mkdir()
    (tmp_path / ".cache").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env_module, "LOAD_WAIT_SECONDS", 2)
    monkeypatch.setattr(env_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(env_module.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    environment = _meta_environment()

    with pytest.raises(Exception, match="server failed to start"):
        environment.reset()

    diagnostics = json.loads((tmp_path / "data" / "meta_judger_diagnostics.json").read_text(encoding="utf-8"))
    assert diagnostics["pid"] == 4321
    assert diagnostics["exit_code"] is None
    assert diagnostics["timeout_reason"] == "load_status did not reach loaded within 2 seconds"
    assert [entry["status"] for entry in diagnostics["load_status_history"]] == ["missing", "missing"]
    assert (tmp_path / "data" / "meta_judger.stdout.log").exists()
    assert (tmp_path / "data" / "meta_judger.stderr.log").exists()


def test_meta_reset_reports_judger_exit_before_loading(monkeypatch, tmp_path):
    process = SimpleNamespace(pid=123, poll=lambda: 7)
    (tmp_path / "data").mkdir()
    (tmp_path / ".cache").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(env_module.subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(RuntimeError, match="meta judger exited before loading with code 7"):
        _meta_environment().reset()

    diagnostics = json.loads((tmp_path / "data" / "meta_judger_diagnostics.json").read_text(encoding="utf-8"))
    assert diagnostics["exit_code"] == 7


def test_meta_get_score_reads_runtime_score_path(monkeypatch, tmp_path):
    (tmp_path / "data").mkdir()
    score = {"score": 100, "end_reason": "task completed"}
    (tmp_path / "data" / "score.json").write_text(json.dumps(score), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    environment = object.__new__(VillagerBench)
    environment.env_type = env_type.meta
    environment.task_name = "meta-smoke"

    assert environment.get_score() == score


def _meta_environment():
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
    return environment


def test_dict2document_rejects_unsupported_database():
    with pytest.raises(ValueError, match="Unsupported database name 'unknown'"):
        dict2document({}, "unknown")
