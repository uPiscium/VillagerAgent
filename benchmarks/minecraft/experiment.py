import argparse
import json
import signal
import time
from pathlib import Path

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


class MinecraftExecuteTimeoutError(TimeoutError):
    """Raised when a bounded real Minecraft run exceeds its timeout."""


def run_minecraft_experiment(
    *,
    config_path: str | Path,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    run_name: str | None = None,
    config_index: int = 0,
    enable_dual_dag_task_selection: bool = True,
    execute: bool = False,
    execute_timeout_seconds: float | None = None,
    command_text: str | None = None,
) -> dict:
    """Run or dry-run a Minecraft experiment and write normalized artifacts.

    Dry-run is the default so CI and local development can validate artifact capture
    without requiring a Minecraft server. ``execute=True`` calls the existing real
    runtime and then captures the same public artifact set from the run outputs.
    """
    launch_config = _load_config(config_path, config_index=config_index)
    selected_run_name = standard_run_name(run_name or launch_config.get("task_name") or _default_run_name(config_path))
    output_dir = Path(output_root) / selected_run_name
    output_dir.mkdir(parents=True, exist_ok=True)

    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
    dual_dag_config = _dual_dag_config(enable_dual_dag_task_selection)
    tasks, graph, task_store = _task_graph_from_config(launch_config)
    action_log: dict = _fixture_action_log(launch_config)
    score: dict = {}
    error = None
    error_type = ""
    timed_out = False

    if execute:
        try:
            _execute_real_runtime_bounded(
                launch_config,
                dual_dag_config=dual_dag_config,
                timeout_seconds=execute_timeout_seconds,
            )
        except MinecraftExecuteTimeoutError as exc:
            error = str(exc)
            error_type = "timeout"
            timed_out = True
        except Exception as exc:  # Preserve partial artifacts for failed smoke runs.
            error = str(exc)
            error_type = exc.__class__.__name__
        action_log = _read_json(Path("data/action_log.json"), default={})
        score = _read_json(Path("data/score.json"), default={})

    artifact = build_minecraft_dual_dag_artifact(
        action_log=action_log,
        tasks=tasks,
        graph=graph,
    )
    decision_support = build_minecraft_runtime_decision_support(
        artifact,
        candidate_tasks=tasks,
    )
    ranked = rank_minecraft_runtime_tasks(
        tasks,
        graph=graph,
        action_log=action_log,
        config=dual_dag_config,
    )
    ranked_tasks = ranked.get("tasks", tasks)
    task_graph_snapshot = _task_graph_snapshot(graph)
    summary = {
        "run_name": selected_run_name,
        "mode": "execute" if execute else "dry_run",
        "started_at": started_at,
        "output_dir": str(output_dir),
        "task_name": launch_config.get("task_name", ""),
        "task_type": launch_config.get("task_type", ""),
        "task_idx": launch_config.get("task_idx"),
        "dual_dag_runtime_enabled": True,
        "dual_dag_task_selection_enabled": True,
        "runtime_task_store": "runtime_task_dag",
        "source_of_truth": "runtime_task_dag",
        "execute_real_environment": bool(execute),
        "execute_timeout_seconds": execute_timeout_seconds,
        "mutates_runtime": False,
        "artifact_summary": artifact.get("summary", {}),
        "recommended_task_id": decision_support.get("recommended_task_id", ""),
        "recommended_description": decision_support.get("recommended_description", ""),
        "task_order": _task_order(tasks),
        "ranked_task_order": _task_order(ranked_tasks),
        "selected_task_id": _task_id(ranked_tasks[0]) if ranked_tasks else "",
        "selected_description": ranked_tasks[0].description if ranked_tasks else "",
        "final_score": sanitize_public_value(score),
        "progress": _progress_from_score(score),
        "error": error,
        "error_type": error_type,
        "timed_out": timed_out,
    }
    metrics = build_minecraft_metrics(
        summary=summary,
        action_log=action_log,
        task_graph_snapshot=task_graph_snapshot,
        decision_support=decision_support,
    )

    _write_json(output_dir / "launch_config.json", sanitize_public_value(launch_config))
    _write_json(output_dir / "action_log.json", sanitize_public_value(action_log))
    _write_json(output_dir / "task_graph_snapshot.json", task_graph_snapshot)
    _write_json(output_dir / "runtime_dual_dag_snapshot.json", task_store.snapshot())
    _write_json(output_dir / "dual_dag_artifact.json", artifact)
    _write_json(output_dir / "decision_support.json", decision_support)
    _write_json(output_dir / "metrics.json", metrics)
    _write_json(output_dir / "summary.json", summary)
    write_provenance(
        output_dir,
        benchmark="minecraft",
        command=command_text or _command_text(),
        resolved_config=sanitize_public_value(launch_config),
        environment_notes="real_environment_execute=" + str(bool(execute)).lower(),
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Minecraft real-environment experiment harness")
    parser.add_argument("--config", required=True, help="Launch config JSON file")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--config-index", type=int, default=0)
    parser.add_argument("--dual-dag-task-selection", action="store_true", help="Compatibility flag; Dual-DAG runtime task selection is always enabled")
    parser.add_argument("--execute", action="store_true", help="Run the real Minecraft environment")
    parser.add_argument("--execute-timeout-seconds", type=float, default=None, help="Bound real execute mode and preserve artifacts on timeout")
    args = parser.parse_args(argv)

    summary = run_minecraft_experiment(
        config_path=args.config,
        output_root=args.output_root,
        run_name=args.run_name,
        config_index=args.config_index,
        enable_dual_dag_task_selection=args.dual_dag_task_selection,
        execute=args.execute,
        execute_timeout_seconds=args.execute_timeout_seconds,
        command_text=_command_text(args),
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


def _execute_real_runtime(launch_config: dict, *, dual_dag_config: dict) -> None:
    from start_with_config import run
    from model.ollama_config import make_ollama_llm_config

    llm_config = make_ollama_llm_config()
    config = dict(launch_config)
    document = config.get("evaluation_arg", {}) if config.get("task_type") == "meta" else {}
    run(
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
    )


def _execute_real_runtime_bounded(
    launch_config: dict,
    *,
    dual_dag_config: dict,
    timeout_seconds: float | None,
) -> None:
    if timeout_seconds is None or timeout_seconds <= 0:
        _execute_real_runtime(launch_config, dual_dag_config=dual_dag_config)
        return

    timeout_triggered = {"value": False}

    def _timeout_handler(signum, frame):
        timeout_triggered["value"] = True
        raise MinecraftExecuteTimeoutError(
            f"Minecraft execute mode timed out after {timeout_seconds} seconds"
        )

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        _execute_real_runtime(launch_config, dual_dag_config=dual_dag_config)
    except RuntimeError as exc:
        # env.run() is a contextmanager that logs and swallows exceptions raised
        # before its first yield; contextlib then surfaces ``generator didn't
        # yield``. Preserve the original timeout classification for reports.
        if timeout_triggered["value"] and str(exc) == "generator didn't yield":
            raise MinecraftExecuteTimeoutError(
                f"Minecraft execute mode timed out after {timeout_seconds} seconds"
            ) from exc
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


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
        "mutates_runtime": False,
        "tasks": [sanitize_public_value(task.to_json()) for task in graph.vertex],
        "edges": [
            {"source": start.description, "target": end.description}
            for start, end in graph.edge
        ],
    }


def _dual_dag_config(enabled: bool) -> dict:
    return {"runtime_task_selection": {"enabled": True, "requested_enabled": bool(enabled)}}


def _fixture_action_log(config: dict) -> dict:
    action_log = config.get("smoke_action_log", {})
    return action_log if isinstance(action_log, dict) else {}


def _task_order(tasks: list[Task]) -> list[dict]:
    return [
        {"task_id": _task_id(task), "description": task.description}
        for task in tasks
    ]


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
    if args.dual_dag_task_selection:
        parts.append("--dual-dag-task-selection")
    if args.execute:
        parts.append("--execute")
    if args.execute_timeout_seconds is not None:
        parts.extend(["--execute-timeout-seconds", str(args.execute_timeout_seconds)])
    return " ".join(parts)


def _read_json(path: Path, *, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
