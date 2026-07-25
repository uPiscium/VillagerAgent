from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.partnr.adapter import PARTNRAdapter, PARTNRConfig
from benchmarks.partnr.fixture_env import fixture_partnr_env_factory


def run_fixture_smoke() -> dict:
    config = PARTNRConfig(
        instruction="Move the apple from the kitchen table into the living-room basket.",
        scene_id="fixture_hssd_scene",
        max_steps=8,
    )
    adapter = PARTNRAdapter(config=config, env_factory=fixture_partnr_env_factory)
    adapter.reset(episode_id="partnr-fixture-smoke", seed=47668090)
    initial = adapter.dual_dag_snapshot("agent_0")
    trace = []
    action_order = ["FindObjectTool", "Navigate", "Pick", "Pick", "Place"]
    for action_type in action_order:
        action = next(
            action for action in adapter.get_legal_actions("agent_0")
            if action.action_type == action_type
        )
        result = adapter.execute_action("agent_0", action)
        trace.append({
            "step": result.step,
            "action_type": action.action_type,
            "information_action": action.action_type == "FindObjectTool",
            "succeeded": result.succeeded,
            "task_percent_complete": result.metrics["task_percent_complete"],
        })
    final = adapter.dual_dag_snapshot("agent_0")
    return {
        "schema_version": 1,
        "benchmark": "partnr",
        "smoke_type": "dependency_free_contract_fixture",
        "performance_claim": False,
        "source_contract": {
            "repository": "https://github.com/facebookresearch/partnr-planner",
            "commit": "ddfff19f4b6c098a31edea4d19e7b75db72433c2",
        },
        "trace": trace,
        "metrics": adapter.final_metrics(),
        "artifact_counts": {
            "initial_epistemic_nodes": len(initial["epistemic_dag"]["nodes"]),
            "initial_action_candidates": len(initial["action_candidate_dag"]["nodes"]),
            "final_epistemic_nodes": len(final["epistemic_dag"]["nodes"]),
            "final_action_candidates": len(final["action_candidate_dag"]["nodes"]),
        },
        "evaluator_isolation": {
            "evaluation_propositions_agent_visible": False,
            "full_world_graph_agent_visible": False,
        },
        "limitations": [
            "Dependency-free fixture; Habitat-Sim physics and PARTNR perception are not executed.",
            "Official evaluator metric names are mirrored but fixture values are not official results.",
            "The fixture cannot support scale or performance claims.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the PARTNR dependency-free adapter fixture.")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    payload = run_fixture_smoke()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    passed = bool(payload["metrics"]["task_success"])
    print(json.dumps({"passed": passed, "output": str(output)}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
