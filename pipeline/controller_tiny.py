import sys
import os
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

from model.init_model import init_language_model

sys.path.append(os.getcwd())
from type_define.graph import Task
from pipeline.task_manager import TaskManager
from pipeline.data_manager import DataManager
from pipeline.agent import BaseAgent
from pipeline.utils import *
from pipeline.controller_prompt import *
from pipeline.runtime_events import NoOpRuntimeEventSink, safe_emit_runtime_event
from env.env import VillagerBench
from env.minecraft_dual_dag import rank_minecraft_runtime_tasks
import logging


@dataclass
class TaskExecutionGroup:
    task: Task
    agents: list[BaseAgent]
    futures: dict[str, Future] = field(default_factory=dict)
    started_at: float | None = None
    submission_complete: bool = False
    completed: bool = False
    terminal_state_persisted: bool = False
    post_processing_complete: bool = False
    cancellation_tokens: dict[str, threading.Event] = field(default_factory=dict)
    timeout_detected: set[str] = field(default_factory=set)
    timeout_detected_at: dict[str, float] = field(default_factory=dict)
    cancellation_requested: set[str] = field(default_factory=set)
    cancellation_acknowledged: set[str] = field(default_factory=set)
    cancellation_forced: set[str] = field(default_factory=set)
    cancellation_requested_at: dict[str, float] = field(default_factory=dict)
    shutdown_escalated: set[str] = field(default_factory=set)
    timeout_details: dict[str, dict] = field(default_factory=dict)
    timeout_checkpoint_persisted: bool = False


class ControllerShutdownError(RuntimeError):
    pass


class GlobalController:
    '''
    Global Controller for Minecraft game agents. The task is to assign tasks to agents. Create a plan that assigns tasks to suitable agents and return a list of task-assignment JSON objects.
    
    This is a tiny version of the GlobalController, which is used for faster task assignment and execution. It is designed for the purpose of testing and debugging.
    
    Args:
    - llm_config (dict): Configuration for the language model.
    - task_manager (TaskManager): TaskManager object.
    - data_manager (DataManager): DataManager object.
    - env (VillagerBench): VillagerBench object.
    - silent (bool): Whether to suppress the log output. Default is False.
    - max_workers (int): The maximum number of workers in the thread pool. Default is 4.
    '''
    def __init__(self, llm_config: dict, task_manager: TaskManager, data_manager: DataManager, env: VillagerBench,
                 silent: bool = False, max_workers=4, tm_llm_config: dict = None, dm_llm_config: dict = None,
                 base_agent_config: dict = None, all_tools=[], minecraft_dual_dag_config: dict | None = None,
                 event_sink=None, emit_terminal_events: bool = True):

        self.task_manager = task_manager
        tm_llm_config = llm_config.copy() if tm_llm_config is None else tm_llm_config
        tm_llm_config["role_name"] = "TaskManager"
        self.task_manager.llm = init_language_model(tm_llm_config)

        self.task_manager.dm = data_manager
        self.data_manager = data_manager
        dm_llm_config = llm_config.copy() if dm_llm_config is None else dm_llm_config
        dm_llm_config["role_name"] = "DataManager"
        self.data_manager.llm = init_language_model(dm_llm_config)

        llm = init_language_model(llm_config)
        base_agent_config = llm_config.copy() if base_agent_config is None else base_agent_config
        base_llm = init_language_model(base_agent_config)
        base_agent_runtime_config = {
            key: base_agent_config[key]
            for key in BaseAgent.LOCAL_MODEL_CONFIG_KEYS
            if key in base_agent_config
        }
        self.agent_list = [
            BaseAgent(
                base_llm,
                env,
                data_manager,
                name=a.name,
                silent=False,
                all_tools=all_tools,
                **base_agent_runtime_config,
            )
            for a in env.agent_pool
        ]
        self.task_manager.agent_list = self.agent_list
        self.assignment = {}
        self.feedback = {}

        self.logger = init_logger("GlobalController", level=logging.DEBUG, dump=True, silent=silent)
        self.env = env
        self.llm = llm
        self.llm.role_name = "GlobalController"

        self.task_list = [Task]  # task published by tm
        self.query_interval = 1  # time interval between two query

        # init lock
        self.task_list_lock = threading.Lock()
        self.result_list_lock = threading.Lock()

        self.task_queue = []
        self.result_queue = []

        # init thread pool
        self.executor = ThreadPoolExecutor(max_workers=max_workers)  # 可以根据需要调整max_workers的数量

        # init max task time
        self.max_task_time = 60 * 30 # 3min

        self.shutdown_event = threading.Event()
        self._failure_lock = threading.Lock()
        self._first_failure = None
        self._controller_threads = []
        self.shutdown_grace_period = 5.0
        self.cancellation_grace_period = 5.0
        self._run_started = False
        self.minecraft_dual_dag_config = minecraft_dual_dag_config or {}
        self.event_sink = event_sink or getattr(task_manager, "event_sink", NoOpRuntimeEventSink())
        self.emit_terminal_events = emit_terminal_events
        self.task_manager.event_sink = self.event_sink

    def emit_runtime_event(self, event_type, *, entity_id=None, source, payload=None):
        safe_emit_runtime_event(getattr(self, "event_sink", NoOpRuntimeEventSink()), event_type, entity_id=entity_id, source=source, payload=payload)

    def _request_shutdown(self):
        self.shutdown_event.set()

    def _record_failure(self, name, exc):
        failure = {
            "thread": name,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        }
        with self._failure_lock:
            if self._first_failure is None:
                self._first_failure = (exc, exc.__traceback__, failure)
        self._request_shutdown()

    def _run_thread(self, name, entrypoint):
        try:
            entrypoint()
            if not self.shutdown_event.is_set():
                self._record_failure(
                    name,
                    ControllerShutdownError(f"Controller thread {name} exited before shutdown"),
                )
        except BaseException as exc:
            self._record_failure(name, exc)

    def validate_assignments(self, result: [dict]):
        validated_assignments = []
        reserved_agent_names = set()

        for assign in result:
            task_id = assign.get("task_id")
            agent_names = assign.get("agent", [])
            if isinstance(agent_names, BaseAgent):
                agent_names = [agent_names.name]
            elif isinstance(agent_names, tuple):
                agent_names = list(agent_names)
            elif not isinstance(agent_names, list):
                agent_names = [agent_names]

            # Check if task exists
            if not isinstance(task_id, int) or task_id >= len(self.task_list) or task_id < 0:
                self.logger.warning("Choose a non exist task!")
                continue

            task_instance = self.task_list[task_id]
            required_agent_count = int(task_instance.number)
            if len(agent_names) != required_agent_count or len(set(agent_names)) != len(agent_names):
                self.logger.warning(
                    f"Task {task_instance.description} requires exactly {required_agent_count} unique agent(s)!"
                )
                continue

            agent_instances = []
            assignment_is_valid = True

            # Check if agents exist and are valid for the task
            for agent_name in agent_names:
                agent = next((a for a in self.agent_list if a.name == agent_name), None)
                if agent is None:
                    self.logger.warning(f"Agent {agent_name} does not exist!")
                    assignment_is_valid = False
                    break

                if (
                    self.assignment.get(agent.name) is not None
                    or agent_name in reserved_agent_names
                    or agent_name not in task_instance.candidate_list
                ):
                    self.logger.warning(f"Agent {agent_name} is not valid for the task!")
                    assignment_is_valid = False
                    break

                agent_instances.append(agent)

            if assignment_is_valid and len(agent_instances) == required_agent_count:
                validated_assignments.append({
                    "task_instance": task_instance,
                    "agent_instances": agent_instances
                })
                reserved_agent_names.update(agent.name for agent in agent_instances)

        return validated_assignments

    
    def execute_assignments(self, validated_assignments):
        for assignment in validated_assignments:
            task_instance = assignment["task_instance"]
            agent_instances = assignment["agent_instances"]
            agent_names = [agent.name for agent in agent_instances]

            for agent in agent_instances:
                self.assignment[agent.name] = task_instance.id
                task_instance._agent.append(agent.name)

            with self.task_list_lock:
                self.task_manager.mark_task_running(task_instance, agent_names)
                task_instance.status = Task.running
                self.task_queue.append(TaskExecutionGroup(
                    task=task_instance,
                    agents=list(agent_instances),
                ))
            self.emit_runtime_event("task_assigned", entity_id=task_instance.id, source="GlobalController.execute_assignments", payload={"agents": agent_names, "required_agent_count": task_instance.number})
        
            name_list = ", ".join(agent_names)
            self.logger.info(f"Agent(s) {name_list} assigned to do task {task_instance.description}")

            # agent_env_dict = self.env.get_init_state()
            # for env_dict in agent_env_dict:
            #     self.logger.warning(str(env_dict))

            task_instance.status = Task.running

    def start_execution_group(self, group: TaskExecutionGroup, *, enqueue: bool = True) -> None:
        if enqueue:
            with self.result_list_lock:
                self.result_queue.append(group)
        group.started_at = time.time()
        for agent in group.agents:
            if self.shutdown_event.is_set():
                raise ControllerShutdownError(
                    f"Task {group.task.description} submission interrupted by controller shutdown"
                )
            with self.result_list_lock:
                supports_cancellation = getattr(
                    agent, "supports_cooperative_cancellation", None
                )
                if callable(supports_cancellation) and supports_cancellation():
                    token = threading.Event()
                    group.cancellation_tokens[agent.name] = token
                    group.futures[agent.name] = self.executor.submit(
                        agent.step,
                        group.task,
                        cancellation_token=token,
                    )
                else:
                    group.futures[agent.name] = self.executor.submit(agent.step, group.task)
            self.logger.info(f"Agent {agent.name} is executing task now ...")
        with self.result_list_lock:
            group.submission_complete = True

    def finalize_execution_group(self, group: TaskExecutionGroup, now: float | None = None) -> bool:
        if group.completed:
            return True
        if group.terminal_state_persisted:
            self._complete_execution_group(group)
            return True
        if not group.submission_complete:
            return False
        now = time.time() if now is None else now
        future_snapshots = {
            agent_name: self._snapshot_future(future)
            for agent_name, future in group.futures.items()
        }
        deadline_reached = (
            group.started_at is not None
            and now - group.started_at >= self.max_task_time
        )
        if deadline_reached:
            for agent in group.agents:
                agent_name = agent.name
                if future_snapshots[agent_name]["done"]:
                    continue
                if agent_name not in group.timeout_detected:
                    group.timeout_detected.add(agent_name)
                    group.timeout_detected_at[agent_name] = now
                    group.timeout_details[agent_name] = {
                        "status": "timeout",
                        "error": f"Task {group.task.description} timeout for agent {agent_name}",
                        "cooperative_cancellation": agent_name in group.cancellation_tokens,
                        "timeout_detected": True,
                        "shutdown_escalated": False,
                        "cancellation_requested": False,
                        "cancellation_acknowledged": False,
                        "cancellation_forced": False,
                    }

                # Completion may race with the deadline snapshot. Recheck before
                # delivering a cancellation signal or deciding work is active.
                future_snapshots[agent_name] = self._snapshot_future(
                    group.futures[agent_name]
                )
                if future_snapshots[agent_name]["done"]:
                    continue
                token = group.cancellation_tokens.get(agent_name)
                if token is not None and agent_name not in group.cancellation_requested:
                    token.set()
                    group.cancellation_requested.add(agent_name)
                    group.cancellation_requested_at[agent_name] = now
                    group.timeout_details[agent_name]["cancellation_requested"] = True

        for agent_name in group.cancellation_requested:
            snapshot = future_snapshots[agent_name]
            if snapshot["done"] and self._is_cancellation_acknowledgement(snapshot):
                group.cancellation_acknowledged.add(agent_name)
                group.timeout_details[agent_name]["cancellation_acknowledged"] = True

        active_agents = [
            agent_name
            for agent_name, snapshot in future_snapshots.items()
            if not snapshot["done"]
        ]
        if active_agents:
            if not group.timeout_detected:
                return False
            cancellation_grace_period = getattr(
                self, "cancellation_grace_period", self.shutdown_grace_period
            )
            escalation_agents = [
                agent_name
                for agent_name in active_agents
                if agent_name in group.timeout_detected
                and now - group.timeout_detected_at[agent_name] >= cancellation_grace_period
            ]
            if escalation_agents:
                for agent_name in escalation_agents:
                    future_snapshots[agent_name] = self._snapshot_future(
                        group.futures[agent_name]
                    )
                escalation_agents = [
                    agent_name
                    for agent_name in escalation_agents
                    if not future_snapshots[agent_name]["done"]
                ]
            if escalation_agents:
                for agent_name in escalation_agents:
                    group.shutdown_escalated.add(agent_name)
                    group.timeout_details[agent_name]["shutdown_escalated"] = True
                self._request_shutdown()
                names = ", ".join(sorted(escalation_agents))
                raise ControllerShutdownError(
                    f"Task {group.task.description} remained active after timeout for {names}"
                )
            if any(not snapshot["done"] for snapshot in future_snapshots.values()):
                return False

        agent_results = {}
        group_succeeded = not group.timeout_detected
        for agent in group.agents:
            snapshot = future_snapshots[agent.name]
            if agent.name in group.timeout_detected:
                agent_results[agent.name] = dict(group.timeout_details[agent.name])
                group_succeeded = False
                continue
            try:
                if snapshot["exception"] is not None:
                    raise snapshot["exception"]
                _, detail = snapshot["result"]
                explicit_failure = isinstance(detail, dict) and "failure" in detail
                reflected_success = False if explicit_failure else bool(agent.reflect(group.task, detail))
                agent_results[agent.name] = {
                    "status": "success" if reflected_success else "failure",
                    "detail": detail,
                }
                if not reflected_success:
                    group_succeeded = False
            except Exception as exc:
                self.logger.error(
                    f"Task {group.task.description} failed for agent {agent.name} with exception: {exc}"
                )
                self.logger.exception(exc)
                agent_results[agent.name] = {
                    "status": "failure",
                    "error": str(exc),
                }
                group_succeeded = False

        status = Task.success if group_succeeded else Task.failure
        if len(group.agents) == 1:
            result = agent_results[group.agents[0].name]
            feedback = (
                result
                if result.get("status") == "timeout"
                else result.get("detail", result.get("error"))
            )
        else:
            feedback = {"agent_results": agent_results}
        self.set_task_status(group.task.id, status, feedback)
        group.task.status = status
        group.terminal_state_persisted = True
        self._complete_execution_group(group)
        return True

    @staticmethod
    def _snapshot_future(future: Future) -> dict:
        if not future.done():
            return {"done": False, "cancelled": False, "result": None, "exception": None}
        if future.cancelled():
            return {"done": True, "cancelled": True, "result": None, "exception": None}
        try:
            result = future.result()
        except BaseException as exc:
            return {"done": True, "cancelled": False, "result": None, "exception": exc}
        return {"done": True, "cancelled": False, "result": result, "exception": None}

    @staticmethod
    def _is_cancellation_acknowledgement(snapshot: dict) -> bool:
        if snapshot["cancelled"] or snapshot["exception"] is not None:
            return False
        result = snapshot["result"]
        if not isinstance(result, tuple) or len(result) != 2:
            return False
        detail = result[1]
        return (
            isinstance(detail, dict)
            and isinstance(detail.get("failure"), dict)
            and detail["failure"].get("reason") == "cancelled"
            and detail["failure"].get("cancellation_acknowledged") is True
        )

    def _complete_execution_group(self, group: TaskExecutionGroup) -> None:
        for agent in self.agent_list:
            if self.assignment.get(agent.name) == group.task.id:
                self.assignment.pop(agent.name)
        self.logger.info(
            f"task {group.task.description} has been executed, the result is {group.task.status}"
        )
        self.task_manager.feedback_task(self.get_task_by_id(group.task.id))
        group.post_processing_complete = True
        group.completed = True

    # worker
    def worker(self):
        while True:
            if self.shutdown_event.is_set():
                break

            # if future.done() and task.id in [t.id for t in self.task_list] and task.status == Task.running:
            if self.env.agents_ping()["status"] == False:
                raise ControllerShutdownError("Some agents are offline")

            with self.task_list_lock:
                if not self.task_queue:
                    group = None
                else:
                    group = self.task_queue[0]
                    with self.result_list_lock:
                        self.result_queue.append(group)
                        self.task_queue.pop(0)
            if group is None:
                self.shutdown_event.wait(self.query_interval)
                continue
            self.start_execution_group(group, enqueue=False)

    def set_task_status(self, task_id, status, feedback):
        self.task_manager.mark_task_status(task_id, status, feedback)

    def get_task_by_id(self, task_id):
        for task in self.task_manager.graph.vertex:
            if task.id == task_id:
                return task
        return None
    
    def update_feedback(self, task, agent, detail):
        task.status = Task.success if agent.reflect(task, detail) else Task.failure
        # task.status = Task.success
        self.set_task_status(task.id, task.status, detail)

        for agent in self.agent_list:
            if self.assignment.get(agent.name) == task.id:
                self.assignment.pop(agent.name)
        self.logger.info(
            f"task {task.description} has been executed, the result is {task.status}")
        self.task_manager.feedback_task(self.get_task_by_id(task.id))

        return

    def update_task_status(self, task, status, detail): 
        task.status = status
        self.set_task_status(task.id, status, detail)

        for agent in self.agent_list:
            if self.assignment.get(agent.name) == task.id:
                self.assignment.pop(agent.name)

        self.logger.info(
            f"task {task.description} has been executed, the result is {task.status}")
        self.task_manager.feedback_task(self.get_task_by_id(task.id))

        return
        

    def process_completed_tasks(self):
        while True:
            if self.shutdown_event.is_set():
                break

            # if future.done() and task.id in [t.id for t in self.task_list] and task.status == Task.running:
            if self.env.agents_ping()["status"] == False:
                raise ControllerShutdownError("Some agents are offline")

            with self.result_list_lock:
                result_list_copy = []
                for index, group in enumerate(self.result_queue):

                    if self.shutdown_event.is_set():
                        result_list_copy.extend(self.result_queue[index:])
                        break
                    if self.finalize_execution_group(group):
                        self.logger.info(f"Task {group.task.description} finished!")
                    else:
                        result_list_copy.append(group)
                    self.shutdown_event.wait(self.query_interval)
                self.result_queue = result_list_copy

                
    def check_task_list_available(self):
        return [
            task for task in self.task_list
            if task.available and task.status == Task.unknown
        ]

    def assign_runnable_tasks(self):
        assigned_count = 0
        for task_id, task in enumerate(self.task_list):
            if not task.available or task.status != Task.unknown:
                continue

            eligible_agents = [
                agent
                for agent in self.agent_list
                if self.assignment.get(agent.name) is None
                and agent.name in task.candidate_list
            ]
            selected_agents = eligible_agents[:task.number]
            if len(selected_agents) != task.number:
                continue

            validated_assignments = self.validate_assignments([{
                "task_id": task_id,
                "agent": [agent.name for agent in selected_agents],
            }])
            if not validated_assignments:
                continue

            self.logger.info(
                f"Task {task.description} is assigned to {[agent.name for agent in selected_agents]}"
            )
            self.emit_runtime_event("task_selected", entity_id=task.id, source="GlobalController.assign_runnable_tasks", payload={"agents": [agent.name for agent in selected_agents], "selection_policy": getattr(self, "minecraft_dual_dag_config", {}).get("task_selection_policy", "original")})
            self.execute_assignments(validated_assignments)
            assigned_count += 1

        return assigned_count

    # 生产者
    def execute_tasks(self):
        while True:
            if self.shutdown_event.is_set():
                break

            # if future.done() and task.id in [t.id for t in self.task_list] and task.status == Task.running:
            if self.env.agents_ping()["status"] == False:
                raise ControllerShutdownError("Some agents are offline")

            open_task_list = self.task_manager.query_subtask_list()
            if open_task_list == []:
                self.logger.info("all assigned tasks are finished ...")
                self._request_shutdown()
                break

            free_agent_names = [
                agent.name for agent in self.agent_list
                if self.assignment.get(agent.name) is None
            ]
            self.task_list = self.task_manager.query_runnable_subtasks(free_agent_names)
            self.task_list = self._rank_task_list_with_minecraft_dual_dag(self.task_list)
            # 写到 logs/task_list.json 中
            agent_states = []
            for agent in self.agent_list:
                if self.assignment.get(agent.name) is None:
                    agent_states.append({"name": agent.name, "state": "free", "task": None})
                else:
                    tmp_description = ""
                    for task in self.task_list:
                        if task.id == self.assignment.get(agent.name):
                            tmp_description = task.description
                            break
                    agent_states.append({"name": agent.name, "state": "busy", "task": tmp_description})

            with open("logs/task_list.json", "w") as f:
                json.dump({
                    "agent_states": agent_states,
                    "task_list": [task.assign_json(idx) for idx, task in enumerate(self.task_list)],
                }, f, indent=4)

            if self.check_task_list_available() == []:
                # self.logger.info("no available task ...")
                self.shutdown_event.wait(self.query_interval)
                continue

            self.assign_runnable_tasks()

    def _rank_task_list_with_minecraft_dual_dag(self, task_list):
        ranked = rank_minecraft_runtime_tasks(
            task_list,
            graph=getattr(self.task_manager, "graph", None),
            action_log=self.env.get_action_log() if hasattr(self.env, "get_action_log") else None,
            config=self.minecraft_dual_dag_config,
        )
        support = ranked.get("decision_support", {})
        if ranked.get("enabled") and support.get("recommended_task_id"):
            self.logger.info(
                "Dual-DAG recommended task %s for runtime selection",
                support.get("recommended_task_id"),
            )
        self.emit_runtime_event("task_candidates_ranked", source="GlobalController._rank_task_list_with_minecraft_dual_dag", payload={"candidate_task_ids": [task.id for task in task_list], "ranked_task_ids": [task.id for task in ranked.get("tasks", task_list)], "enabled": bool(ranked.get("enabled"))})
        return ranked.get("tasks", task_list)

    def run(self):
        if self._run_started:
            raise ControllerShutdownError("Controller instances cannot be reused after run() starts")
        self._run_started = True
        self.shutdown_event.clear()
        self._first_failure = None
        self._controller_threads = [
            threading.Thread(
                name=f"controller-{name}",
                target=self._run_thread,
                args=(name, entrypoint),
                daemon=True,
            )
            for name, entrypoint in (
                ("execute_tasks", self.execute_tasks),
                ("worker", self.worker),
                ("process_completed_tasks", self.process_completed_tasks),
            )
        ]
        started_threads = []
        try:
            for thread in self._controller_threads:
                thread.start()
                started_threads.append(thread)
            self.shutdown_event.wait()
        except BaseException as exc:
            self._record_failure("run", exc)

        self._request_shutdown()
        self.executor.shutdown(wait=False, cancel_futures=True)
        deadline = time.monotonic() + self.shutdown_grace_period
        executor_threads = list(getattr(self.executor, "_threads", ()))
        for thread in [*started_threads, *executor_threads]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(remaining)

        alive_threads = [
            thread.name
            for thread in [*started_threads, *executor_threads]
            if thread.is_alive()
        ]
        (
            interrupted_task_ids,
            active_task_ids,
            active_agent_ids,
            incomplete_submission_task_ids,
            undrained_queues,
        ) = self._finalize_shutdown_groups()
        shutdown_complete = (
            not alive_threads
            and not active_task_ids
            and not incomplete_submission_task_ids
            and not undrained_queues
        )
        if not shutdown_complete or interrupted_task_ids:
            message = "Controller shutdown incomplete"
            if alive_threads:
                message += f"; live threads: {', '.join(alive_threads)}"
            if undrained_queues:
                message += f"; undrained queues: {', '.join(undrained_queues)}"
            if self._first_failure is None:
                self._record_failure("run", ControllerShutdownError(message))
            self._first_failure[2].update({
                "shutdown_complete": shutdown_complete,
                "live_threads": alive_threads,
                "undrained_queues": undrained_queues,
                "interrupted_task_ids": interrupted_task_ids,
                "active_task_ids": active_task_ids,
                "active_agent_ids": active_agent_ids,
                "incomplete_submission_task_ids": incomplete_submission_task_ids,
            })
            setattr(self._first_failure[0], "controller_shutdown_context", {
                "shutdown_complete": shutdown_complete,
                "live_threads": alive_threads,
                "undrained_queues": undrained_queues,
                "interrupted_task_ids": interrupted_task_ids,
                "active_task_ids": active_task_ids,
                "active_agent_ids": active_agent_ids,
                "incomplete_submission_task_ids": incomplete_submission_task_ids,
            })

        try:
            self.task_manager.checkpoint_runtime_state(raise_on_error=True)
        except BaseException as exc:
            if self._first_failure is not None:
                self._first_failure[2]["checkpoint_error"] = {
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
            self._record_failure("run.checkpoint", exc)

        if self._first_failure is None:
            if self.emit_terminal_events:
                self.emit_runtime_event(
                    "run_completed",
                    source="GlobalController.run",
                )
            return

        exc, exc_traceback, failure = self._first_failure
        if self.emit_terminal_events:
            self.emit_runtime_event(
                "run_failed",
                source="GlobalController.run",
                payload=failure,
            )
        raise exc.with_traceback(exc_traceback)

    def _finalize_shutdown_groups(self):
        interrupted_task_ids = []
        active_task_ids = []
        active_agent_ids = []
        incomplete_submission_task_ids = []
        undrained_queues = []
        groups = []
        for name, lock, queue in (
            ("task_queue", self.task_list_lock, self.task_queue),
            ("result_queue", self.result_list_lock, self.result_queue),
        ):
            if not lock.acquire(blocking=False):
                undrained_queues.append(name)
                continue
            try:
                groups.extend((name, group) for group in queue)
            finally:
                lock.release()

        for queue_name, group in groups:
            if group.completed or group.terminal_state_persisted:
                continue
            try:
                execution_may_still_be_active = any(
                    future.running() for future in group.futures.values()
                )
                agent_names = [agent.name for agent in group.agents]
                submitted_agent_names = list(group.futures)
                active_group_agents = [
                    agent_name
                    for agent_name, future in group.futures.items()
                    if future.running()
                ]
                feedback = {
                    "reason": "controller_shutdown",
                    "execution_may_still_be_active": execution_may_still_be_active,
                    "assigned_agents": agent_names,
                    "submitted_agents": submitted_agent_names,
                    "active_agents": active_group_agents,
                    "unsubmitted_agents": [
                        agent_name for agent_name in agent_names
                        if agent_name not in group.futures
                    ],
                    "submission_complete": group.submission_complete,
                    "agent_reuse_blocked": True,
                    "requires_agent_reconciliation": True,
                }
                if group.timeout_detected:
                    feedback.update({
                        "timeout_detected": sorted(group.timeout_detected),
                        "shutdown_escalated": sorted(group.shutdown_escalated),
                        "cancellation_requested": sorted(group.cancellation_requested),
                        "cancellation_acknowledged": sorted(group.cancellation_acknowledged),
                        "cancellation_forced": sorted(group.cancellation_forced),
                        "timeout_details": dict(group.timeout_details),
                    })
                if group.shutdown_escalated and execution_may_still_be_active:
                    feedback["reason"] = "task_timeout_shutdown_escalation"
                    if not group.timeout_checkpoint_persisted:
                        self.task_manager.mark_task_status(group.task.id, Task.running, feedback)
                        group.timeout_checkpoint_persisted = True
                else:
                    self.task_manager.mark_task_status(group.task.id, Task.failure, feedback)
                    group.task.status = Task.failure
                    group.terminal_state_persisted = True
                    group.post_processing_complete = True
                    group.completed = True
                interrupted_task_ids.append(group.task.id)
                if execution_may_still_be_active:
                    active_task_ids.append(group.task.id)
                    active_agent_ids.extend(active_group_agents)
                if not group.submission_complete:
                    incomplete_submission_task_ids.append(group.task.id)
            except BaseException as exc:
                if queue_name not in undrained_queues:
                    undrained_queues.append(queue_name)
                self._record_failure("run.finalize_shutdown", exc)
        return (
            interrupted_task_ids,
            active_task_ids,
            active_agent_ids,
            incomplete_submission_task_ids,
            undrained_queues,
        )
