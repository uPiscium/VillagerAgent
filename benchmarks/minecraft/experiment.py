import argparse
from copy import deepcopy
import json
import multiprocessing
import os
import queue
import time
from pathlib import Path

from benchmarks.common.run_artifacts import finalize_run_directory, prepare_run_directory
from benchmarks.common.sanitization import collect_secret_values, sanitize_artifact_value
from benchmarks.experiment_provenance import standard_run_name, write_provenance
from benchmarks.minecraft.metrics import build_minecraft_metrics
from env.minecraft_dual_dag import (
    build_minecraft_dual_dag_artifact,
    build_minecraft_runtime_decision_support,
    rank_minecraft_runtime_tasks,
    sanitize_public_value,
)
from pipeline.dual_dag_task_store import RuntimeTaskDAGStore
from type_define.graph import Graph, Task


DEFAULT_OUTPUT_ROOT = Path("result/minecraft")
REQUIRED_CONFIG_FIELDS = ("task_type", "task_idx", "agent_num", "task_goal", "host", "port", "task_name")
TASK_SELECTION_POLICIES = ("dual-dag", "original")
RUNTIME_TERMINATE_GRACE_SECONDS = 1.0


class MinecraftExecuteTimeoutError(TimeoutError):
    """Raised when a bounded real Minecraft run exceeds its timeout."""

    def __init__(self, message: str, *, process_metadata: dict | None = None):
        super().__init__(message)
        self.process_metadata = process_metadata or {}


class MinecraftRuntimeChildError(RuntimeError):
    def __init__(self, message: str, *, error_type: str, process_metadata: dict):
        super().__init__(message)
        self.error_type = error_type
        self.process_metadata = process_metadata


def run_minecraft_experiment(
    *,
    config_path: str | Path,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    run_name: str | None = None,
    config_index: int = 0,
    enable_dual_dag_task_selection: bool = True,
    task_selection_policy: str | None = None,
    execute: bool = False,
    execute_timeout_seconds: float | None = None,
    retain_runtime_result: bool = False,
    command_text: str | None = None,
    overwrite: bool = False,
) -> dict:
    attempt_state: dict = {}
    try:
        return _run_minecraft_experiment_attempt(
            config_path=config_path,
            output_root=output_root,
            run_name=run_name,
            config_index=config_index,
            enable_dual_dag_task_selection=enable_dual_dag_task_selection,
            task_selection_policy=task_selection_policy,
            execute=execute,
            execute_timeout_seconds=execute_timeout_seconds,
            retain_runtime_result=retain_runtime_result,
            command_text=command_text,
            overwrite=overwrite,
            attempt_state=attempt_state,
        )
    except BaseException:
        if attempt_state:
            finalize_run_directory(
                attempt_state["output_dir"],
                attempt_id=attempt_state["attempt_id"],
                producer="benchmarks.minecraft.experiment",
                status="failed",
            )
        raise


def _run_minecraft_experiment_attempt(
    *,
    config_path: str | Path,
    output_root: str | Path,
    run_name: str | None,
    config_index: int,
    enable_dual_dag_task_selection: bool,
    task_selection_policy: str | None,
    execute: bool,
    execute_timeout_seconds: float | None,
    retain_runtime_result: bool,
    command_text: str | None,
    overwrite: bool,
    attempt_state: dict,
) -> dict:
    """Run or dry-run a Minecraft experiment and write normalized artifacts.

    Dry-run is the default so CI and local development can validate artifact capture
    without requiring a Minecraft server. ``execute=True`` calls the existing real
    runtime and then captures the same public artifact set from the run outputs.
    """
    launch_config = _load_config(config_path, config_index=config_index)
    secret_values = collect_secret_values(launch_config)
    selected_run_name = standard_run_name(run_name or launch_config.get("task_name") or _default_run_name(config_path))
    output_dir = Path(output_root) / selected_run_name
    attempt_id = prepare_run_directory(
        output_dir,
        producer="benchmarks.minecraft.experiment",
        overwrite=overwrite,
    )
    attempt_state.update({"output_dir": output_dir, "attempt_id": attempt_id})

    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
    selected_policy = _task_selection_policy(
        task_selection_policy or launch_config.get("task_selection_policy"),
        enable_dual_dag_task_selection=enable_dual_dag_task_selection,
    )
    dual_dag_config = _dual_dag_config(selected_policy)
    tasks, graph, task_store = _task_graph_from_config(launch_config)
    action_log: dict = _fixture_action_log(launch_config)
    action_log_available = "smoke_action_log" in launch_config
    score: dict = {}
    runtime_result: dict = {}
    error = None
    error_type = ""
    timed_out = False
    runtime_process = {}
    runtime_result_path = output_dir / ".runtime" / "runtime_result.json"

    if execute:
        _remove_runtime_result(runtime_result_path)
        try:
            runtime_result = _execute_real_runtime_bounded(
                launch_config,
                dual_dag_config=dual_dag_config,
                timeout_seconds=execute_timeout_seconds,
                runtime_result_path=runtime_result_path,
            ) or {}
        except MinecraftExecuteTimeoutError as exc:
            error = str(exc)
            error_type = "timeout"
            timed_out = True
            runtime_process = exc.process_metadata
        except MinecraftRuntimeChildError as exc:
            error = str(exc)
            error_type = exc.error_type
            runtime_process = exc.process_metadata
        except Exception as exc:  # Preserve partial artifacts for failed smoke runs.
            error = str(exc)
            error_type = exc.__class__.__name__
        persisted_runtime_result = _read_json(runtime_result_path, default={})
        runtime_result = runtime_result or persisted_runtime_result
        runtime_process = runtime_process or runtime_result.pop("runtime_process", {})
        action_log_available = isinstance(runtime_result.get("action_log"), dict)
        action_log = runtime_result.get("action_log") if action_log_available else {}
        score = runtime_result.get("score") if isinstance(runtime_result.get("score"), dict) else {}

    runtime_task_dag_snapshot = _runtime_task_dag_snapshot(
        runtime_result=runtime_result,
        fallback_snapshot=task_store.snapshot(),
        execute=execute,
    )
    task_state_source = runtime_task_dag_snapshot.get("snapshot_source", "config_fixture")
    artifact_tasks = tasks
    artifact_graph = graph
    if execute and task_state_source == "real_runtime":
        artifact_tasks, artifact_graph = task_graph_from_runtime_task_dag_snapshot(
            runtime_task_dag_snapshot
        )

    artifact = build_minecraft_dual_dag_artifact(
        action_log=action_log,
        tasks=artifact_tasks,
        graph=artifact_graph,
    )
    artifact["task_state_source"] = task_state_source
    decision_support = build_minecraft_runtime_decision_support(
        artifact,
        candidate_tasks=artifact_tasks,
    )
    decision_support["task_state_source"] = task_state_source
    ranked = rank_minecraft_runtime_tasks(
        artifact_tasks,
        graph=artifact_graph,
        action_log=action_log,
        config=dual_dag_config,
    )
    ranked_tasks = ranked.get("tasks", artifact_tasks)
    task_graph_snapshot = runtime_result.get("task_graph_snapshot") or _task_graph_snapshot(artifact_graph)
    runtime_selected_task_ids = _runtime_selected_task_ids(runtime_result) if execute else []
    selected_task = _find_task(artifact_tasks, runtime_selected_task_ids[0]) if runtime_selected_task_ids else None
    if not execute and ranked_tasks:
        selected_task = ranked_tasks[0]
    summary = {
        "attempt_id": attempt_id,
        "run_name": selected_run_name,
        "mode": "execute" if execute else "dry_run",
        "started_at": started_at,
        "output_dir": str(output_dir),
        "task_name": launch_config.get("task_name", ""),
        "task_type": launch_config.get("task_type", ""),
        "task_idx": launch_config.get("task_idx"),
        "dual_dag_runtime_enabled": True,
        "dual_dag_task_selection_enabled": selected_policy == "dual-dag",
        "task_selection_policy": selected_policy,
        "runtime_selection_policy": selected_policy,
        "runtime_task_store": "runtime_task_dag",
        "source_of_truth": "runtime_task_dag",
        "snapshot_source": runtime_task_dag_snapshot.get("snapshot_source", "config_fixture"),
        "task_state_source": task_state_source,
        "execute_real_environment": bool(execute),
        "execute_timeout_seconds": execute_timeout_seconds,
        "mutates_environment": bool(execute),
        "artifact_generation_mutates_runtime": False,
        "task_selection_mutates_order": ranked["task_selection_mutates_order"],
        "task_order_changed": ranked["task_order_changed"],
        "mutates_runtime": False,
        "artifact_summary": artifact.get("summary", {}),
        "recommended_task_id": decision_support.get("recommended_task_id", ""),
        "recommended_description": decision_support.get("recommended_description", ""),
        "task_order": _task_order(artifact_tasks),
        "ranked_task_order": _task_order(ranked_tasks),
        "posthoc_ranked_task_order": _task_order(ranked_tasks),
        "runtime_selected_task_ids": runtime_selected_task_ids,
        "selected_task_id": (
            runtime_selected_task_ids[0]
            if execute and runtime_selected_task_ids
            else _task_id(selected_task) if selected_task is not None else ""
        ),
        "selected_description": selected_task.description if selected_task is not None else "",
        "final_score": sanitize_public_value(score),
        "progress": _progress_from_score(score),
        "error": error,
        "error_type": error_type,
        "timed_out": timed_out,
        "runtime_result_retained": bool(execute and retain_runtime_result and runtime_result_path.exists()),
        "runtime_process_isolated": bool(execute),
        "runtime_process_exit_code": runtime_process.get("exit_code"),
        "runtime_process_terminated": bool(runtime_process.get("terminated", False)),
        "runtime_process_killed": bool(runtime_process.get("killed", False)),
        "action_log_available": action_log_available,
    }
    metrics = build_minecraft_metrics(
        summary=summary,
        action_log=action_log,
        task_graph_snapshot=task_graph_snapshot,
        decision_support=decision_support,
    )

    sanitized_launch_config = sanitize_artifact_value(
        sanitize_public_value(launch_config),
        secret_values=secret_values,
    )
    action_log = sanitize_artifact_value(action_log, secret_values=secret_values)
    task_graph_snapshot = sanitize_artifact_value(task_graph_snapshot, secret_values=secret_values)
    runtime_task_dag_snapshot = sanitize_artifact_value(runtime_task_dag_snapshot, secret_values=secret_values)
    artifact = sanitize_artifact_value(artifact, secret_values=secret_values)
    decision_support = sanitize_artifact_value(decision_support, secret_values=secret_values)
    metrics = sanitize_artifact_value(metrics, secret_values=secret_values)
    summary = sanitize_artifact_value(summary, secret_values=secret_values)
    _write_json(output_dir / "launch_config.json", sanitized_launch_config)
    _write_json(output_dir / "action_log.json", action_log)
    _write_json(output_dir / "task_graph_snapshot.json", task_graph_snapshot)
    _write_json(output_dir / "runtime_dual_dag_snapshot.json", runtime_task_dag_snapshot)
    _write_json(output_dir / "dual_dag_artifact.json", artifact)
    _write_json(output_dir / "decision_support.json", decision_support)
    _write_json(output_dir / "metrics.json", metrics)
    _write_json(output_dir / "summary.json", summary)
    write_provenance(
        output_dir,
        benchmark="minecraft",
        command=command_text or _command_text(),
        resolved_config=sanitized_launch_config,
        environment_notes="real_environment_execute=" + str(bool(execute)).lower(),
    )
    if execute and not retain_runtime_result:
        _remove_runtime_result(runtime_result_path)
    finalize_run_directory(
        output_dir,
        attempt_id=attempt_id,
        producer="benchmarks.minecraft.experiment",
        status="failed" if error else "completed",
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Minecraft real-environment experiment harness")
    parser.add_argument("--config", required=True, help="Launch config JSON file")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--config-index", type=int, default=0)
    parser.add_argument("--task-selection-policy", choices=TASK_SELECTION_POLICIES, default="dual-dag", help="Runtime task ordering policy")
    parser.add_argument("--dual-dag-task-selection", action="store_true", help="Deprecated compatibility flag; equivalent to --task-selection-policy dual-dag")
    parser.add_argument("--no-dual-dag-task-selection", action="store_true", help="Deprecated compatibility flag; equivalent to --task-selection-policy original")
    parser.add_argument("--execute", action="store_true", help="Run the real Minecraft environment")
    parser.add_argument("--execute-timeout-seconds", type=float, default=None, help="Bound real execute mode and preserve artifacts on timeout")
    parser.add_argument("--retain-runtime-result", action="store_true", help="Keep the per-run internal runtime result after normalized artifacts are written")
    parser.add_argument("--overwrite", action="store_true", help="Explicitly replace an existing non-empty run directory")
    args = parser.parse_args(argv)

    summary = run_minecraft_experiment(
        config_path=args.config,
        output_root=args.output_root,
        run_name=args.run_name,
        config_index=args.config_index,
        enable_dual_dag_task_selection=not args.no_dual_dag_task_selection,
        task_selection_policy=_policy_from_args(args),
        execute=args.execute,
        execute_timeout_seconds=args.execute_timeout_seconds,
        retain_runtime_result=args.retain_runtime_result,
        command_text=_command_text(args),
        overwrite=args.overwrite,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary.get("error") is None else 1


def _load_config(config_path: str | Path, *, config_index: int) -> dict:
    config = _read_json(Path(config_path), default=None)
    if isinstance(config, list):
        if config_index < 0 or config_index >= len(config):
            raise ValueError(f"Minecraft config index out of range: {config_index}")
        selected = config[config_index]
        if not isinstance(selected, dict):
            raise ValueError(f"Minecraft config entry at index {config_index} must be an object")
        return validate_minecraft_config(dict(selected), context=f"config[{config_index}]")
    if isinstance(config, dict):
        return validate_minecraft_config(dict(config), context="config")
    raise ValueError(f"Unsupported Minecraft config shape: {config_path}")


def validate_minecraft_config(config: dict, *, context: str = "config") -> dict:
    missing = [field for field in REQUIRED_CONFIG_FIELDS if field not in config]
    if missing:
        raise ValueError(f"{context} missing required field(s): {', '.join(missing)}")
    agent_num = _required_int(config, "agent_num", context=context)
    port = _required_int(config, "port", context=context)
    task_idx = _required_int(config, "task_idx", context=context)
    if agent_num < 0:
        raise ValueError(f"{context}.agent_num must be non-negative")
    if port <= 0:
        raise ValueError(f"{context}.port must be positive")
    if task_idx < 0:
        raise ValueError(f"{context}.task_idx must be non-negative")
    if config.get("task_selection_policy") not in (None, *TASK_SELECTION_POLICIES):
        raise ValueError(f"{context}.task_selection_policy must be one of: {', '.join(TASK_SELECTION_POLICIES)}")
    _validate_smoke_tasks(config, context=context)
    action_log = config.get("smoke_action_log", {})
    if action_log is not None and not isinstance(action_log, dict):
        raise ValueError(f"{context}.smoke_action_log must be an object")
    return config


def _validate_smoke_tasks(config: dict, *, context: str) -> None:
    smoke_tasks = config.get("smoke_tasks")
    if smoke_tasks is None:
        return
    if not isinstance(smoke_tasks, list):
        raise ValueError(f"{context}.smoke_tasks must be a list")
    for index, task in enumerate(smoke_tasks):
        task_context = f"{context}.smoke_tasks[{index}]"
        if not isinstance(task, dict):
            raise ValueError(f"{task_context} must be an object")
        if "description" not in task:
            raise ValueError(f"{task_context} missing required field: description")
        for list_field in ("candidate_agents", "assigned_agents", "required_subtasks", "required subtasks"):
            if list_field in task and not isinstance(task[list_field], list):
                raise ValueError(f"{task_context}.{list_field} must be a list")
        if "number" in task and _required_int(task, "number", context=task_context) <= 0:
            raise ValueError(f"{task_context}.number must be positive")


def _required_int(config: dict, field: str, *, context: str) -> int:
    try:
        return int(config[field])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{context}.{field} must be an integer") from exc


def _execute_real_runtime(
    launch_config: dict,
    *,
    dual_dag_config: dict,
    runtime_result_path: Path,
) -> dict:
    from start_with_config import run
    from model.ollama_config import make_ollama_llm_config

    llm_config = make_ollama_llm_config()
    config = dict(launch_config)
    document = config.get("evaluation_arg", {}) if config.get("task_type") == "meta" else {}
    return run(
        llm_config["api_model"],
        llm_config["api_base"],
        config["task_type"],
        config["task_idx"],
        config["agent_num"],
        config.get("dig_needed", False),
        config.get("max_task_num", 0),
        config["task_goal"],
        config.get("document_file", ""),
        config["host"],
        config["port"],
        config["task_name"],
        config.get("role", "same"),
        [llm_config.get("api_key_list", [])],
        document,
        minecraft_dual_dag_config=dual_dag_config,
        runtime_result_path=str(runtime_result_path),
    )


def _execute_real_runtime_bounded(
    launch_config: dict,
    *,
    dual_dag_config: dict,
    timeout_seconds: float | None,
    runtime_result_path: Path,
) -> dict:
    context = multiprocessing.get_context()
    status_queue = context.Queue()
    process = context.Process(
        target=_runtime_process_entry,
        args=(launch_config, dual_dag_config, str(runtime_result_path), status_queue),
    )
    process.start()
    process.join(timeout_seconds if timeout_seconds is not None and timeout_seconds > 0 else None)
    if process.is_alive():
        partial_before_termination = _read_json(runtime_result_path, default={})
        process_metadata = _terminate_runtime_process(
            process,
            grace_seconds=RUNTIME_TERMINATE_GRACE_SECONDS,
        )
        if partial_before_termination and not runtime_result_path.exists():
            _write_runtime_checkpoint(runtime_result_path, partial_before_termination)
        status_queue.close()
        raise MinecraftExecuteTimeoutError(
            f"Minecraft execute mode timed out after {timeout_seconds} seconds",
            process_metadata=process_metadata,
        )

    process_metadata = {
        "exit_code": process.exitcode,
        "terminated": False,
        "killed": False,
    }
    child_status = _read_child_status(status_queue)
    status_queue.close()
    if child_status.get("status") == "error":
        raise MinecraftRuntimeChildError(
            child_status.get("error", "Minecraft runtime child failed"),
            error_type=child_status.get("error_type", "RuntimeError"),
            process_metadata=process_metadata,
        )
    if process.exitcode not in (0, None):
        raise MinecraftRuntimeChildError(
            f"Minecraft runtime child exited with code {process.exitcode}",
            error_type="ChildProcessError",
            process_metadata=process_metadata,
        )
    result = _read_json(runtime_result_path, default={})
    result["runtime_process"] = process_metadata
    return result


def _runtime_process_entry(
    launch_config: dict,
    dual_dag_config: dict,
    runtime_result_path: str,
    status_queue,
) -> None:
    try:
        result = _execute_real_runtime(
            launch_config,
            dual_dag_config=dual_dag_config,
            runtime_result_path=Path(runtime_result_path),
        ) or {}
        result_path = Path(runtime_result_path)
        if result or not result_path.exists():
            _write_runtime_checkpoint(result_path, result)
        status_queue.put({"status": "completed"})
    except BaseException as exc:
        partial = _read_json(Path(runtime_result_path), default={})
        partial["error"] = str(exc)
        _write_runtime_checkpoint(Path(runtime_result_path), partial)
        status_queue.put({
            "status": "error",
            "error": str(exc),
            "error_type": exc.__class__.__name__,
        })


def _terminate_runtime_process(process, *, grace_seconds: float) -> dict:
    process.terminate()
    process.join(grace_seconds)
    killed = False
    if process.is_alive():
        process.kill()
        process.join()
        killed = True
    return {
        "exit_code": process.exitcode,
        "terminated": True,
        "killed": killed,
    }


def _read_child_status(status_queue) -> dict:
    try:
        return status_queue.get(timeout=0.5)
    except queue.Empty:
        return {}


def _write_runtime_checkpoint(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary_path, path)


def _task_graph_from_config(config: dict) -> tuple[list[Task], Graph, RuntimeTaskDAGStore]:
    task_configs = config.get("smoke_tasks")
    if isinstance(task_configs, list) and task_configs:
        tasks = [_task_from_config(config, task_config) for task_config in task_configs]
    else:
        tasks = [_task_from_config(config, {
            "description": config.get("task_goal", config.get("task_name", "Minecraft task")),
        })]
    task_store = RuntimeTaskDAGStore()
    task_store.load_tasks_from_decomposition(tasks)
    graph = task_store.to_task_graph_projection()
    return tasks, graph, task_store


def _runtime_task_dag_snapshot(*, runtime_result: dict, fallback_snapshot: dict, execute: bool) -> dict:
    if execute and isinstance(runtime_result.get("runtime_task_dag_snapshot"), dict) and runtime_result["runtime_task_dag_snapshot"]:
        snapshot = dict(runtime_result["runtime_task_dag_snapshot"])
        snapshot["snapshot_source"] = "real_runtime"
        return snapshot
    snapshot = dict(fallback_snapshot)
    snapshot["snapshot_source"] = "config_fixture"
    return snapshot


def task_graph_from_runtime_task_dag_snapshot(snapshot: dict) -> tuple[list[Task], Graph]:
    tasks = []
    task_by_node_id = {}
    graph = Graph()
    for node in snapshot.get("nodes", []) or []:
        if not isinstance(node, dict) or node.get("node_type") != "runtime_task":
            continue
        node_id = str(node.get("node_id", ""))
        if not node_id:
            continue
        content = node.get("content", {}) if isinstance(node.get("content"), dict) else {}
        lifecycle = node.get("lifecycle", {}) if isinstance(node.get("lifecycle"), dict) else {}
        derived = node.get("derived", {}) if isinstance(node.get("derived"), dict) else {}
        metadata = deepcopy(content.get("metadata", {}))
        if not isinstance(metadata, dict):
            metadata = {"value": metadata}
        metadata["runtime_snapshot"] = {
            "last_assigned_agents": deepcopy(lifecycle.get("last_assigned_agents", []) or []),
            "provenance": deepcopy(node.get("provenance", {})),
        }
        task = Task(str(content.get("description", "")), metadata)
        task.id = node_id.removeprefix("runtime:task:")
        task.milestones = deepcopy(content.get("milestones", []) or [])
        task.reflect = deepcopy(content.get("reflect"))
        task.status = str(lifecycle.get("status", Task.unknown))
        task.candidate_list = list(lifecycle.get("candidate_agents", []) or [])
        task._agent = list(lifecycle.get("active_agents", []) or [])
        task.number = int(lifecycle.get("required_agent_count", 1) or 1)
        task.available = bool(derived.get("dependency_ready", False)) and task.status == Task.unknown
        tasks.append(task)
        task_by_node_id[node_id] = task
        graph.add_node(task)

    for edge in snapshot.get("edges", []) or []:
        if not isinstance(edge, dict) or edge.get("edge_type") != "precedes_task":
            continue
        source_id = str(edge.get("source_id", ""))
        target_id = str(edge.get("target_id", ""))
        if source_id not in task_by_node_id or target_id not in task_by_node_id:
            raise ValueError(f"Runtime task snapshot edge references unknown task: {source_id} -> {target_id}")
        graph.add_edge(task_by_node_id[source_id], task_by_node_id[target_id])

    for task in tasks:
        task._direct_pre_task_list = list(graph.get_node_to(task))
        task.predecessor_task_list = _graph_predecessors(graph, task)
    return tasks, graph


def _task_from_config(config: dict, task_config: dict) -> Task:
    task = Task(task_config.get("description", "Minecraft task"), {
        "task_name": config.get("task_name", ""),
        "task_type": config.get("task_type", ""),
        "task_idx": config.get("task_idx"),
        "smoke_task_id": task_config.get("id", ""),
    })
    if task_config.get("id"):
        task.id = str(task_config["id"])
    agent_num = int(config.get("agent_num", 0) or 0)
    task.candidate_list = task_config.get("candidate_agents") or _agent_names(agent_num)
    task._agent = task_config.get("assigned_agents", [])
    task.number = int(task_config.get("number", max(1, min(agent_num, 1))))
    task._pre_idxs = [int(index) for index in task_config.get("required_subtasks", task_config.get("required subtasks", []))]
    return task


def _task_graph_snapshot(graph: Graph) -> dict:
    return {
        "artifact_generation_mutates_runtime": False,
        "mutates_runtime": False,
        "tasks": [sanitize_public_value(task.to_json()) for task in graph.vertex],
        "edges": [
            {"source": start.description, "target": end.description}
            for start, end in graph.edge
        ],
    }


def _task_selection_policy(value: str | None, *, enable_dual_dag_task_selection: bool = True) -> str:
    if value is None:
        return "dual-dag" if enable_dual_dag_task_selection else "original"
    if value not in TASK_SELECTION_POLICIES:
        raise ValueError(f"Unsupported task_selection_policy: {value}")
    return value


def _policy_from_args(args: argparse.Namespace) -> str:
    if args.no_dual_dag_task_selection:
        return "original"
    if args.dual_dag_task_selection:
        return "dual-dag"
    return args.task_selection_policy


def _dual_dag_config(task_selection_policy: str) -> dict:
    return {
        "runtime_task_selection": {
            "enabled": task_selection_policy == "dual-dag",
            "policy": task_selection_policy,
        }
    }


def _fixture_action_log(config: dict) -> dict:
    action_log = config.get("smoke_action_log", {})
    return action_log if isinstance(action_log, dict) else {}


def _task_order(tasks: list[Task]) -> list[dict]:
    return [
        {"task_id": _task_id(task), "description": task.description}
        for task in tasks
    ]


def _runtime_selected_task_ids(runtime_result: dict) -> list[str]:
    selected = runtime_result.get("runtime_selected_task_ids", [])
    if not isinstance(selected, list):
        return []
    return [str(task_id) for task_id in selected if task_id is not None]


def _find_task(tasks: list[Task], task_id: str) -> Task | None:
    for task in tasks:
        if task_id in {_task_id(task), str(task.id), f"runtime:task:{task.id}"}:
            return task
    return None


def _graph_predecessors(graph: Graph, task: Task) -> list[Task]:
    predecessors = []
    pending = list(graph.get_node_to(task))
    while pending:
        predecessor = pending.pop(0)
        if predecessor in predecessors:
            continue
        predecessors.append(predecessor)
        pending.extend(graph.get_node_to(predecessor))
    return predecessors


def _task_id(task: Task) -> str:
    return f"minecraft:task:{task.id}"


def _agent_names(agent_num: int) -> list[str]:
    names = ["Alice", "Bob", "Cindy", "David", "Eve", "Frank"]
    return names[:agent_num]


def _progress_from_score(score: dict):
    if not isinstance(score, dict):
        return None
    for key in ("progress", "score", "completion", "success_rate"):
        if key in score:
            return score[key]
    return None


def _default_run_name(config_path: str | Path) -> str:
    return f"minecraft_experiment_{Path(config_path).stem}"


def _command_text(args: argparse.Namespace | None = None) -> str:
    if args is None:
        return "python -m benchmarks.minecraft.experiment"
    parts = ["python -m benchmarks.minecraft.experiment", "--config", args.config]
    if args.output_root != str(DEFAULT_OUTPUT_ROOT):
        parts.extend(["--output-root", args.output_root])
    if args.run_name:
        parts.extend(["--run-name", args.run_name])
    if args.config_index:
        parts.extend(["--config-index", str(args.config_index)])
    if args.task_selection_policy != "dual-dag":
        parts.extend(["--task-selection-policy", args.task_selection_policy])
    if args.dual_dag_task_selection:
        parts.append("--dual-dag-task-selection")
    if args.no_dual_dag_task_selection:
        parts.append("--no-dual-dag-task-selection")
    if args.execute:
        parts.append("--execute")
    if args.execute_timeout_seconds is not None:
        parts.extend(["--execute-timeout-seconds", str(args.execute_timeout_seconds)])
    if args.retain_runtime_result:
        parts.append("--retain-runtime-result")
    if getattr(args, "overwrite", False):
        parts.append("--overwrite")
    return " ".join(parts)


def _read_json(path: Path, *, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _remove_runtime_result(path: Path) -> None:
    for candidate in (path, path.with_suffix(path.suffix + ".tmp")):
        if candidate.exists():
            candidate.unlink()
    runtime_dir = path.parent
    if runtime_dir.exists() and not any(runtime_dir.iterdir()):
        runtime_dir.rmdir()


def _write_json(path: Path, payload) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
