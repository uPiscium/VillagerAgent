from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from openai import OpenAI

from benchmarks.common.actions import ActionSpec, InformationActionSpec
from benchmarks.common.sanitization import sanitize_artifact_value
from benchmarks.cwah.adapter import CWAHConfig, CWAHSymbolicAdapter
from benchmarks.cwah.artifacts import write_normalized_artifacts
from benchmarks.cwah.coela_env import coela_cwah_env_factory
from benchmarks.cwah.mock_env import mock_cwah_env_factory
from benchmarks.cwah.provenance import (
    is_provider_timeout,
    model_metadata,
    model_provider,
    provenance_assets,
    resolved_external_paths,
)
from benchmarks.experiment_provenance import finalize_provenance, model_identity, update_provenance_assets, write_provenance


def main() -> None:
    args = parse_args()
    provenance_dir = _provenance_dir(args)
    owns_provenance = bool(
        provenance_dir and not (provenance_dir / "provenance.json").exists()
    )
    immutable_model_metadata = model_metadata(args.base_url, args.model) if owns_provenance else {}
    if owns_provenance:
        api_key = os.environ.get("CWAH_LLM_API_KEY", "ollama")
        effective_settings = {
            **vars(args),
            **resolved_external_paths(args),
            "api_key": api_key,
        }
        write_provenance(
            provenance_dir,
            benchmark="cwah",
            command=[sys.executable, "-m", "benchmarks.cwah.llm_smoke", *sys.argv[1:]],
            resolved_config=effective_settings,
            environment_notes=f"single_run=true; env={args.env}",
            assets=provenance_assets(args, metadata=immutable_model_metadata),
        )
    try:
        events = _run(args)
    except BaseException as exc:
        if provenance_dir and (provenance_dir / "provenance.json").exists():
            finalize_provenance(
                provenance_dir,
                status="timeout" if is_provider_timeout(exc) else "failure",
            )
        raise
    if owns_provenance:
        provider_metadata = {**immutable_model_metadata, **_provider_metadata(events)}
        if provider_metadata:
            update_provenance_assets(
                provenance_dir,
                [model_identity(
                    name="policy_model",
                    provider=model_provider(args.base_url),
                    model=args.model,
                    metadata=provider_metadata,
                )],
            )
        finalize_provenance(provenance_dir, status="success")


def _run(args: argparse.Namespace) -> list[dict]:
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
    api_key = os.environ.get("CWAH_LLM_API_KEY", "ollama")
    client = OpenAI(base_url=args.base_url, api_key=api_key)
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
        "attempt_id": args.attempt_id or None,
    }
    events = [{"event": "episode_started", "episode": episode.__dict__, "run_config": run_config}]
    blocked_action_ids: set[str] = set()
    failed_action_signatures: set[str] = set()
    failed_open_target_ids: set[str] = set()
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
                failed_open_target_ids=tuple(sorted(failed_open_target_ids)),
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
                blocked_open_target_ids=failed_open_target_ids,
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
                if action.action_type == "open":
                    target_id = str(action.parameters.get("object_id", ""))
                    if target_id:
                        failed_open_target_ids.add(target_id)
                    decision["open_failure_recorded"] = {
                        "action_id": action.action_id,
                        "target_id": target_id,
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
        payload = sanitize_artifact_value(
            {"run_config": run_config, "events": events, "metrics": metrics},
            secret_values=(api_key,),
        )
        output.write_text(json.dumps(payload, default=_json_default, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.artifact_dir:
        write_normalized_artifacts(
            artifact_dir=Path(args.artifact_dir),
            run_config=run_config,
            events=events,
            metrics=metrics,
            dual_dag_snapshot=adapter.dual_dag_snapshot(),
            secret_values=(api_key,),
        )
    print(json.dumps({"passed": True, "env": args.env, "run_config": run_config, "metrics": metrics}, sort_keys=True))
    return events


def _provenance_dir(args: argparse.Namespace) -> Path | None:
    if args.artifact_dir:
        return Path(args.artifact_dir).parent
    if args.output:
        return Path(args.output).parent
    return None


def _provider_metadata(events: list[dict]) -> dict:
    for event in events:
        metadata = event.get("decision", {}).get("provider_metadata", {})
        if isinstance(metadata, dict) and any(
            metadata.get(key) for key in ("digest", "revision", "system_fingerprint")
        ):
            return metadata
    return {}


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
    failed_open_target_ids: tuple[str, ...] = (),
    suppressed_action_signatures: tuple[str, ...] = (),
) -> dict:
    prompt = {
        "agent_id": context.actor_id,
        "step": context.step,
        "observation_summary": summarize_observations(context.visible_epistemic_nodes),
        "candidate_action_intents": summarize_action_intents(context.legal_actions),
        "action_candidate_dag": list(context.visible_candidates),
        "recent_failed_action_ids": list(blocked_action_ids)[-10:],
        "recent_failed_action_signatures": list(failed_action_signatures)[-10:],
        "recent_failed_open_target_ids": list(failed_open_target_ids)[-10:],
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
            "Do not choose recent_failed_action_ids, recent_failed_action_signatures, "
            "recent_failed_open_target_ids, or "
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
    decision["provider_metadata"] = {
        "model": getattr(response, "model", None),
        "system_fingerprint": getattr(response, "system_fingerprint", None),
        "revision": getattr(response, "revision", None),
        "digest": getattr(response, "digest", None),
    }
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
            "placement_relation": params.get("placement_relation", ""),
            "placement_relation_compatibility": params.get("placement_relation_compatibility", ""),
            "target_affordance": params.get("target_affordance", ""),
            "placement_suitability": params.get("placement_suitability", ""),
            "container_suitability": params.get("container_suitability", ""),
            "search_priority": params.get("search_priority", ""),
            "search_reason": params.get("search_reason", ""),
            "missing_goal_object": bool(params.get("missing_goal_object")),
            "missing_goal_target": bool(params.get("missing_goal_target")),
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
    blocked_open_target_ids: set[str] | frozenset[str] | None = None,
) -> ActionSpec:
    blocked_action_ids = blocked_action_ids or set()
    blocked_action_signatures = blocked_action_signatures or set()
    blocked_open_target_ids = blocked_open_target_ids or set()
    action_by_id = {action.action_id: action for action in legal_actions}
    selected = action_by_id.get(str(decision.get("action_id", "")))
    selected_signature = action_failure_signature(selected) if selected is not None else ""
    selected_is_blocked = selected is not None and (selected.action_id in blocked_action_ids or selected_signature in blocked_action_signatures)
    selected_open_is_blocked = _open_target_id(selected) in blocked_open_target_ids
    selected_needs_setup = selected is not None and selected.parameters.get("precondition_status") == "setup_required"
    selected_precondition_blocked = selected is not None and selected.parameters.get("precondition_status") == "blocked"
    post_grab_transition = preferred_post_grab_goal_transition(
        legal_actions,
        blocked_action_ids=blocked_action_ids,
        blocked_action_signatures=blocked_action_signatures,
        blocked_open_target_ids=blocked_open_target_ids,
    )
    if selected_is_blocked:
        decision["policy_override"] = {
            "reason": "avoid_repeated_failed_action",
            "blocked_action_id": selected.action_id,
            "blocked_action_signature": selected_signature,
        }
    if selected_open_is_blocked:
        decision["policy_override"] = {
            "reason": "avoid_repeated_failed_open",
            "blocked_action_id": selected.action_id,
            "blocked_open_target_id": _open_target_id(selected),
        }
    if selected_needs_setup:
        decision["policy_override"] = {
            "reason": "precondition_setup_required",
            "blocked_action_id": selected.action_id,
            "setup_action_id": selected.parameters.get("setup_action_id", ""),
        }
    if selected_precondition_blocked:
        decision["policy_override"] = {
            "reason": "precondition_blocked",
            "blocked_action_id": selected.action_id,
            "precondition_reason": selected.parameters.get("precondition_reason", ""),
        }
    if prefer_physical and post_grab_transition is not None:
        transition, goal_action = post_grab_transition
        replaceable_selection = (
            selected is None
            or selected.action_type in {"send_message", "wait", "walktowards"}
            or selected_is_blocked
            or selected_open_is_blocked
            or selected_needs_setup
            or selected_precondition_blocked
        )
        if replaceable_selection and (selected is None or transition.action_id != selected.action_id):
            decision["policy_override"] = {
                "reason": "post_grab_goal_transition",
                "action_id": transition.action_id,
                "action_type": transition.action_type,
                "goal_action_id": goal_action.action_id,
            }
            return transition
    if prefer_physical and (selected is None or selected.action_type in {"send_message", "wait"} or selected_is_blocked or selected_open_is_blocked or selected_needs_setup or selected_precondition_blocked):
        physical = preferred_physical_action(
            legal_actions,
            blocked_action_ids=blocked_action_ids,
            blocked_action_signatures=blocked_action_signatures,
            blocked_open_target_ids=blocked_open_target_ids,
        )
        if physical is not None and (selected is None or physical.action_id != selected.action_id):
            decision["policy_override"] = {"reason": "prefer_physical_after_steps", "action_id": physical.action_id, "action_type": physical.action_type}
            return physical
    if selected is not None and not selected_is_blocked and not selected_open_is_blocked and not selected_needs_setup and not selected_precondition_blocked:
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
                blocked_open_target_ids=blocked_open_target_ids,
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
    blocked_open_target_ids: set[str] | frozenset[str] | None = None,
) -> ActionSpec | None:
    blocked_action_ids = blocked_action_ids or set()
    blocked_action_signatures = blocked_action_signatures or set()
    blocked_open_target_ids = blocked_open_target_ids or set()
    post_grab_transition = preferred_post_grab_goal_transition(
        legal_actions,
        blocked_action_ids=blocked_action_ids,
        blocked_action_signatures=blocked_action_signatures,
        blocked_open_target_ids=blocked_open_target_ids,
    )
    if post_grab_transition is not None:
        return post_grab_transition[0]
    physical = [
        action
        for action in legal_actions
        if action.action_type not in {"send_message", "wait"}
        and action.action_id not in blocked_action_ids
        and action_failure_signature(action) not in blocked_action_signatures
        and _open_target_id(action) not in blocked_open_target_ids
        and action.parameters.get("precondition_status") not in {"blocked", "setup_required"}
    ]
    return min(physical, key=physical_action_rank, default=None)


def preferred_post_grab_goal_transition(
    legal_actions: tuple[ActionSpec, ...],
    *,
    blocked_action_ids: set[str] | frozenset[str] | None = None,
    blocked_action_signatures: set[str] | frozenset[str] | None = None,
    blocked_open_target_ids: set[str] | frozenset[str] | None = None,
) -> tuple[ActionSpec, ActionSpec] | None:
    blocked_action_ids = blocked_action_ids or set()
    blocked_action_signatures = blocked_action_signatures or set()
    blocked_open_target_ids = blocked_open_target_ids or set()
    action_by_id = {action.action_id: action for action in legal_actions}
    placements = sorted(
        (
            action
            for action in legal_actions
            if action.action_type in {"putin", "putback"}
            and action.parameters.get("hand_state") == "holding"
            and bool(action.parameters.get("goal_object_match"))
            and bool(action.parameters.get("goal_target_match"))
            and action.parameters.get("placement_relation_compatibility") == "goal_relation_match"
            and action.parameters.get("placement_suitability") == "goal_relation_match"
            and _action_is_available(action, blocked_action_ids, blocked_action_signatures, blocked_open_target_ids)
        ),
        key=physical_action_rank,
    )
    for placement in placements:
        status = placement.parameters.get("precondition_status")
        if status == "executable_now":
            return placement, placement
        if status != "setup_required":
            continue
        setup = action_by_id.get(str(placement.parameters.get("setup_action_id", "")))
        if (
            setup is not None
            and setup.action_type in {"walktowards", "open"}
            and setup.parameters.get("precondition_status") == "executable_now"
            and _action_is_available(setup, blocked_action_ids, blocked_action_signatures, blocked_open_target_ids)
        ):
            return setup, placement
    return None


def _action_is_available(
    action: ActionSpec,
    blocked_action_ids: set[str] | frozenset[str],
    blocked_action_signatures: set[str] | frozenset[str],
    blocked_open_target_ids: set[str] | frozenset[str],
) -> bool:
    return (
        action.action_id not in blocked_action_ids
        and action_failure_signature(action) not in blocked_action_signatures
        and _open_target_id(action) not in blocked_open_target_ids
        and action.parameters.get("precondition_status") != "blocked"
    )


def _open_target_id(action: ActionSpec | None) -> str:
    if action is None or action.action_type != "open":
        return ""
    return str(action.parameters.get("object_id", ""))


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
    placement_suitability = str(params.get("placement_suitability", ""))
    placement_relation_compatibility = str(params.get("placement_relation_compatibility", ""))
    container_suitability = str(params.get("container_suitability", ""))
    search_priority = str(params.get("search_priority", ""))
    if precondition_status == "setup_required":
        return (20, 0, action.action_id)
    if precondition_status == "blocked":
        return (30, 0, action.action_id)
    if action.action_type in {"putin", "putback"} and placement_relation_compatibility == "goal_relation_mismatch":
        return (13, 0, action.action_id)
    if action.action_type == "putin" and ("on" in goal_relations and "inside" not in goal_relations):
        return (13, 1, action.action_id)
    if action.action_type == "putin" and container_suitability == "container_likely_unsuitable":
        return (13, 2, action.action_id)
    if action.action_type == "putback" and "on" in goal_relations:
        return (0, 0, action.action_id)
    if action.action_type == "putin" and "inside" in goal_relations:
        return (0, 1, action.action_id)
    if action.action_type in {"putback", "putin"} and goal_object and goal_target:
        return (1, 0, action.action_id)
    if placement_suitability == "fallback_receptacle":
        return (12, 0, action.action_id)
    if action.action_type == "grab" and goal_object:
        return (2, 0, action.action_id)
    if action.action_type == "walktowards" and goal_object:
        return (2, 1, action.action_id)
    if action.action_type == "walktowards" and goal_target:
        return (2, 2, action.action_id)
    if action.action_type == "walktowards" and search_priority in {"search_goal_object_room", "search_goal_target_room"}:
        return (3, 0, action.action_id)
    if action.action_type == "walktowards" and search_priority in {"search_goal_object_receptacle", "search_goal_target_receptacle"}:
        return (3, 1, action.action_id)
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
    parser.add_argument("--model", default=os.environ.get("CWAH_LLM_MODEL", "gemma4:e4b"))
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--output", default="")
    parser.add_argument("--artifact-dir", default="", help="Optional directory for normalized summary.json, turns.jsonl, and metrics.csv artifacts.")
    parser.add_argument("--coela-cwah-path", default="")
    parser.add_argument("--dataset-path", default="")
    parser.add_argument("--executable-file", default="")
    parser.add_argument("--base-port", type=int, default=6314)
    parser.add_argument("--attempt-id", default="", help="Run attempt identifier supplied by an experiment harness")
    return parser.parse_args()


if __name__ == "__main__":
    main()
