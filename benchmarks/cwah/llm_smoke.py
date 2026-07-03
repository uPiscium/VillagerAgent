from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from openai import OpenAI

from benchmarks.common.actions import ActionSpec, InformationActionSpec
from benchmarks.cwah.adapter import CWAHConfig, CWAHSymbolicAdapter
from benchmarks.cwah.artifacts import write_normalized_artifacts
from benchmarks.cwah.coela_env import coela_cwah_env_factory
from benchmarks.cwah.mock_env import mock_cwah_env_factory


def main() -> None:
    args = parse_args()
    adapter = CWAHSymbolicAdapter(
        config=CWAHConfig(
            episode_id=args.episode_id,
            seed=args.seed,
            task_id=args.task_id,
            max_steps=args.max_steps,
            metadata={
                "repo_root": str(Path.cwd()),
                "coela_cwah_path": args.coela_cwah_path,
                "dataset_path": args.dataset_path,
                "executable_file": args.executable_file,
                "base_port": args.base_port,
            },
        ),
        env_factory=mock_cwah_env_factory if args.env == "mock" else coela_cwah_env_factory,
    )
    client = OpenAI(base_url=args.base_url, api_key=args.api_key)
    episode = adapter.reset(episode_id=args.episode_id, seed=args.seed)
    max_policy_steps = args.max_steps if args.full_episode else args.max_policy_steps
    run_config = {
        "env": args.env,
        "episode_id": args.episode_id,
        "seed": args.seed,
        "task_id": args.task_id,
        "max_steps": args.max_steps,
        "max_policy_steps": max_policy_steps,
        "full_episode": args.full_episode,
        "model": args.model,
        "prefer_physical_after_steps": args.prefer_physical_after_steps,
    }
    events = [{"event": "episode_started", "episode": episode.__dict__, "run_config": run_config}]

    for step in range(max_policy_steps):
        if adapter.is_terminal():
            break
        for agent_id in adapter.agent_ids():
            if adapter.is_terminal():
                break
            context = adapter.decision_context(agent_id)
            decision = decide_with_llm(
                client=client,
                model=args.model,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                context=context,
            )
            prefer_physical = args.prefer_physical_after_steps >= 0 and context.step >= args.prefer_physical_after_steps
            action = action_from_decision(agent_id, decision, context.legal_actions, prefer_physical=prefer_physical)
            if action.action_type == "send_message":
                result = adapter.execute_information_action(agent_id, action)
            else:
                result = adapter.execute_action(agent_id, action)
            events.append({
                "event": "policy_step",
                "step": step,
                "agent_id": agent_id,
                "decision": decision,
                "result": result.__dict__,
            })

    metrics = adapter.final_metrics()
    events.append({"event": "episode_completed", "metrics": metrics})
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"run_config": run_config, "events": events, "metrics": metrics}, default=_json_default, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.artifact_dir:
        write_normalized_artifacts(artifact_dir=Path(args.artifact_dir), run_config=run_config, events=events, metrics=metrics)
    print(json.dumps({"passed": True, "env": args.env, "run_config": run_config, "metrics": metrics}, sort_keys=True))


def _json_default(value):
    if hasattr(value, "__dict__"):
        return value.__dict__
    if isinstance(value, (frozenset, set, tuple)):
        return list(value)
    return str(value)


def decide_with_llm(*, client: OpenAI, model: str, temperature: float, max_tokens: int, context) -> dict:
    prompt = {
        "agent_id": context.actor_id,
        "step": context.step,
        "observation_summary": summarize_observations(context.visible_epistemic_nodes),
        "legal_actions": [
            {"action_id": action.action_id, "action_type": action.action_type, "parameters": action.parameters}
            for action in context.legal_actions
        ],
        "instruction": (
            "Choose exactly one legal action by action_id. Prefer physical actions that move toward, grab, "
            "or place visible task objects. Use send_message only to share useful new information. "
            "Return compact JSON only: "
            "{\"action_id\": \"...\", \"message\": \"...\"}. The message field is only used for send_message."
        ),
    }
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a cooperative C-WAH smoke-test policy. Return JSON only."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    content = response.choices[0].message.content or ""
    try:
        decision = json.loads(content)
    except json.JSONDecodeError:
        decision = {"action_type": "send_message", "message": content.strip()[:200] or "I am checking my local observation."}
    decision["raw_content"] = content
    return decision


def summarize_observations(visible_epistemic_nodes: tuple[dict, ...]) -> dict:
    objects = []
    rooms = []
    relations = []
    messages = []
    for record in visible_epistemic_nodes:
        source_kind = record.get("source_kind")
        proposition = record.get("proposition", {})
        grounding = record.get("grounding", {})
        if source_kind == "agent_message":
            messages.append(proposition.get("object"))
            continue
        node = grounding.get("node") if isinstance(grounding, dict) else None
        if isinstance(node, dict):
            item = {"id": node.get("id"), "class_name": node.get("class_name"), "category": node.get("category")}
            if node.get("category") == "Rooms":
                rooms.append(item)
            else:
                objects.append(item)
            continue
        if proposition:
            relations.append(proposition)
    return {
        "visible_objects": objects[:25],
        "visible_rooms": rooms[:10],
        "relations": relations[:25],
        "recent_messages": [message for message in messages if message][:10],
    }


def action_from_decision(
    agent_id: str,
    decision: dict,
    legal_actions: tuple[ActionSpec, ...],
    *,
    prefer_physical: bool = False,
) -> ActionSpec:
    action_by_id = {action.action_id: action for action in legal_actions}
    selected = action_by_id.get(str(decision.get("action_id", "")))
    if prefer_physical and (selected is None or selected.action_type in {"send_message", "wait"}):
        physical = first_physical_action(legal_actions)
        if physical is not None:
            decision["policy_override"] = {"reason": "prefer_physical_after_steps", "action_id": physical.action_id}
            return physical
    if selected is not None:
        if selected.action_type == "send_message":
            return InformationActionSpec(
                action_id=selected.action_id,
                action_type="send_message",
                parameters={"message": str(decision.get("message", "I am checking my local observation."))},
                information_subtype="send_message",
            )
        return selected

    action_type = str(decision.get("action_type", "send_message"))
    if action_type == "send_message":
        if prefer_physical:
            physical = first_physical_action(legal_actions)
            if physical is not None:
                decision["policy_override"] = {"reason": "prefer_physical_after_steps", "action_id": physical.action_id}
                return physical
        return InformationActionSpec(
            action_id=f"send_message:{agent_id}",
            action_type="send_message",
            parameters={"message": str(decision.get("message", "I am checking my local observation."))},
            information_subtype="send_message",
        )
    return ActionSpec(action_id=f"wait:{agent_id}", action_type="wait", parameters={})


def first_physical_action(legal_actions: tuple[ActionSpec, ...]) -> ActionSpec | None:
    for action in legal_actions:
        if action.action_type not in {"send_message", "wait"}:
            return action
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a C-WAH adapter smoke test with real LLM calls.")
    parser.add_argument("--env", choices=["mock", "coela"], default="mock")
    parser.add_argument("--episode-id", default="cwah-llm-smoke")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=250)
    parser.add_argument("--max-policy-steps", type=int, default=2)
    parser.add_argument("--full-episode", action="store_true", help="Run until terminal or max_steps instead of the smoke-step cap.")
    parser.add_argument("--prefer-physical-after-steps", type=int, default=2, help="Prefer physical actions after this environment step; use -1 to disable.")
    parser.add_argument("--base-url", default=os.environ.get("CWAH_LLM_BASE_URL", "http://ollama.arc.upiscium.dev/v1"))
    parser.add_argument("--api-key", default=os.environ.get("CWAH_LLM_API_KEY", "ollama"))
    parser.add_argument("--model", default=os.environ.get("CWAH_LLM_MODEL", "gemma4:e4b"))
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--output", default="")
    parser.add_argument("--artifact-dir", default="", help="Optional directory for normalized summary.json, turns.jsonl, and metrics.csv artifacts.")
    parser.add_argument("--coela-cwah-path", default="")
    parser.add_argument("--dataset-path", default="")
    parser.add_argument("--executable-file", default="")
    parser.add_argument("--base-port", type=int, default=6314)
    return parser.parse_args()


if __name__ == "__main__":
    main()
