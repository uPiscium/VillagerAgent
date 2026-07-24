from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from benchmarks.common.actions import ActionSpec, InformationActionSpec
from benchmarks.tdw_mat.adapter import TDWMATAdapter, TDWMATConfig
from benchmarks.tdw_mat.mock_env import mock_tdw_mat_env_factory


CONDITIONS = {
    "baseline": {"dual_dag": False, "communication_policy": "planner_default"},
    "communication_disabled": {"dual_dag": True, "communication_policy": "disabled"},
    "current_communication": {"dual_dag": True, "communication_policy": "always_on"},
    "value_of_information": {"dual_dag": True, "communication_policy": "value_of_information"},
}


def run_fixture_comparison(subset_path: str | Path) -> dict[str, Any]:
    subset = json.loads(Path(subset_path).read_text(encoding="utf-8"))
    episodes = []
    for declaration in subset["diagnostic_subset"]:
        scenario_results = {
            condition: _run_condition(declaration, condition)
            for condition in CONDITIONS
        }
        disabled = scenario_results["communication_disabled"]["metrics"]
        for condition, result in scenario_results.items():
            metrics = result["metrics"]
            result["matched_communication_utility"] = {
                "transport_rate_delta_vs_disabled": (
                    metrics["transport_rate"] - disabled["transport_rate"]
                ),
                "step_delta_vs_disabled": metrics["episode_steps"] - disabled["episode_steps"],
            }
            episodes.append(result)
    return {
        "schema_version": 1,
        "benchmark": "tdw_mat",
        "comparison_type": "fixture_policy_smoke",
        "performance_claim": False,
        "source": subset["source"],
        "subset_size": len(subset["diagnostic_subset"]),
        "conditions": CONDITIONS,
        "episodes": episodes,
        "condition_summary": _condition_summary(episodes),
        "limitations": [
            "Dependency-free fixture using official TDW-MAT observation and action schemas.",
            "No Unity physics, navigation, perception model, or simulator timing is represented.",
            "Matched communication utility is diagnostic and cannot support embodied superiority claims.",
        ],
    }


def _run_condition(declaration: dict[str, Any], condition: str) -> dict[str, Any]:
    policy = CONDITIONS[condition]
    config = TDWMATConfig(
        scene=str(declaration["scene"]),
        layout=str(declaration["layout"]),
        task=str(declaration["task"]),
        seed=int(declaration["seed"]),
        metadata={"episode_index": int(declaration["episode_index"])},
    )
    adapter = TDWMATAdapter(config=config, env_factory=mock_tdw_mat_env_factory)
    episode_id = f"tdw-mat-fixture-{declaration['episode_index']}-{condition}"
    adapter.reset(episode_id=episode_id, seed=config.seed)
    initial_snapshot = adapter.dual_dag_snapshot("agent_0")
    trace = []

    if condition == "current_communication":
        result = adapter.execute_information_action(
            "agent_0",
            InformationActionSpec(
                action_id="send_message:agent_0",
                action_type="send_message",
                parameters={"message": "I found bread in the kitchen."},
                information_subtype="send_message",
            ),
        )
        trace.append(_trace_row(result, "send_message", information_action=True))

    for action in _qualification_actions():
        result = adapter.execute_action("agent_0", action)
        trace.append(_trace_row(result, action.action_type, information_action=False))

    return {
        "episode_id": episode_id,
        "episode_index": declaration["episode_index"],
        "condition": condition,
        "policy": policy,
        "communication_decision": _communication_decision(condition),
        "scenario": {
            key: declaration[key] for key in ("scene", "layout", "task", "seed")
        },
        "metrics": adapter.final_metrics(),
        "trace": trace,
        "dual_dag_artifact": {
            "used_for_decision": bool(policy["dual_dag"]),
            "epistemic_nodes": initial_snapshot["epistemic_dag"]["nodes"],
            "action_candidates": initial_snapshot["action_candidate_dag"]["nodes"],
        },
    }


def _communication_decision(condition: str) -> dict[str, Any]:
    if condition == "current_communication":
        return {
            "decision": "communicate",
            "expected_progress_gain": 0.0,
            "frame_cost": 5,
            "reason": "current_policy_communicates_on_goal_relevant_discovery",
        }
    if condition == "value_of_information":
        return {
            "decision": "act_physically",
            "expected_progress_gain": 0.0,
            "frame_cost": 5,
            "reason": "communication_value_does_not_exceed_frame_cost",
        }
    return {
        "decision": "act_physically",
        "expected_progress_gain": 0.0,
        "frame_cost": 0,
        "reason": "communication_disabled" if condition == "communication_disabled" else "baseline",
    }


def _qualification_actions() -> tuple[ActionSpec, ...]:
    return (
        ActionSpec(
            action_id="known-infeasible-grasp",
            action_type="grasp",
            parameters={"object_id": 999, "arm": "left", "predicted_feasible": False},
        ),
        ActionSpec(
            action_id="false-infeasible-grasp",
            action_type="grasp",
            parameters={"object_id": 10, "arm": "left", "predicted_feasible": False},
        ),
        ActionSpec(
            action_id="false-feasible-grasp",
            action_type="grasp",
            parameters={"object_id": 10, "arm": "left", "predicted_feasible": True},
        ),
        ActionSpec(
            action_id="recovery-drop",
            action_type="drop",
            parameters={"arm": "left", "predicted_feasible": True},
        ),
    )


def _trace_row(result, action_type: str, *, information_action: bool) -> dict[str, Any]:
    return {
        "step": result.step,
        "action_type": action_type,
        "information_action": information_action,
        "succeeded": result.succeeded,
        "frames": result.metrics["num_frames_for_step"],
        "transport_rate": result.metrics["transport_rate"],
    }


def _condition_summary(episodes: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    summary = {}
    for condition in CONDITIONS:
        rows = [episode for episode in episodes if episode["condition"] == condition]
        metrics = [row["metrics"] for row in rows]
        summary[condition] = {
            "episodes": len(rows),
            "task_success_rate": _mean([float(row["task_success"]) for row in metrics]),
            "mean_transport_rate": _mean([row["transport_rate"] for row in metrics]),
            "mean_episode_steps": _mean([row["episode_steps"] for row in metrics]),
            "mean_communication_count": _mean([row["communication_count"] for row in metrics]),
            "mean_communication_utility": _mean([row["communication_utility"] for row in metrics]),
            "feasibility_prediction_precision": _mean([
                row["feasibility_prediction_precision"] for row in metrics
            ]),
            "feasibility_prediction_recall": _mean([
                row["feasibility_prediction_recall"] for row in metrics
            ]),
            "false_feasible_action_rate": _mean([
                row["false_feasible_action_rate"] for row in metrics
            ]),
            "false_infeasible_action_rate": _mean([
                row["false_infeasible_action_rate"] for row in metrics
            ]),
            "recovery_after_failure_rate": _mean([
                row["recovery_after_failure_rate"] for row in metrics
            ]),
            "mean_action_throughput": _mean([row["action_throughput"] for row in metrics]),
            "mean_information_action_to_progress_latency": _mean([
                row["information_action_to_progress_latency"] for row in metrics
            ]),
            "mean_transport_rate_delta_vs_disabled": _mean([
                row["matched_communication_utility"]["transport_rate_delta_vs_disabled"]
                for row in rows
            ]),
            "mean_step_delta_vs_disabled": _mean([
                row["matched_communication_utility"]["step_delta_vs_disabled"]
                for row in rows
            ]),
        }
    return summary


def _mean(values: list[float | int]) -> float:
    return sum(values) / len(values) if values else 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the TDW-MAT fixture policy comparison.")
    parser.add_argument("--subset", default="configs/tdw_mat/subset.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    payload = run_fixture_comparison(args.subset)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "episodes": len(payload["episodes"]), "output": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
