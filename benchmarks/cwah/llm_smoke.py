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
        "navigation_loop_threshold": args.navigation_loop_threshold,
    }
    events = [{"event": "episode_started", "episode": episode.__dict__, "run_config": run_config}]
    blocked_action_ids: set[str] = set()
    failed_action_signatures: set[str] = set()
    suppressed_action_signatures: set[str] = set()
    navigation_signature_counts: dict[str, int] = {}

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
                blocked_action_ids=tuple(sorted(blocked_action_ids)),
                failed_action_signatures=tuple(sorted(failed_action_signatures)),
                suppressed_action_signatures=tuple(sorted(suppressed_action_signatures)),
            )
            prefer_physical = args.prefer_physical_after_steps >= 0 and context.step >= args.prefer_physical_after_steps
            blocked_action_signatures = failed_action_signatures | suppressed_action_signatures
            action = action_from_decision(
                agent_id,
                decision,
                context.legal_actions,
                prefer_physical=prefer_physical,
                blocked_action_ids=blocked_action_ids,
                blocked_action_signatures=blocked_action_signatures,
            )
            if action.action_type == "send_message":
                result = adapter.execute_information_action(agent_id, action)
            else:
                result = adapter.execute_action(agent_id, action)
            if not result.succeeded or result.error:
                blocked_action_ids.add(action.action_id)
                signature = action_failure_signature(action)
                if signature:
                    failed_action_signatures.add(signature)
                decision["failed_action_recorded"] = {
                    "action_id": action.action_id,
                    "action_signature": signature,
                    "error": result.error or "execution_failed",
                }
            navigation_signature = action_navigation_signature(action)
            if navigation_signature and args.navigation_loop_threshold > 0:
                navigation_signature_counts[navigation_signature] = navigation_signature_counts.get(navigation_signature, 0) + 1
                if navigation_signature_counts[navigation_signature] >= args.navigation_loop_threshold:
                    suppressed_action_signatures.add(navigation_signature)
                    decision["navigation_loop_recorded"] = {
                        "action_signature": navigation_signature,
                        "count": navigation_signature_counts[navigation_signature],
                        "threshold": args.navigation_loop_threshold,
                    }
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


def decide_with_llm(
    *,
    client: OpenAI,
    model: str,
    temperature: float,
    max_tokens: int,
    context,
    blocked_action_ids: tuple[str, ...] = (),
    failed_action_signatures: tuple[str, ...] = (),
    suppressed_action_signatures: tuple[str, ...] = (),
) -> dict:
    prompt = {
        "agent_id": context.actor_id,
        "step": context.step,
        "observation_summary": summarize_observations(context.visible_epistemic_nodes),
        "candidate_action_intents": summarize_action_intents(context.legal_actions),
        "recent_failed_action_ids": list(blocked_action_ids)[-10:],
        "recent_failed_action_signatures": list(failed_action_signatures)[-10:],
        "recent_suppressed_action_signatures": list(suppressed_action_signatures)[-10:],
        "legal_actions": [
            {"action_id": action.action_id, "action_type": action.action_type, "parameters": action.parameters}
            for action in context.legal_actions
        ],
        "instruction": (
            "Choose exactly one legal action by action_id. Prefer useful physical sequences: walktowards a "
            "goal object or receptacle, grab grabbable objects, open closed containers, then putin or putback "
            "held objects into visible containers or onto visible surfaces. Use send_message only to share "
            "useful new information. Prefer candidate actions with precondition_status executable_now. "
            "If a goal action is setup_required, choose its setup_action_id first when that setup action is legal. "
            "Do not choose recent_failed_action_ids, recent_failed_action_signatures, or "
            "recent_suppressed_action_signatures unless no alternative exists. "
            "Return compact JSON only: "
            "{\"action_id\": \"...\", \"rationale\": \"...\", \"message\": \"...\"}. "
            "The message field is only used for send_message."
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
    task_goals = []
    nodes_by_id = {}
    for record in visible_epistemic_nodes:
        source_kind = record.get("source_kind")
        proposition = record.get("proposition", {})
        grounding = record.get("grounding", {})
        if source_kind == "task_goal":
            goal_hint = grounding.get("task_goal_hint") if isinstance(grounding, dict) else None
            if isinstance(goal_hint, dict):
                task_goals.append(goal_hint)
            elif proposition:
                task_goals.append(proposition)
            continue
        if source_kind == "agent_message":
            messages.append(proposition.get("object"))
            continue
        node = grounding.get("node") if isinstance(grounding, dict) else None
        if isinstance(node, dict):
            item = {
                "id": node.get("id"),
                "class_name": node.get("class_name"),
                "category": node.get("category"),
                "properties": (node.get("properties") or [])[:8],
                "states": (node.get("states") or [])[:8],
            }
            nodes_by_id[node.get("id")] = item
            if node.get("category") == "Rooms":
                rooms.append(item)
            else:
                objects.append(item)
            continue
        edge = grounding.get("edge") if isinstance(grounding, dict) else None
        if isinstance(edge, dict):
            relations.append({
                "from_id": edge.get("from_id"),
                "from_name": nodes_by_id.get(edge.get("from_id"), {}).get("class_name"),
                "relation_type": edge.get("relation_type"),
                "to_id": edge.get("to_id"),
                "to_name": nodes_by_id.get(edge.get("to_id"), {}).get("class_name"),
            })
        elif proposition:
            relations.append(proposition)
    return {
        "visible_objects": objects[:25],
        "visible_rooms": rooms[:10],
        "relations": relations[:25],
        "task_goals": task_goals[:20],
        "held_objects": [relation for relation in relations if "hold" in str(relation.get("relation_type", relation.get("predicate", ""))).lower()][:10],
        "receptacles_or_surfaces": [
            obj for obj in objects if set(str(value).upper() for value in [*(obj.get("properties") or []), *(obj.get("states") or [])]) & {"CONTAINERS", "SURFACES", "RECIPIENT", "PLACEABLE", "OPEN", "CLOSED"}
        ][:15],
        "recent_messages": [message for message in messages if message][:10],
    }


def summarize_action_intents(legal_actions: tuple[ActionSpec, ...]) -> list[dict]:
    intents = []
    for action in legal_actions:
        params = action.parameters
        intent = "communicate" if action.action_type == "send_message" else action.action_type
        if action.action_type == "walktowards":
            intent = f"move near {params.get('object_name', 'object')}"
        elif action.action_type == "grab":
            intent = f"pick up {params.get('object_name', 'object')}"
        elif action.action_type in {"putin", "putback"}:
            intent = f"place {params.get('object_name', 'object')} at {params.get('target_name', 'target')}"
        elif action.action_type == "open":
            intent = f"open {params.get('object_name', 'object')}"
        elif action.action_type == "close":
            intent = f"close {params.get('object_name', 'object')}"
        intents.append({
            "action_id": action.action_id,
            "action_type": action.action_type,
            "intent": intent,
            "precondition_status": params.get("precondition_status", "unknown"),
            "precondition_reason": params.get("precondition_reason", ""),
            "setup_action_id": params.get("setup_action_id", ""),
            "hand_state": params.get("hand_state", "unknown"),
            "held_object_id": params.get("held_object_id"),
            "held_object_name": params.get("held_object_name", ""),
            "goal_object_match": bool(params.get("goal_object_match")),
            "goal_target_match": bool(params.get("goal_target_match")),
            "goal_relation_matches": list(params.get("goal_relation_matches", ())),
        })
    return intents[:40]


def action_from_decision(
    agent_id: str,
    decision: dict,
    legal_actions: tuple[ActionSpec, ...],
    *,
    prefer_physical: bool = False,
    blocked_action_ids: set[str] | frozenset[str] | None = None,
    blocked_action_signatures: set[str] | frozenset[str] | None = None,
) -> ActionSpec:
    blocked_action_ids = blocked_action_ids or set()
    blocked_action_signatures = blocked_action_signatures or set()
    action_by_id = {action.action_id: action for action in legal_actions}
    selected = action_by_id.get(str(decision.get("action_id", "")))
    selected_signature = action_failure_signature(selected) if selected is not None else ""
    selected_is_blocked = selected is not None and (selected.action_id in blocked_action_ids or selected_signature in blocked_action_signatures)
    selected_needs_setup = selected is not None and selected.parameters.get("precondition_status") == "setup_required"
    if selected_is_blocked:
        decision["policy_override"] = {
            "reason": "avoid_repeated_failed_action",
            "blocked_action_id": selected.action_id,
            "blocked_action_signature": selected_signature,
        }
    if selected_needs_setup:
        decision["policy_override"] = {
            "reason": "precondition_setup_required",
            "blocked_action_id": selected.action_id,
            "setup_action_id": selected.parameters.get("setup_action_id", ""),
        }
    if prefer_physical and (selected is None or selected.action_type in {"send_message", "wait"} or selected_is_blocked or selected_needs_setup):
        physical = preferred_physical_action(
            legal_actions,
            blocked_action_ids=blocked_action_ids,
            blocked_action_signatures=blocked_action_signatures,
        )
        if physical is not None and (selected is None or physical.action_id != selected.action_id):
            decision["policy_override"] = {"reason": "prefer_physical_after_steps", "action_id": physical.action_id, "action_type": physical.action_type}
            return physical
    if selected is not None and not selected_is_blocked:
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
            physical = preferred_physical_action(
                legal_actions,
                blocked_action_ids=blocked_action_ids,
                blocked_action_signatures=blocked_action_signatures,
            )
            if physical is not None:
                decision["policy_override"] = {"reason": "prefer_physical_after_steps", "action_id": physical.action_id, "action_type": physical.action_type}
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


def preferred_physical_action(
    legal_actions: tuple[ActionSpec, ...],
    *,
    blocked_action_ids: set[str] | frozenset[str] | None = None,
    blocked_action_signatures: set[str] | frozenset[str] | None = None,
) -> ActionSpec | None:
    blocked_action_ids = blocked_action_ids or set()
    blocked_action_signatures = blocked_action_signatures or set()
    physical = [
        action
        for action in legal_actions
        if action.action_type not in {"send_message", "wait"}
        and action.action_id not in blocked_action_ids
        and action_failure_signature(action) not in blocked_action_signatures
    ]
    return min(physical, key=physical_action_rank, default=None)


def action_failure_signature(action: ActionSpec | None) -> str:
    if action is None or action.action_type in {"send_message", "wait"}:
        return ""
    params = action.parameters
    object_id = params.get("object_id", "")
    target_id = params.get("target_id", "")
    return f"{action.action_type}:{object_id}:{target_id}"


def action_navigation_signature(action: ActionSpec | None) -> str:
    if action is None or action.action_type != "walktowards":
        return ""
    return action_failure_signature(action)


def physical_action_rank(action: ActionSpec) -> tuple[int, int, str]:
    params = action.parameters
    goal_object = bool(params.get("goal_object_match"))
    goal_target = bool(params.get("goal_target_match"))
    goal_relations = set(params.get("goal_relation_matches", ()))
    precondition_status = str(params.get("precondition_status", "unknown"))
    if precondition_status == "setup_required":
        return (20, 0, action.action_id)
    if precondition_status == "blocked":
        return (30, 0, action.action_id)
    if action.action_type == "putback" and "on" in goal_relations:
        return (0, 0, action.action_id)
    if action.action_type == "putin" and "inside" in goal_relations:
        return (0, 1, action.action_id)
    if action.action_type in {"putback", "putin"} and goal_object and goal_target:
        return (1, 0, action.action_id)
    if action.action_type == "grab" and goal_object:
        return (2, 0, action.action_id)
    if action.action_type == "walktowards" and goal_object:
        return (2, 1, action.action_id)
    if action.action_type == "walktowards" and goal_target:
        return (2, 2, action.action_id)
    if action.action_type == "open" and goal_target:
        return (5, 0, action.action_id)
    fallback_priority = {"grab": 6, "putin": 7, "putback": 8, "open": 9, "close": 10, "walktowards": 11}
    return (fallback_priority.get(action.action_type, 99), 0, action.action_id)


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
    parser.add_argument("--navigation-loop-threshold", type=int, default=12, help="Suppress repeated walktowards signatures after this many episode-local selections; use 0 to disable.")
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
