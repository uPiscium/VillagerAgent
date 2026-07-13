import pytest

from pipeline.agent import BaseAgent
from type_define.graph import Task


class FakeEnv:
    running = True

    def __init__(self, *, failures_before_success=0):
        self.failures_before_success = failures_before_success
        self.step_calls = 0
        self.agent_status_calls = 0
        self.last_task_prompt = ""

    def step(self, name, task_prompt):
        self.step_calls += 1
        self.last_task_prompt = task_prompt
        if self.step_calls <= self.failures_before_success:
            raise RuntimeError(f"step failed {self.step_calls}")
        return "done", {"action_list": [], "final_answer": "done"}

    def agent_status(self, name):
        self.agent_status_calls += 1
        return {"status": True, "message": {"my_name": name}}


class FakeDataManager:
    def __init__(self):
        self.updated = []

    def query_env_with_task(self, description, agent_query=False):
        return "env summary"

    def query_history(self, name):
        return "agent history"

    def query_other_agent_state(self, name):
        return "no other agents"

    def update_database(self, payload):
        self.updated.append(payload)


def test_base_agent_normal_step_raises_original_error_after_retry_exhaustion(monkeypatch):
    monkeypatch.setattr("pipeline.agent.time.sleep", lambda seconds: None)
    env = FakeEnv(failures_before_success=3)
    dm = FakeDataManager()
    agent = BaseAgent(llm=object(), env=env, data_manager=dm, name="Alice", silent=True)
    task = Task("Inspect area", {"document": "public"})
    task._agent = ["Alice"]

    with pytest.raises(RuntimeError, match="step failed 3"):
        agent.normal_step(task)

    assert env.step_calls == 3
    assert env.agent_status_calls == 0
    assert dm.updated == []
    assert agent.IDLE is True


def test_base_agent_normal_step_updates_database_after_success(monkeypatch):
    monkeypatch.setattr("pipeline.agent.time.sleep", lambda seconds: None)
    env = FakeEnv(failures_before_success=2)
    dm = FakeDataManager()
    agent = BaseAgent(llm=object(), env=env, data_manager=dm, name="Alice", silent=True)
    task = Task("Inspect area", {"document": "public"})
    task._agent = ["Alice"]

    feedback, detail = agent.normal_step(task)

    assert feedback == "done"
    assert detail["final_answer"] == "done"
    assert env.step_calls == 3
    assert env.agent_status_calls == 1
    assert len(dm.updated) == 1
    assert dm.updated[0]["detail"] == detail
    assert agent.IDLE is True


def test_base_agent_normal_step_truncates_long_task_content(monkeypatch):
    monkeypatch.setattr("pipeline.agent.time.sleep", lambda seconds: None)
    env = FakeEnv()
    dm = FakeDataManager()
    agent = BaseAgent(llm=object(), env=env, data_manager=dm, name="Alice", silent=True)
    task = Task("Inspect area", {"document": "visible-start" + ("x" * 20000) + "visible-tail"})
    task._agent = ["Alice"]

    agent.normal_step(task)

    assert "visible-tail" not in env.last_task_prompt
    assert "..." in env.last_task_prompt
    assert "*** The relevant data of task(not environment data)***" in env.last_task_prompt
