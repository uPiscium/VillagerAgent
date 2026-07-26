import logging
import threading
import time
from concurrent.futures import Future
from types import SimpleNamespace

import pipeline.controller_tiny as controller_tiny
from pipeline.controller_tiny import GlobalController
from pipeline.task_manager import TaskManager
from type_define.graph import Task


def test_execute_assignments_queues_all_assigned_agents_as_one_group():
    controller, task, agents = _controller_with_task(["Alice", "Bob"], required=2)

    controller.execute_assignments([{
        "task_instance": task,
        "agent_instances": agents,
    }])

    group = controller.task_queue[0]
    assert group.task is task
    assert [agent.name for agent in group.agents] == ["Alice", "Bob"]


def test_start_execution_group_creates_one_future_per_agent():
    controller, task, agents = _controller_with_task(["Alice", "Bob"], required=2)
    controller.execute_assignments([{"task_instance": task, "agent_instances": agents}])
    controller.executor = _ExecutorStub()

    controller.start_execution_group(controller.task_queue.pop(0))

    group = controller.result_queue[0]
    assert list(group.futures) == ["Alice", "Bob"]
    assert controller.executor.submitted_agents == ["Alice", "Bob"]


def test_controller_forwards_local_runtime_config_to_base_agents(monkeypatch):
    created_agents = []
    model_configs = []

    class AgentFactory:
        LOCAL_MODEL_CONFIG_KEYS = (
            "local_model_max_attempts",
            "local_model_max_actions",
            "local_model_inter_action_delay",
        )

        def __init__(self, llm, env, data_manager, **kwargs):
            self.name = kwargs["name"]
            created_agents.append(kwargs)

    def init_model(config):
        model_configs.append(dict(config))
        return SimpleNamespace(role_name="")

    monkeypatch.setattr(controller_tiny, "BaseAgent", AgentFactory)
    monkeypatch.setattr(controller_tiny, "init_language_model", init_model)
    task_manager = SimpleNamespace()
    data_manager = SimpleNamespace()
    env = SimpleNamespace(agent_pool=[SimpleNamespace(name="Alice")])
    base_agent_config = {
        "provider": "vllm",
        "api_model": "local-model",
        "local_model_max_attempts": 7,
        "local_model_max_actions": 3,
        "local_model_inter_action_delay": 0.25,
    }

    controller = GlobalController(
        {"provider": "openai", "api_model": "controller-model"},
        task_manager,
        data_manager,
        env,
        silent=True,
        base_agent_config=base_agent_config,
    )
    controller.executor.shutdown()

    assert base_agent_config in model_configs
    assert created_agents == [{
        "name": "Alice",
        "silent": False,
        "all_tools": [],
        "local_model_max_attempts": 7,
        "local_model_max_actions": 3,
        "local_model_inter_action_delay": 0.25,
    }]


def test_execution_group_succeeds_only_after_all_agents_succeed():
    controller, task, agents = _controller_with_task(["Alice", "Bob"], required=2)
    group = _started_group(controller, task, agents)

    assert controller.finalize_execution_group(group) is True

    assert controller.task_manager.status_updates == [(task.id, Task.success, {
        "agent_results": {
            "Alice": {"status": "success", "detail": "Alice detail"},
            "Bob": {"status": "success", "detail": "Bob detail"},
        },
    })]


def test_single_agent_execution_preserves_detail_feedback_shape():
    controller, task, agents = _controller_with_task(["Alice"], required=1)
    group = _started_group(controller, task, agents)

    controller.finalize_execution_group(group)

    assert controller.task_manager.status_updates == [
        (task.id, Task.success, "Alice detail")
    ]


def test_execution_group_fails_once_when_one_agent_raises():
    controller, task, agents = _controller_with_task(["Alice", "Bob"], required=2)
    agents[1].step_error = RuntimeError("Bob failed")
    group = _started_group(controller, task, agents)

    assert controller.finalize_execution_group(group) is True

    assert len(controller.task_manager.status_updates) == 1
    task_id, status, feedback = controller.task_manager.status_updates[0]
    assert task_id == task.id
    assert status == Task.failure
    assert feedback["agent_results"]["Alice"]["status"] == "success"
    assert feedback["agent_results"]["Bob"] == {
        "status": "failure",
        "error": "Bob failed",
    }

    assert controller.finalize_execution_group(group) is True
    assert len(controller.task_manager.status_updates) == 1


def test_execution_group_waits_for_all_agents_and_fails_on_reflection():
    controller, task, agents = _controller_with_task(["Alice", "Bob"], required=2)
    agents[0].reflect_success = False
    group = _started_group(controller, task, agents, pending_agent="Bob")

    assert controller.finalize_execution_group(group) is False
    assert controller.task_manager.status_updates == []

    group.futures["Bob"].set_result(("done", "Bob detail"))
    assert controller.finalize_execution_group(group) is True
    _, status, feedback = controller.task_manager.status_updates[0]
    assert status == Task.failure
    assert feedback["agent_results"]["Alice"]["status"] == "failure"
    assert feedback["agent_results"]["Bob"]["status"] == "success"


def test_execution_group_does_not_reflect_explicit_agent_failure():
    controller, task, agents = _controller_with_task(["Alice"], required=1)
    failure_detail = {
        "final_answer": "Local model attempt budget exhausted.",
        "failure": {"reason": "model_attempt_budget_exhausted"},
    }
    agents[0].step_detail = failure_detail
    agents[0].reflect_success = True
    group = _started_group(controller, task, agents)

    assert controller.finalize_execution_group(group) is True

    assert agents[0].reflect_calls == 0
    assert controller.task_manager.status_updates == [
        (task.id, Task.failure, failure_detail)
    ]


def test_execution_group_fails_once_when_one_agent_times_out():
    controller, task, agents = _controller_with_task(["Alice", "Bob"], required=2)
    group = _started_group(controller, task, agents, pending_agent="Bob")
    group.started_at = time.time() - controller.max_task_time - 1

    assert controller.finalize_execution_group(group) is True

    assert len(controller.task_manager.status_updates) == 1
    _, status, feedback = controller.task_manager.status_updates[0]
    assert status == Task.failure
    assert feedback["agent_results"]["Bob"] == {
        "status": "timeout",
        "error": f"Task {task.description} timeout for agent Bob",
    }


def test_execution_group_terminal_transition_preserves_assignment_history():
    manager = TaskManager(silent=True)
    task = _task("Shared task", ["Alice", "Bob"], required=2)
    manager.set_task_list_from_decomposition([task])
    projected_task = manager.query_runnable_subtasks(["Alice", "Bob"])[0]
    manager.feedback_task = lambda _task: None
    controller, _, agents = _controller_with_task(["Alice", "Bob"], required=2)
    controller.task_manager = manager
    controller.task_list = [projected_task]

    controller.execute_assignments([{
        "task_instance": projected_task,
        "agent_instances": agents,
    }])
    running_node = manager.runtime_task_store.snapshot()["nodes"][0]
    assert running_node["lifecycle"]["active_agents"] == ["Alice", "Bob"]
    group = _started_group(controller, projected_task, agents, enqueue_assignment=False)
    controller.finalize_execution_group(group)

    node = manager.runtime_task_store.snapshot()["nodes"][0]
    assert node["lifecycle"]["status"] == Task.success
    assert node["lifecycle"]["active_agents"] == []
    assert node["lifecycle"]["last_assigned_agents"] == ["Alice", "Bob"]


class _AgentStub:
    def __init__(self, name):
        self.name = name
        self.step_error = None
        self.step_detail = f"{name} detail"
        self.reflect_success = True
        self.reflect_calls = 0

    def step(self, _task):
        if self.step_error is not None:
            raise self.step_error
        return "done", self.step_detail

    def reflect(self, _task, _detail):
        self.reflect_calls += 1
        return self.reflect_success


class _ExecutorStub:
    def __init__(self, pending_agent=None):
        self.pending_agent = pending_agent
        self.submitted_agents = []

    def submit(self, fn, task):
        agent = fn.__self__
        self.submitted_agents.append(agent.name)
        future = Future()
        if agent.name == self.pending_agent:
            return future
        try:
            future.set_result(fn(task))
        except Exception as exc:
            future.set_exception(exc)
        return future


class _TaskManagerStub:
    def __init__(self, task):
        self.running_updates = []
        self.status_updates = []
        self.graph = SimpleNamespace(vertex=[task])

    def mark_task_running(self, task, agent_names):
        self.running_updates.append((task.id, list(agent_names)))

    def mark_task_status(self, task_id, status, feedback=None):
        self.status_updates.append((task_id, status, feedback))

    def feedback_task(self, _task):
        return None


def _controller_with_task(agent_names, required):
    task = _task("Shared task", agent_names, required)
    agents = [_AgentStub(name) for name in agent_names]
    controller = object.__new__(GlobalController)
    controller.agent_list = agents
    controller.assignment = {}
    controller.task_list = [task]
    controller.task_queue = []
    controller.result_queue = []
    controller.task_list_lock = threading.Lock()
    controller.result_list_lock = threading.Lock()
    controller.shutdown_event = threading.Event()
    controller.task_manager = _TaskManagerStub(task)
    controller.logger = logging.getLogger("test-controller-multi-agent")
    controller.max_task_time = 30
    return controller, task, agents


def _started_group(controller, task, agents, pending_agent=None, enqueue_assignment=True):
    if enqueue_assignment:
        controller.execute_assignments([{
            "task_instance": task,
            "agent_instances": agents,
        }])
        group = controller.task_queue.pop(0)
    else:
        group = controller.task_queue.pop(0)
    controller.executor = _ExecutorStub(pending_agent=pending_agent)
    controller.start_execution_group(group)
    return controller.result_queue.pop(0)


def _task(description, candidates, required):
    task = Task(description, {})
    task.candidate_list = list(candidates)
    task.number = required
    task.available = True
    task.status = Task.unknown
    return task
