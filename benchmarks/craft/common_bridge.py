from typing import Any

from benchmarks.common.actions import ActionSpec
from benchmarks.common.decision import BudgetState, DecisionContext, TraceEvent
from benchmarks.common.visibility import Visibility


def decision_context_from_runtime(
    *,
    runtime,
    agent_id: str,
    episode_id: str,
    step: int,
    legal_actions: tuple[ActionSpec, ...] = (),
    remaining_budget: BudgetState | None = None,
) -> DecisionContext:
    snapshot = runtime.serialized_snapshot()
    context = DecisionContext(
        benchmark="CRAFT",
        episode_id=episode_id,
        step=step,
        actor_id=agent_id,
        visible_epistemic_nodes=tuple(
            node for node in snapshot.get("epistemic_nodes", ())
            if _craft_node_visible_to(node, agent_id)
        ),
        visible_candidates=tuple(snapshot.get("action_nodes", ())),
        legal_actions=legal_actions,
        remaining_budget=remaining_budget or BudgetState(),
        recent_public_events=_public_events(snapshot),
    )
    context.validate_agent_facing()
    return context


def craft_visibility_from_provenance(provenance: dict[str, Any]) -> Visibility:
    visibility = provenance.get("visibility")
    if visibility == "public":
        return Visibility(public=True)
    if visibility == "private":
        director_id = provenance.get("director_id")
        return Visibility(visible_to=frozenset([str(director_id)] if director_id else ()))
    return Visibility(evaluator_only=True)


def _craft_node_visible_to(node: dict[str, Any], agent_id: str) -> bool:
    provenance = node.get("provenance", {}) if isinstance(node, dict) else {}
    return craft_visibility_from_provenance(provenance).allows(agent_id)


def _public_events(snapshot: dict[str, Any]) -> tuple[TraceEvent, ...]:
    events = []
    for node in snapshot.get("epistemic_nodes", ()):
        provenance = node.get("provenance", {}) if isinstance(node, dict) else {}
        if provenance.get("visibility") != "public":
            continue
        events.append(TraceEvent(
            event_type=str(node.get("node_type", "epistemic_node")),
            step=int(provenance.get("turn_index", 0) or 0),
            source_id=node.get("node_id"),
            payload={"node_id": node.get("node_id"), "node_type": node.get("node_type")},
        ))
    return tuple(events)
