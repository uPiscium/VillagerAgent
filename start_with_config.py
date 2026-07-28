import argparse
import json
import math
import os
import sys
import time
import inspect
from functools import wraps
from env.env import VillagerBench, env_type, Agent
from model.init_model import init_language_model
from model.ollama_config import make_ollama_llm_config, configure_ollama_agent, load_agent_api_key_list
from env.runtime_paths import RuntimePaths, atomic_write_json
from env.judger_artifacts import ScoreOwnershipError, validate_score_identity

start_time = time.time()
from pipeline.controller_tiny import GlobalController
from pipeline.data_manager import DataManager
from pipeline.task_manager import TaskManager


def _task_graph_snapshot(graph) -> dict:
    return {
        "artifact_generation_mutates_runtime": False,
        "mutates_runtime": False,
        "projection": "type_define.Graph compatibility projection",
        "tasks": [task.to_json() for task in getattr(graph, "vertex", [])],
        "edges": [
            {"source": start.description, "target": end.description}
            for start, end in getattr(graph, "edge", [])
        ],
    }


class JudgedRuntimeValidationError(RuntimeError):
    pass


def _runtime_result(env=None, tm=None, controller=None, *, error: str | None = None, attempt_id: str | None = None, task_name: str | None = None) -> dict:
    runtime_store = getattr(tm, "runtime_task_store", None) if tm is not None else None
    runtime_snapshot = runtime_store.snapshot() if runtime_store is not None else {}
    task_graph_snapshot = _task_graph_snapshot(tm.graph) if tm is not None and hasattr(tm, "graph") else {}
    score = env.get_score() if env is not None and hasattr(env, "get_score") else {}
    return {
        "score": score,
        "attempt_id": attempt_id,
        "task_name": task_name,
        "expected_score_identity": {
            "attempt_id": attempt_id,
            "task_name": task_name,
        },
        "action_log": env.get_action_log() if env is not None and hasattr(env, "get_action_log") else {},
        "runtime_task_dag_snapshot": runtime_snapshot,
        "task_graph_snapshot": task_graph_snapshot,
        "controller": {
            "shutdown_complete": bool(getattr(controller, "shutdown_complete", False)),
            "state": getattr(controller, "controller_state", None),
            "context": getattr(controller, "shutdown_context", None),
            "active_assignments": dict(getattr(controller, "assignment", {})),
        },
        "error": error,
    }


def _runtime_checkpoint_result(env=None, tm=None, controller=None, *, attempt_id: str | None = None, task_name: str | None = None) -> dict:
    result = _runtime_result(None, tm, controller, attempt_id=attempt_id, task_name=task_name)
    if env is not None and hasattr(env, "get_action_log"):
        result["action_log"] = env.get_action_log()
    return result


def validate_judged_runtime_result(result: dict) -> None:
    score = result.get("score")
    if not isinstance(score, dict) or not score:
        raise JudgedRuntimeValidationError("judged runtime result has no score payload")
    try:
        validate_score_identity(
            score,
            expected_attempt_id=result.get("attempt_id"),
            expected_task_name=result.get("task_name"),
        )
    except ScoreOwnershipError as exc:
        raise JudgedRuntimeValidationError(str(exc)) from exc
    if score.get("status") != "success":
        raise JudgedRuntimeValidationError(
            f"judger reported terminal status {score.get('status')!r}"
        )
    snapshot = result.get("runtime_task_dag_snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("summary", {}).get("terminal_state") != "success":
        raise JudgedRuntimeValidationError("runtime task DAG is not terminal success")
    nodes = snapshot.get("nodes", [])
    if not nodes or any(
        node.get("lifecycle", {}).get("status") != "success"
        or node.get("lifecycle", {}).get("active_agents")
        for node in nodes
    ):
        raise JudgedRuntimeValidationError(
            "runtime task DAG contains non-success or actively assigned tasks"
        )
    if result.get("controller", {}).get("shutdown_complete") is not True:
        raise JudgedRuntimeValidationError("controller shutdown is incomplete")
    if result.get("controller", {}).get("active_assignments"):
        raise JudgedRuntimeValidationError("controller still has active assignments")
    if result.get("error") is not None:
        raise JudgedRuntimeValidationError("judged runtime result contains an error")


def _write_runtime_result(path: str | None, payload: dict) -> None:
    if not path:
        return
    atomic_write_json(path, payload)

print(f"pipeline Time taken: {time.time() - start_time}")
start_time = time.time()

os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
os.environ["no_proxy"] = "localhost,127.0.0.1,::1"


def _with_runtime_paths(function):
    signature = inspect.signature(function)

    @wraps(function)
    def wrapped(*args, **kwargs):
        bound = signature.bind_partial(*args, **kwargs)
        paths = bound.arguments.get("runtime_paths") or RuntimePaths.legacy()
        bound.arguments["runtime_paths"] = paths
        with paths.activated():
            return function(*bound.args, **bound.kwargs)

    return wrapped


@_with_runtime_paths
def run(api_model: str, api_base: str, task_type: str, task_idx: int, agent_num: int, dig_needed: bool, max_task_num: int, task_goal: str, document_file: str, host: str, port: int, task_name: str, role: str = "same", api_key_list: list | None = None, document: dict | None = None, minecraft_dual_dag_config: dict | None = None, runtime_result_path: str | None = None, task_scenario: str | None = None, runtime_event_path: str | None = None, emit_controller_terminal_event: bool = True, runtime_paths: RuntimePaths | None = None, attempt_id: str | None = None):
    start_time = time.time()

    if task_type == "meta" and not task_scenario:
        raise ValueError("meta task requires task_scenario")
    runtime_paths = runtime_paths or RuntimePaths.legacy()
    runtime_paths.ensure_directories()
    document = dict(document or {})
    api_key_list = load_agent_api_key_list()
    meta_setting = {
            "api_model": api_model,
            "api_base": api_base,
            "task_type": task_type,
            "task_idx": task_idx,
            "agent_num": agent_num,
            "dig_needed": dig_needed,
            "max_task_num": max_task_num,
            "task_goal": task_goal,
            "document_file": document_file,
            "host": host,
            "port": port,
            "task_name": task_name,
            "role": role,
            "attempt_id": attempt_id,
        }
    if task_type == "meta":
        meta_setting["task_scenario"] = task_scenario
        meta_setting["evaluation_arg"] = document
    atomic_write_json(runtime_paths.meta_setting, meta_setting)

    # Agent.base_url = "https://api.deepseek.com/v1"
    # Agent.model = "deepseek-chat"

    # Agent.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    # # Agent.model = "qwen3-235b-a22b"
    # Agent.model = "qwen3-next-80b-a3b-instruct"
    # Agent.api_key_list = api_key_list

    # Agent.base_url = "http://10.112.59.240:55049/v1"
    selected_api_key = api_key_list[0] if api_key_list else None
    configure_ollama_agent(Agent, api_model=api_model, api_base=api_base, api_key=selected_api_key)

    # 设置env
    if task_type == "construction":
        env = VillagerBench(env_type=env_type.construction, task_id=task_idx, dig_needed=dig_needed, host=host, port=port, max_task_num=max_task_num, task_name=task_name, _virtual_debug=False, runtime_paths=runtime_paths)
    elif task_type == "farming":
        env = VillagerBench(env_type=env_type.farming, task_id=task_idx, dig_needed=False, host=host, port=port, max_task_num=max_task_num, task_name=task_name, _virtual_debug=False, runtime_paths=runtime_paths)
    elif task_type == "puzzle":
        env = VillagerBench(env_type=env_type.puzzle, task_id=task_idx, dig_needed=False, host=host, port=port, max_task_num=max_task_num, task_name=task_name, _virtual_debug=False, runtime_paths=runtime_paths)
    elif task_type == "meta":
        env = VillagerBench(env_type=env_type.meta, task_id=task_idx, dig_needed=False, host=host, port=port, max_task_num=max_task_num, task_name=task_name, _virtual_debug=False, runtime_paths=runtime_paths)
    elif task_type == "gen":
        env = VillagerBench(env_type=env_type.gen, task_id=task_idx, dig_needed=False, host=host, port=port, max_task_num=max_task_num, task_name=task_name, _virtual_debug=False, runtime_paths=runtime_paths)
    else:
        raise NotImplementedError
    env.attempt_id = attempt_id
    if task_type == "meta" and runtime_result_path:
        env.meta_diagnostics_dir = os.path.dirname(runtime_result_path) or "."

    # 设置agent_tool
    if task_type == "construction":
        agent_tool = [Agent.placeBlock, Agent.fetchContainerContents, Agent.MineBlock, Agent.scanNearbyEntities, Agent.equipItem,
                      Agent.navigateTo, Agent.withdrawItem, Agent.dismantleDirtLadder, Agent.erectDirtLadder, Agent.handoverBlock]
    elif task_type == "farming":
        agent_tool = [Agent.fetchContainerContents, Agent.MineBlock, Agent.scanNearbyEntities, Agent.equipItem, Agent.SmeltingCooking,
                      Agent.navigateTo, Agent.withdrawItem, Agent.craftBlock, Agent.attackTarget, Agent.useItemOnEntity,
                      Agent.handoverBlock]
    elif task_type == "puzzle":
        agent_tool = [Agent.placeBlock, Agent.fetchContainerContents, Agent.MineBlock, Agent.scanNearbyEntities, Agent.equipItem,
                      Agent.navigateTo, Agent.withdrawItem, Agent.ToggleAction, Agent.handoverBlock]
    elif task_type == "meta" or task_type == "gen":
        agent_tool = [Agent.scanNearbyEntities, Agent.navigateTo, Agent.attackTarget, Agent.useItemOnEntity, Agent.useItemOnBlock,
                      Agent.MineBlock, Agent.placeBlock, Agent.equipItem, Agent.handoverBlock, Agent.SmeltingCooking, Agent.withdrawItem, 
                      Agent.storeItem, Agent.craftBlock, Agent.eat, Agent.fetchContainerContents, Agent.wake, Agent.talkTo, Agent.waitForFeedback,
                      Agent.openContainer, Agent.performMovement, 
                      Agent.sleep, Agent.startFishing, Agent.ToggleAction, 
                      Agent.read, Agent.mountEntity, Agent.dismountEntity]
    else:
        raise NotImplementedError

    print(f"VillagerBench Time taken: {time.time() - start_time}")
    start_time = time.time()

    # 设置agent_pool
    name_list = ["Alice", "Bob", "Cindy", "David", "Eve", "Frank", "Grace", "Helen", "Ivy", "Jack", "Kevin", "Lily",
                 "Mary", "Nancy", "Olivia", "Peter", "Queen", "Rose", "Sam", "Tom", "Umbrella", "Vivian", "Wendy",
                 "Xavier", "Yolanda", "Zoe"]
    if agent_num == 3 and task_type == "farming" and role == "different":
        agent_tool = [Agent.fetchContainerContents, Agent.scanNearbyEntities, Agent.equipItem,
                      Agent.navigateTo, Agent.withdrawItem, Agent.craftBlock, Agent.SmeltingCooking,
                      Agent.handoverBlock]
        env.agent_register(agent_tool=agent_tool, agent_number=1, name_list=[name_list[0]])
        agent_tool = [Agent.fetchContainerContents, Agent.scanNearbyEntities, Agent.equipItem,
                      Agent.navigateTo, Agent.withdrawItem, Agent.craftBlock, Agent.MineBlock,
                      Agent.handoverBlock]
        env.agent_register(agent_tool=agent_tool, agent_number=1, name_list=[name_list[1]])
        agent_tool = [Agent.fetchContainerContents, Agent.scanNearbyEntities, Agent.equipItem,
                      Agent.navigateTo, Agent.withdrawItem, Agent.craftBlock, Agent.attackTarget, 
                      Agent.handoverBlock]
        env.agent_register(agent_tool=agent_tool, agent_number=1, name_list=[name_list[2]])
    else:
        action = document.get("action", None)
        if action == "chat" or action == "handover":
            env.agent_register(agent_tool=agent_tool, agent_number=agent_num+1, name_list=name_list[:agent_num+1])
        else:
            env.agent_register(agent_tool=agent_tool, agent_number=agent_num, name_list=name_list[:agent_num])

    runtime_tm = None
    runtime_ctrl = None
    try:
        with env.run(fast_api=True):  # Use the FastAPI bridge; it avoids viewer-only Node dependencies such as canvas.
            # 启动DM
            history_output_dir = runtime_paths.run_result_dir(task_name)
            dm = DataManager(silent=False, history_output_dir=history_output_dir)
            dm.update_database_init(env.get_init_state())

            print(f"DataManager Time taken: {time.time() - start_time}")
            start_time = time.time()

            # 启动TM
            from pipeline.runtime_events import JsonlRuntimeEventRecorder, NoOpRuntimeEventSink
            event_sink = JsonlRuntimeEventRecorder(runtime_event_path, run_id=task_name) if runtime_event_path else NoOpRuntimeEventSink()
            tm = TaskManager(
                silent=False,
                cache_enabled=False,
                history_output_dir=history_output_dir,
            )
            tm.event_sink = event_sink
            runtime_tm = tm
            tm.runtime_checkpoint = lambda: _write_runtime_result(
                runtime_result_path,
                _runtime_checkpoint_result(env, tm, runtime_ctrl, attempt_id=attempt_id, task_name=task_name),
            )
            _write_runtime_result(
                runtime_result_path,
                _runtime_checkpoint_result(env, tm, runtime_ctrl, attempt_id=attempt_id, task_name=task_name),
            )

            print(f"TaskManager Time taken: {time.time() - start_time}")
            start_time = time.time()

            # 设置llm
            llm_config = make_ollama_llm_config(api_model=api_model, api_base=api_base, api_key=selected_api_key)
            # llm_config = {
            #     "api_key": api_key_list[0],
            #     "api_base": "https://api.deepseek.com/v1",
            #     "api_model": "deepseek-chat",
            #     "api_key_list": api_key_list
            # }
        
            # llm_config = {
            #     "api_key": "sk-VillagerTuning",
            #     # "api_base": "http://10.112.59.240:50892/v1",
            #     "api_base": "http://localhost:8264/v1/",
            #     "api_model": "default",
            #     "api_key_list": ["sk-VillagerTuning"]
            # }

            tm_llm_config = llm_config
            dm_llm_config = llm_config
            # base_llm_config = llm_config

            # tm_llm_config = {
            #     "api_key": api_key_list[0],
            #     "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            #     "api_model": "qwen-max",
            #     "api_key_list": api_key_list
            # }

            # dm_llm_config = {
            #     "api_key": api_key_list[0],
            #     "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            #     "api_model": "qwen-plus",
            #     "api_key_list": api_key_list
            # }

            # base_llm_config = {
            #     "api_key": api_key_list[0],
            #     "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            #     "api_model": "qwen3-next-80b-a3b-instruct",
            #     "api_key_list": api_key_list
            # }
            base_llm_config = make_ollama_llm_config(api_model=api_model, api_base=api_base, api_key=selected_api_key)


            ctrl = GlobalController(llm_config, tm, dm, env,
                                tm_llm_config=tm_llm_config, 
                                dm_llm_config=dm_llm_config,
                                base_agent_config=base_llm_config,
                                all_tools=agent_tool,
                                minecraft_dual_dag_config=minecraft_dual_dag_config,
                                event_sink=event_sink,
                                emit_terminal_events=emit_controller_terminal_event)
            runtime_ctrl = ctrl

            # response = ctrl.agent_list[0].llm.few_shot_generate_thoughts(system_prompt="", example_prompt="hi")
            # print(response)
            if task_type == "farming": #补充材料来源prompt
                with open("data/farm_setting.json", "r") as f:
                    task_settings = json.load(f)
                task_data = task_settings[task_idx]
                task_goal += f"\nBelow is a detailed list of ingredients and their specific sources. Use this information to plan and coordinate your actions efficiently:\n"
                if "cake" in task_data["name"]:
                    task_goal += f"egg: egg in chest\n"
                    task_goal += f"milk: {task_data['milk']}\n"
                    task_goal += f"wheat: {task_data['wheat']}\n"
                    task_goal += f"sugar: {task_data['sugar']}\n"
                elif "rabbit_stew" in task_data["name"]:
                    task_goal += f"cooked_rabbit: {task_data['cooked_rabbit']}\n"
                    task_goal += f"baked_potato: {task_data['baked_potato']}\n"
                    task_goal += f"carrot: {task_data['carrot']}\n"
                    task_goal += f"brown_mushroom: {task_data['brown_mushroom']}\n"
                    task_goal += f"bowl: {task_data['bowl']}\n"
                
            if os.path.exists(document_file):
                document["recipe"] = json.load((open(document_file)))
            tm.init_task(description=task_goal, document=document)
            _write_runtime_result(
                runtime_result_path,
                _runtime_result(env, tm, ctrl, attempt_id=attempt_id, task_name=task_name),
            )

            ctrl.run()

            result = _runtime_result(env, tm, ctrl, attempt_id=attempt_id, task_name=task_name)
            if task_type == "meta":
                validate_judged_runtime_result(result)
            _write_runtime_result(runtime_result_path, result)
            return result
    except Exception as exc:
        result = _runtime_result(
            env,
            runtime_tm,
            runtime_ctrl,
            error=str(exc),
            attempt_id=attempt_id,
            task_name=task_name,
        )
        _write_runtime_result(runtime_result_path, result)
        raise


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely run or validate a Minecraft experiment config",
    )
    parser.add_argument("--config", required=True, help="Launch config JSON file")
    parser.add_argument("--config-index", type=int, default=0, help="Config list entry to select")
    parser.add_argument("--output-root", default="result/minecraft", help="Artifact output directory")
    parser.add_argument("--timeout", type=float, default=None, help="Positive execute timeout in seconds")
    parser.add_argument("--execute", action="store_true", help="Run the real Minecraft environment")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    if not os.path.isfile(args.config):
        parser.error(f"config file not found: {args.config}")
    if args.execute and (
        args.timeout is None or not math.isfinite(args.timeout) or args.timeout <= 0
    ):
        parser.error("--execute requires --timeout with a positive value")

    try:
        from benchmarks.minecraft.experiment import run_minecraft_experiment
    except (ImportError, AttributeError) as exc:
        print(f"error: unable to load Minecraft experiment harness: {exc}", file=sys.stderr)
        return 1

    try:
        summary = run_minecraft_experiment(
            config_path=args.config,
            config_index=args.config_index,
            output_root=args.output_root,
            execute=args.execute,
            execute_timeout_seconds=args.timeout,
        )
    except Exception as exc:
        print(f"error: Minecraft experiment harness failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2))
    return 0 if summary.get("error") is None else 1


if __name__ == "__main__":
    raise SystemExit(main())

# python env/minecraft_server.py -H 10.214.180.148 -P 25565 -LP 5000 -U Alice -W world -D false
# python env/meta_judger.py --idx 0 --host 10.214.180.148 --port 25565 --agent_num 1 --agent_names Alice --task_name meta_test_task0
