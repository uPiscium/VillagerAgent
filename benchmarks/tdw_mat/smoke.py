from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.common.actions import InformationActionSpec
from benchmarks.tdw_mat.adapter import TDWMATAdapter, TDWMATConfig
from benchmarks.tdw_mat.mock_env import mock_tdw_mat_env_factory


def run_fixture_smoke() -> dict:
    config = TDWMATConfig()
    adapter = TDWMATAdapter(config=config, env_factory=mock_tdw_mat_env_factory)
    episode = adapter.reset(episode_id="tdw-mat-fixture-smoke", seed=config.seed)
    message_result = adapter.execute_information_action(
        "agent_0",
        InformationActionSpec(
            action_id="send_message:agent_0",
            action_type="send_message",
            parameters={"message": "I found bread in the kitchen."},
            information_subtype="send_message",
        ),
    )
    before_grasp = adapter.decision_context("agent_0")
    grasp = next(action for action in before_grasp.legal_actions if action.action_type == "grasp")
    grasp_result = adapter.execute_action("agent_0", grasp)
    before_drop = adapter.decision_context("agent_0")
    drop = next(action for action in before_drop.legal_actions if action.action_type == "drop")
    drop_result = adapter.execute_action("agent_0", drop)
    final_context = adapter.decision_context("agent_0")
    snapshot = adapter.dual_dag_snapshot("agent_0")
    return {
        "schema_version": 1,
        "benchmark": "tdw_mat",
        "smoke_type": "fixture_contract",
        "performance_claim": False,
        "source_scenario": {
            "scene": config.scene,
            "layout": config.layout,
            "task": config.task,
            "seed": config.seed,
        },
        "episode": {
            "episode_id": episode.episode_id,
            "agent_ids": list(episode.agent_ids),
            "steps": adapter.step_index,
        },
        "metrics": adapter.final_metrics(),
        "trace": [
            {
                "step": message_result.step,
                "action_id": "send_message:agent_0",
                "action_type": "send_message",
                "information_action": True,
                "succeeded": message_result.succeeded,
                "frames": message_result.metrics["num_frames_for_step"],
            },
            {
                "step": grasp_result.step,
                "action_id": grasp.action_id,
                "action_type": grasp.action_type,
                "information_action": False,
                "succeeded": grasp_result.succeeded,
                "frames": grasp_result.metrics["num_frames_for_step"],
            },
            {
                "step": drop_result.step,
                "action_id": drop.action_id,
                "action_type": drop.action_type,
                "information_action": False,
                "succeeded": drop_result.succeeded,
                "frames": drop_result.metrics["num_frames_for_step"],
            },
        ],
        "artifact_counts": {
            "visible_epistemic_nodes": len(snapshot["epistemic_dag"]["nodes"]),
            "visible_candidates": len(snapshot["action_candidate_dag"]["nodes"]),
            "public_events": len(final_context.recent_public_events),
        },
        "caveat": (
            "Dependency-free schema/adapter smoke using the official TDW-MAT observation and action contract; "
            "not a Unity simulator run."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the TDW-MAT fixture-backed adapter smoke.")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    payload = run_fixture_smoke()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": payload["metrics"]["task_success"], "output": str(output)}))
    return 0 if payload["metrics"]["task_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
