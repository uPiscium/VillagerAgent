from env.minecraft_client import Agent
from contextlib import contextmanager
from copy import copy
from functools import wraps
import traceback
import names
import subprocess
import json
import time
import os
from env.utils import init_logger
import logging
from pathlib import Path

from env.runtime_paths import RuntimePaths, atomic_write_json, read_json_artifact


LOAD_WAIT_SECONDS = 160


class env_type:
    none = -1
    construction = 0
    farming = 1
    puzzle = 2
    auto = 3

    meta = 10
    gen = 13

class VillagerBench:
    '''
    VillagerBench is the environment for the Minecraft task
    
    Args:
    - env_type: int, the type of the environment, 0 for construction, 1 for farming, 2 for puzzle, -1 for none (this is for pure agent environment, no judger will be launched)
    - task_id: int, the id of the task, different task_id means different task in the same scenario
    - dig_needed: bool, whether the agent need to dig the block
    - host: str, the host of the minecraft server
    - port: int, the port of the minecraft server default 25565
    - max_task_num: int, the max task number for the puzzle task
    - task_name: str, the name of the task
    - _virtual_debug: bool, whether the environment is in virtual debug mode
    '''
    def __init__(self, env_type, task_id: int, dig_needed: bool, host: str = "0.0.0.0", port: int = 25565, max_task_num: int = 1, task_name: str = "test", _virtual_debug: bool = False, runtime_paths: RuntimePaths | None = None):
        self.env_type = env_type
        self.task_id = task_id
        self.host = host
        self.port = port
        self.task_name = task_name
        self.runtime_paths = runtime_paths or RuntimePaths.legacy()
        self.runtime_paths.ensure_directories()
        self._invalid_status_reads = 0
        self.agent_pool = []
        self.log = {}
        self.reset_token()
        self.running = False
        self._virtual_debug = _virtual_debug
        self.logger = init_logger(name="Env", level=logging.DEBUG)
        self.max_task_num = max_task_num  # For puzzle
        self.dig_needed = dig_needed  # For construction
        self.launch_time = None
        self.langchain_model = ""
        self.base_port = 5000
        self.op_path = ""
        self.meta_diagnostics_dir = None
        self._tool_action_enter = lambda: None
        self._tool_action_exit = lambda: None
        atomic_write_json(self.runtime_paths.score, {})
        atomic_write_json(self.runtime_paths.action_log, {})
        atomic_write_json(self.runtime_paths.llm_inference, {"time": 0})
        atomic_write_json(self.runtime_paths.state, {"state": "idle"})
        
        # 删除之前的log
        if self.runtime_paths.logs_dir.exists():
            for file_path in self.runtime_paths.logs_dir.iterdir():
                if not file_path.is_file():
                    continue
                for _ in range(3):  # 尝试3次
                    try:
                        file_path.unlink()
                        break  # 成功删除，跳出循环
                    except Exception as e:
                        print(f"删除失败：{e}")
                        time.sleep(1)  # 等待1秒再次尝试
                else:
                    print(f"无法删除文件 {file_path}，可能仍然被锁定。")

    def _paths(self) -> RuntimePaths:
        return getattr(self, "runtime_paths", RuntimePaths.legacy())
          
    @contextmanager
    def run(self, server_debug: bool = False, fast_api=False):
        try:
            if not self._virtual_debug:
                self.launch(debug=server_debug, fast_api=fast_api)
                self.logger.info(f"[env launched at {self.host}]")
            else:
                self.logger.info("[virtual debug mode, env not launched]")
            self.launch_time = time.time()
            yield
        except Exception as e:
            tb = traceback.format_exc()
            self.logger.error(f"Exception occurred: {e}\n{tb}")
            self.stop()
            raise
        finally:
            self.stop()
            paths = self._paths()
            state_result = read_json_artifact(paths.state)
            if state_result.state == "valid" and isinstance(state_result.value, dict):
                state = state_result.value
                state["state"] = "idle"
                atomic_write_json(paths.state, state)
            if paths.env_cache.exists():
                atomic_write_json(paths.env_cache, [])

    def stop(self):
        if self.running:
            self.running = False
            Agent.kill()

    def virtual_env(name: str):
        env = {
            "I_held_item": {
                "spruce_planks": 1
            },
            "sign": "text",
            "blocks": [
                {
                    "spruce_planks": [
                        -3,
                        -60,
                        0
                    ]
                }
            ],
            "equipment": "hidden",
            "food": 20,
            "health": 20,
            "my_name": name,
            "my_position": [
                -1,
                -59,
                1
            ],
            "nearby_entities": [

            ],
            "oxygen": 20,
            "saturation": 2,
            "timeOfDay": "sunrise"
        }

        env = {
            "message": env,
            "status": True
        }
        return env
    
    def get_total_time(self):
        if self.launch_time is None:
            return 0
        return time.time() - self.launch_time
    
    def get_token_info(self):
        token_result = read_json_artifact(self._paths().tokens)
        if token_result.state == "valid":
            return token_result.value
        else:
            return {"message": "token info not found", "status": False}
    
    def get_action_log(self):
        action_result = read_json_artifact(self._paths().action_log)
        if action_result.state == "valid":
            return action_result.value
        else:
            return {"message": "action log not found", "status": False}
        
    def get_init_state(self) -> [dict]:
        if not self.running and not self._virtual_debug:
            raise RuntimeError("Environment is not running; call '.launch()' first")
        if self.running:
            return [self.agent_status(agent.name) for agent in self.agent_pool]
        else:
            return [VillagerBench.virtual_env(agent.name) for agent in self.agent_pool]

    def reset_token(self):
        tokens = {}
        current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        tokens["dates"] = current_time
        tokens["tokens_used"] = 0
        tokens["prompt_tokens"] = 0
        tokens["completion_tokens"] = 0
        tokens["successful_requests"] = 0
        tokens["total_cost"] = 0
        tokens["action_cost"] = 0
        atomic_write_json(self._paths().tokens, tokens)

    def get_all_agent_description(self) -> dict:
        agent_dict = {}
        for agent in self.agent_pool:
            tools = agent.tools
            tool_dict = {}
            for tool in tools:
                tool_dict[tool.name] = tool.description
            agent_dict[agent.name] = tool_dict
        return agent_dict


    def get_all_agent_description_tiny(self) -> dict:
        agent_dict = {}
        for agent in self.agent_pool:
            tools = agent.tools
            tool_list = []
            for tool in tools:
                tool_list.append(tool.name)
            agent_dict[agent.name] = tool_list
        
        # 分成共有和私有两部分，共有是所有agent tools的交集，私有是每个agent的独有tools
        public_tools = []
        private_tools = {}
        # 交集

        for agent in self.agent_pool:
            if len(public_tools) == 0:
                public_tools = agent_dict[agent.name]
            else:
                public_tools = list(set(public_tools).intersection(set(agent_dict[agent.name])))
        
        for agent in self.agent_pool:
            private_tools[agent.name] = list(set(agent_dict[agent.name]) - set(public_tools))
        

        return {"public_tools": public_tools, "private_tools": private_tools}
    

    def agent_describe(self, agent_name: str):
        for agent in self.agent_pool:
            if agent.name == agent_name:
                tools = agent.tools
                tool_dict = {}
                description = f"agent {agent_name} has tools:"
                for tool in tools:
                    tool_dict[tool.name] = tool.description
                    description += f" {tool.name}, {tool.description}\n"
                return tool_dict, description
        return {}, f"agent {agent_name} not found"

    def agents_ping(self):
        try:
            for agent in self.agent_pool:
                Agent.ping(agent.name)
        except:
            return {"message": "some agent not found", "status": False}
        return {"message": "all agents are online", "status": True}

    def agent_status(self, agent_name: str):  # 返回一个dict
        for agent in self.agent_pool:
            if agent.name == agent_name:
               return Agent.get_environment_info_dict(agent_name)
        return {"message": f"agent {agent_name} not found", "status": False}

    def agent_register(self, agent_tool=None, agent_number: int = 1, name_list: list[str] | None = None):
        '''
        register the agent to the environment
        '''
        agent_tool = self.guard_tool_actions(agent_tool or ())
        name_list = list(name_list or ())
        if len(name_list) != agent_number:
            self.logger.warning(
                "[warning but dont worry] agent number not equal to names number, random names will be used")
            name_list = [names.get_first_name() for i in range(agent_number)]

        for i in range(agent_number):
            agent = Agent(
                name_list[i],
                tools=agent_tool,
                local_port=self.base_port + len(self.agent_pool),
                model=self.langchain_model,
                runtime_paths=self.runtime_paths,
            )
            agent.reflection_output_dir = self.runtime_paths.run_result_dir(self.task_name)
            if len(agent_tool) != 0:
                agent.tool = agent_tool
            self.agent_pool.append(agent)
            self.log[agent.name] = []

    def configure_tool_action_barrier(self, enter, exit) -> None:
        self._tool_action_enter = enter
        self._tool_action_exit = exit

    def guard_tool_actions(self, tools) -> list:
        return [self._guard_tool_action(tool) for tool in tools]

    def _guard_tool_action(self, tool):
        original = getattr(tool, "func", None)
        if not callable(original):
            return tool
        guarded_tool = copy(tool)

        @wraps(original)
        def guarded(*args, **kwargs):
            self._tool_action_enter()
            try:
                return original(*args, **kwargs)
            finally:
                self._tool_action_exit()

        guarded_tool.func = guarded
        return guarded_tool

    def launch(self, debug: bool = False, fast_api=False):
        Agent.launch(
            host=self.host,
            port=self.port,
            debug=debug,
            fast=fast_api,
            runtime_paths=self.runtime_paths,
        )
        self.running = True
        self.reset()

    def reset(self):
        if self._virtual_debug:
            return
        self.logger.info("resetting...")
        paths = self._paths()
        if paths.load_status.exists():
            atomic_write_json(paths.load_status, {"status": "loading"})
        self.logger.info("waiting for server to start...")
        agent_names = [agent.name for agent in self.agent_pool]
        agent_names_str = ",".join(agent_names)
        if not self.running:
            raise RuntimeError("Environment is not running; call '.launch()' before '.reset()'")
        judger_env = paths.subprocess_environment()

        if self.env_type == env_type.construction:
            if self.dig_needed:
                subprocess.Popen(["python", "env/build_judger.py", "--idx", str(self.task_id), "--host", self.host, "--port" , str(self.port), "--agent_num", str(len(self.agent_pool)), "--dig_needed","true", "--agent_names", agent_names_str, "--task_name", self.task_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=judger_env)
                self.logger.debug(f"python env/build_judger.py --idx {self.task_id} --host {self.host} --port {self.port} --dig_needed true --agent_num {len(self.agent_pool)} --agent_names {agent_names_str} --task_name {self.task_name}")
            else:
                subprocess.Popen(["python", "env/build_judger.py", "--idx", str(self.task_id), "--host", self.host, "--port" , str(self.port), "--agent_num", str(len(self.agent_pool)), "--agent_names", agent_names_str, "--task_name", self.task_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=judger_env)
                self.logger.debug(f"python env/build_judger.py --idx {self.task_id} --host {self.host} --port {self.port} --agent_num {len(self.agent_pool)} --agent_names {agent_names_str} --task_name {self.task_name}")
        elif self.env_type == env_type.farming:
            subprocess.Popen(["python", "env/farm_craft_judger.py", "--idx", str(self.task_id), "--host", self.host, "--port" , str(self.port), "--agent_num", str(len(self.agent_pool)), "--agent_names", agent_names_str, "--task_name", self.task_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=judger_env)
            self.logger.debug(f"python env/farm_craft_judger.py --idx {self.task_id} --host {self.host} --port {self.port} --agent_num {len(self.agent_pool)} --agent_names {agent_names_str} --task_name {self.task_name}")
        elif self.env_type == env_type.puzzle:
            subprocess.Popen(["python", "env/escape_room_judger.py", "--idx", str(self.task_id), "--host", self.host, "--port" , str(self.port), "--max_task_num", str(self.max_task_num), "--agent_num", str(len(self.agent_pool)), "--agent_names", agent_names_str, "--task_name", self.task_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=judger_env)
            self.logger.debug(f"python env/escape_room_judger.py --idx {self.task_id} --host {self.host} --port {self.port} --max_task_num {self.max_task_num} --agent_num {len(self.agent_pool)} --agent_names {agent_names_str} --task_name {self.task_name}")
        elif self.env_type == env_type.auto:
            subprocess.Popen(["python", "env/auto_judger.py", "--idx", str(self.task_id), "--host", self.host, "--port" , str(self.port), "--agent_num", str(len(self.agent_pool)), "--agent_names", agent_names_str, "--task_name", self.task_name, "--op_path", self.op_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=judger_env)
            self.logger.debug(f"python env/auto_judger.py --idx {self.task_id} --host {self.host} --port {self.port} --agent_num {len(self.agent_pool)} --agent_names {agent_names_str} --task_name {self.task_name} --op_path {self.op_path}")
        elif self.env_type == env_type.meta:
            command = ["python", "env/meta_judger.py", "--idx", str(self.task_id), "--host", self.host, "--port" , str(self.port), "--agent_num", str(len(self.agent_pool)), "--agent_names", agent_names_str, "--task_name", self.task_name, "--runtime-root", str(paths.root.resolve()), "--runtime-layout", paths.layout]
            diagnostics_dir = Path(getattr(self, "meta_diagnostics_dir", None) or paths.data_dir)
            diagnostics_dir.mkdir(parents=True, exist_ok=True)
            stdout_path = str(diagnostics_dir / "meta_judger.stdout.log")
            stderr_path = str(diagnostics_dir / "meta_judger.stderr.log")
            with open(stdout_path, "wb") as stdout, open(stderr_path, "wb") as stderr:
                judger_process = subprocess.Popen(
                    command,
                    stdout=stdout,
                    stderr=stderr,
                    env=paths.subprocess_environment(),
                )
            diagnostics = {
                "command": command,
                "pid": judger_process.pid,
                "stdout_path": stdout_path,
                "stderr_path": stderr_path,
                "load_status_history": [],
                "exit_code": None,
                "timeout_reason": None,
            }
            self._write_meta_judger_diagnostics(diagnostics)
            self.logger.debug(f"python env/meta_judger.py --idx {self.task_id} --host {self.host} --port {self.port} --agent_num {len(self.agent_pool)} --agent_names {agent_names_str} --task_name {self.task_name}")
        elif self.env_type == env_type.gen:
            subprocess.Popen(["python", "env/llm_gen_judger.py", "--host", self.host, "--port" , str(self.port), "--agent_num", str(len(self.agent_pool)), "--agent_names", agent_names_str, "--task_name", self.task_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=judger_env)
            self.logger.debug(f"python env/llm_gen_judger.py --host {self.host} --port {self.port} --agent_num {len(self.agent_pool)} --agent_names {agent_names_str} --task_name {self.task_name}")
        elif self.env_type == env_type.none:
            self.logger.info("no env type specified, only agent will be launched")
            return
        else:
            raise ValueError(f"Unsupported environment type: {self.env_type!r}")
        max_wait_num = LOAD_WAIT_SECONDS
        loaded = False
        while max_wait_num:
            time.sleep(1)
            max_wait_num -= 1
            try:
                if max_wait_num % 30 == 0 and max_wait_num != 120:
                    self.logger.info(f"waiting for server to start, guess the server is starting this task for the first time, please wait")
                status_result = read_json_artifact(paths.load_status)
                if status_result.state == "absent":
                    if self.env_type == env_type.meta:
                        diagnostics["load_status_history"].append({"status": "missing", "time": time.time()})
                        diagnostics["exit_code"] = judger_process.poll()
                        self._write_meta_judger_diagnostics(diagnostics)
                        if diagnostics["exit_code"] is not None:
                            raise RuntimeError(f"meta judger exited before loading with code {diagnostics['exit_code']}")
                    continue
                if status_result.state == "invalid":
                    self._invalid_status_reads = getattr(self, "_invalid_status_reads", 0) + 1
                    if self._invalid_status_reads >= 3:
                        raise RuntimeError(
                            f"load status remained invalid: {status_result.error}"
                        )
                    continue
                self._invalid_status_reads = 0
                status_data = status_result.value
                if self.env_type == env_type.meta:
                    phase = None
                    phase_path = paths.meta_judger_phase
                    if phase_path.exists():
                        with phase_path.open("r", encoding="utf-8") as f:
                            phase = f.read().strip() or None
                    diagnostics["load_status_history"].append({
                        "status": status_data.get("status"),
                        "phase": phase,
                        "time": time.time(),
                    })
                    diagnostics["load_phase"] = phase
                    diagnostics["exit_code"] = judger_process.poll()
                    self._write_meta_judger_diagnostics(diagnostics)
                    if diagnostics["exit_code"] is not None and status_data.get("status") != "loaded":
                        raise RuntimeError(f"meta judger exited before loading with code {diagnostics['exit_code']}")
                if status_data["status"] == "loaded":
                    self.logger.info("server started in background")
                    loaded = True
                    break
            except RuntimeError:
                raise
            except Exception as exc:
                raise Exception("server failed to start") from exc
        if not loaded:
            if self.env_type == env_type.meta:
                diagnostics["exit_code"] = judger_process.poll()
                diagnostics["timeout_reason"] = f"load_status did not reach loaded within {LOAD_WAIT_SECONDS} seconds"
                self._write_meta_judger_diagnostics(diagnostics)
            raise Exception("server failed to start")

    def _write_meta_judger_diagnostics(self, diagnostics):
        diagnostics_dir = Path(getattr(self, "meta_diagnostics_dir", None) or self._paths().data_dir)
        atomic_write_json(diagnostics_dir / "meta_judger_diagnostics.json", diagnostics)
    
    def get_msg(self, agent_name: str):
        '''
        get the message of the agent
        '''
        if self.running:
            return Agent.getMsg(agent_name)
        else:
            return {"message": "env not running", "status": False}
    
    def chat(self, from_agent: str, to_agent: str, message: str):
        '''
        chat with other agent
        '''
        if self.running:
            msg_instruction = f"/msg {to_agent} {message}"
            for agent in self.agent_pool:
                if agent.name == from_agent:
                    agent.run(msg_instruction)
                    return {"message": "success", "status": True}
            return {"message": "agent not found", "status": False}
        else:
            return {"message": "env not running", "status": False}

    def step(self, agent_name: str, action: str, max_turn: int = 7):
        '''
        final_answer, {"input": response["input"], "action_list": action_list, "final_answer": final_answer}
        '''
        self.logger.debug("=" * 20 + " Env Step " + "=" * 20)
        self.logger.info(f"agent {agent_name}")
        self.logger.info("=" * 20 + " Env Step " + "=" * 20)
        find_agent = False
        for agent in self.agent_pool:
            if agent.name == agent_name:
                feedback, detail = agent.run(action, max_iterations=max_turn)

                self.log[agent_name].append(detail)

                return feedback, detail

        if not find_agent:
            self.logger.warning(f"agent {agent_name} not found")
            return None, {"input": None, "action_list": None, "final_answer": None}
        
    def iter_step(self, agent_name: str, instruction: str, actions: [], observations: [], recommended_actions: []):
        '''
        final_answer, {"input": response["input"], "action_list": action_list, "final_answer": final_answer}
        '''
        self.logger.debug("=" * 20 + " Env Step (iter) " + "=" * 20)
        self.logger.info(f"agent {agent_name}")
        self.logger.info("=" * 20 + " Env Step (iter)" + "=" * 20)
        find_agent = False
        for agent in self.agent_pool:
            if agent.name == agent_name:
                feedback, detail = agent.step(instruction, actions=actions, observations=observations, recommended_actions=recommended_actions)

                self.log[agent_name].append(detail)

                return feedback, detail

        if not find_agent:
            self.logger.warning(f"agent {agent_name} not found")
            return (None, None), {"input": None, "action_list": None, "final_answer": None}

    def get_metadata(self):
        if self.env_type == env_type.construction:
            with self._paths().map_description.open("r", encoding="utf-8") as f:
                metadata = json.load(f)
            return metadata

    def get_score(self):
        if self.env_type in (env_type.construction, env_type.farming, env_type.puzzle, env_type.meta):
            paths = self._paths()
            score_result = read_json_artifact(paths.score)
            if score_result.state != "valid":
                raise RuntimeError(
                    f"score artifact is {score_result.state}: {score_result.error or paths.score}"
                )
            return score_result.value

    def is_task_complete(self):
        if self.env_type != env_type.meta:
            return False
        status_result = read_json_artifact(self._paths().load_status)
        if status_result.state == "absent":
            return False
        if status_result.state == "invalid":
            self._invalid_status_reads = getattr(self, "_invalid_status_reads", 0) + 1
            if self._invalid_status_reads >= 3:
                raise RuntimeError(f"load status remained invalid: {status_result.error}")
            return False
        self._invalid_status_reads = 0
        return isinstance(status_result.value, dict) and status_result.value.get("status") == "end"


if __name__ == "__main__":

    try:

        env = VillagerBench(env_type.construction, 0)
        agent_tool = [Agent.place_item, Agent.open_container, Agent.dig_block, Agent.find_item]
        env.agent_register(agent_tool=agent_tool, agent_number=2)
        agent_tool = [Agent.place_item, Agent.open_container, Agent.dig_block, Agent.find_item]
        env.agent_register(agent_tool=agent_tool, agent_number=2)
        env.launch()

        feedback, detail = env.step(env.agent_pool[0].name, "open chest and get 1 dirt ")
        status = env.agent_status(env.agent_pool[0].name)

        env.get_score()

    finally:
        Agent.kill()
