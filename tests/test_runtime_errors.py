import importlib
import sys
import types
from types import SimpleNamespace

import pytest

import env.env as env_module
from env.env import VillagerBench
from env.minecraft_client import Agent
from env.minecraft_define import MinecraftEvent
from pipeline.agent import BaseAgent
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


def test_dict2document_rejects_unsupported_database():
    with pytest.raises(ValueError, match="Unsupported database name 'unknown'"):
        dict2document({}, "unknown")


def test_rl_step_rejects_invalid_model_action_index():
    agent = object.__new__(BaseAgent)
    agent.name = "Alice"
    agent.data_manager = SimpleNamespace(
        query_env_with_task=lambda *_args, **_kwargs: "environment"
    )
    agent.env = SimpleNamespace(agents_ping=lambda: {"status": True})
    agent.rl_model = SimpleNamespace(take_action=lambda _state: 2)
    agent.rl_env = SimpleNamespace(available_actions=["move", "mine"])

    task = SimpleNamespace(description="Mine stone", milestones=[], content={})

    with pytest.raises(ValueError, match="invalid action index 2.*integer from 0 to 1"):
        agent.rl_step(task)


def test_entity_query_rejects_unsupported_query_type(monkeypatch):
    env_api = _import_env_api_with_fake_javascript(monkeypatch)

    with pytest.raises(ValueError, match="Unsupported entity query type 'display_name'"):
        env_api.get_entity_by("display_name", {"entities": {}}, "villager")


def test_unequip_rejects_unsupported_destination(monkeypatch):
    env_api = _import_env_api_with_fake_javascript(monkeypatch)
    bot = SimpleNamespace(unequip=lambda _destination: pytest.fail("bot should not be called"))

    with pytest.raises(ValueError, match="Invalid unequip destination 'back'"):
        env_api.unequip(bot, "back")


@pytest.mark.parametrize("method_name", ["step", "run"])
def test_minecraft_agent_requires_api_keys(monkeypatch, method_name):
    monkeypatch.setattr(Agent, "api_key_list", [])
    agent = Agent("nobody")

    with pytest.raises(RuntimeError, match=rf"set Agent\.api_key_list before calling '{method_name}\(\)'"):
        getattr(agent, method_name)("test instruction")


@pytest.mark.parametrize("method_name", ["step", "run"])
def test_minecraft_agent_rejects_unsupported_model(monkeypatch, method_name):
    monkeypatch.setattr(Agent, "api_key_list", ["test-key"])
    agent = Agent("nobody", model="unknown-model")

    with pytest.raises(ValueError, match=rf"Unsupported Minecraft Agent model 'unknown-model' for '{method_name}\(\)'"):
        getattr(agent, method_name)("test instruction")


class _EventBot:
    def blockAt(self, _position):
        return SimpleNamespace(
            name="stone",
            position=SimpleNamespace(x=0, y=0, z=0),
            type=1,
            _properties={
                "open": None,
                "facing": None,
                "face": None,
                "axis": None,
                "part": None,
                "hinge": None,
                "powered": None,
            },
        )


def test_minecraft_event_rejects_unsupported_activation_mode():
    condition = [{"position": [0, 0, 0], "activate_mode": "edge"}]

    with pytest.raises(ValueError, match="Unsupported Minecraft event activate_mode 'edge'"):
        MinecraftEvent(_EventBot(), lambda *position: position, condition, [])


def test_minecraft_event_reports_mutated_activation_mode():
    condition = [{"position": [0, 0, 0], "activate_mode": "level"}]
    event = MinecraftEvent(_EventBot(), lambda *position: position, condition, [])
    condition[0]["activate_mode"] = "edge"

    with pytest.raises(RuntimeError, match="condition has invalid activate_mode 'edge'"):
        event.event_update()


def _import_env_api_with_fake_javascript(monkeypatch):
    original_stdout = sys.stdout
    fake_javascript = types.ModuleType("javascript")
    fake_javascript.require = lambda *args, **kwargs: None
    fake_javascript.On = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "javascript", fake_javascript)
    monkeypatch.delitem(sys.modules, "env.env_api", raising=False)
    module = importlib.import_module("env.env_api")
    monkeypatch.setattr(sys, "stdout", original_stdout)
    return module
