import sys
import os
import threading
import time
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
    completed: bool = False


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
                 event_sink=None):

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
        self.agent_list = [BaseAgent(base_llm, env, data_manager, name=a.name, silent=False, all_tools=all_tools) for a in env.agent_pool]
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

        self.shutdown = False
        self.minecraft_dual_dag_config = minecraft_dual_dag_config or {}
        self.event_sink = event_sink or getattr(task_manager, "event_sink", NoOpRuntimeEventSink())
        self.task_manager.event_sink = self.event_sink

    def emit_runtime_event(self, event_type, *, entity_id=None, source, payload=None):
        safe_emit_runtime_event(getattr(self, "event_sink", NoOpRuntimeEventSink()), event_type, entity_id=entity_id, source=source, payload=payload)

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

    def start_execution_group(self, group: TaskExecutionGroup) -> None:
        group.started_at = time.time()
        for agent in group.agents:
            group.futures[agent.name] = self.executor.submit(agent.step, group.task)
            self.logger.info(f"Agent {agent.name} is executing task now ...")
        with self.result_list_lock:
            self.result_queue.append(group)

    def finalize_execution_group(self, group: TaskExecutionGroup, now: float | None = None) -> bool:
        if group.completed:
            return True
        now = time.time() if now is None else now
        timed_out = (
            group.started_at is not None
            and now - group.started_at > self.max_task_time
            and not all(future.done() for future in group.futures.values())
        )
        if not timed_out and not all(future.done() for future in group.futures.values()):
            return False

        agent_results = {}
        group_succeeded = not timed_out
        for agent in group.agents:
            future = group.futures[agent.name]
            if timed_out and not future.done():
                future.cancel()
                agent_results[agent.name] = {
                    "status": "timeout",
                    "error": f"Task {group.task.description} timeout for agent {agent.name}",
                }
                group_succeeded = False
                continue

            try:
                _, detail = future.result()
                reflected_success = bool(agent.reflect(group.task, detail))
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
            feedback = result.get("detail", result.get("error"))
        else:
            feedback = {"agent_results": agent_results}
        self.update_task_status(group.task, status, feedback)
        group.completed = True
        return True

    # worker
    def worker(self):
        while True:
            if self.shutdown:
                break

            # if future.done() and task.id in [t.id for t in self.task_list] and task.status == Task.running:
            if self.env.agents_ping()["status"] == False:
                self.logger.info("Some agents are offline!")
                self.shutdown = True
                break
                
            with self.task_list_lock:
                if not self.task_queue:
                    time.sleep(self.query_interval)
                    continue
                while self.task_queue:
                    group = self.task_queue.pop(0)
                    self.start_execution_group(group)
                    # time.sleep(self.query_interval)

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
            if self.shutdown:
                break

            # if future.done() and task.id in [t.id for t in self.task_list] and task.status == Task.running:
            if self.env.agents_ping()["status"] == False:
                self.logger.info("Some agents are offline!")
                self.shutdown = True
                break

            with self.result_list_lock:
                result_list_copy = []
                for group in self.result_queue:

                    if self.shutdown:
                        break
                    if self.finalize_execution_group(group):
                        self.logger.info(f"Task {group.task.description} finished!")
                    else:
                        result_list_copy.append(group)
                    time.sleep(self.query_interval)
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
        try:
            while True:
                if self.shutdown:
                    break

                # if future.done() and task.id in [t.id for t in self.task_list] and task.status == Task.running:
                if self.env.agents_ping()["status"] == False:
                    self.logger.info("Some agents are offline!")
                    self.shutdown = True
                    break

                open_task_list = self.task_manager.query_subtask_list()
                if open_task_list == []:
                    self.logger.info("all assigned tasks are finished ...")
                    self.shutdown = True
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
                    # sleep
                    time.sleep(self.query_interval)
                    continue

                self.assign_runnable_tasks()

        except KeyboardInterrupt:
            self.shutdown = True
            self.task_manager = None
            self.data_manager = None
            self.executor.shutdown(wait=False)
            raise Exception("Interrupted by user")

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
        try:
            # generate threads
            task_thread = threading.Thread(target=self.execute_tasks)
            worker_thread = threading.Thread(target=self.worker)
            result_thread = threading.Thread(target=self.process_completed_tasks)
            # start threads
            task_thread.start()
            worker_thread.start()
            result_thread.start()
            # wait for threads to finish
            task_thread.join()
            worker_thread.join()
            result_thread.join()
        except KeyboardInterrupt:
            # shutdown
            self.shutdown = True
            self.task_manager = None
            self.data_manager = None

            self.executor.shutdown(wait=False)
            # raise exception
            raise Exception("Interrupted by user")
