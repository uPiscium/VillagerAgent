from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.common.actions import InformationActionSpec
from benchmarks.tdw_mat.adapter import TDWMATAdapter, TDWMATConfig
from benchmarks.tdw_mat.real_env import (
    DEFAULT_COELA_ROOT,
    CoELATDWMATEnvFactory,
    CoELATDWRuntimeConfig,
    inspect_real_preflight,
)


def run_real_smoke(*, runtime: CoELATDWRuntimeConfig) -> dict:
    preflight = inspect_real_preflight(coela_root=runtime.coela_root)
    if not preflight["ready"]:
        raise RuntimeError("TDW-MAT real smoke preflight failed: " + ", ".join(preflight["missing"]))
    scenario = TDWMATConfig(max_frames=30)
    adapter = TDWMATAdapter(config=scenario, env_factory=CoELATDWMATEnvFactory(runtime))
    try:
        episode = adapter.reset(episode_id="tdw-mat-real-smoke", seed=scenario.seed)
        initial = adapter.dual_dag_snapshot("agent_0")
        result = adapter.execute_information_action(
            "agent_0",
            InformationActionSpec(
                action_id="send_message:agent_0",
                action_type="send_message",
                parameters={"message": "Starting the food transport task."},
                information_subtype="send_message",
            ),
        )
        return {
            "schema_version": 1,
            "benchmark": "tdw_mat",
            "smoke_type": "real_simulator_one_step",
            "performance_claim": False,
            "preflight": preflight,
            "source_scenario": {
                "scene": scenario.scene,
                "layout": scenario.layout,
                "task": scenario.task,
                "seed": scenario.seed,
            },
            "episode_id": episode.episode_id,
            "reset_artifact_counts": {
                "epistemic_nodes": len(initial["epistemic_dag"]["nodes"]),
                "action_candidates": len(initial["action_candidate_dag"]["nodes"]),
            },
            "trace": [{
                "step": result.step,
                "action_type": "send_message",
                "information_action": True,
                "succeeded": result.succeeded,
                "frames": result.metrics["num_frames_for_step"],
            }],
            "metrics": adapter.final_metrics(),
            "caveat": "One-step real environment integration smoke; not a completion or performance run.",
        }
    finally:
        if adapter.env is not None:
            adapter.env.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight or run one bounded real TDW-MAT step.")
    parser.add_argument("--coela-root", default=str(DEFAULT_COELA_ROOT))
    parser.add_argument("--output", required=True)
    parser.add_argument("--runtime-output", default="result/tdw_mat/real_runtime")
    parser.add_argument("--port", type=int, default=1071)
    parser.add_argument("--screen-size", type=int, default=128)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args(argv)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.preflight_only:
        payload = inspect_real_preflight(coela_root=args.coela_root)
        output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ready": payload["ready"], "missing": payload["missing"], "output": str(output)}))
        return 2 if args.require_ready and not payload["ready"] else 0
    runtime = CoELATDWRuntimeConfig(
        coela_root=Path(args.coela_root),
        output_dir=Path(args.runtime_output),
        port=args.port,
        screen_size=args.screen_size,
    )
    payload = run_real_smoke(runtime=runtime)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "output": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
