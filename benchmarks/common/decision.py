from dataclasses import dataclass, field
from typing import Any

from benchmarks.common.actions import ActionSpec


@dataclass(frozen=True)
class BudgetState:
    remaining_steps: int | None = None
    remaining_information_actions: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TraceEvent:
    event_type: str
    step: int
    source_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionContext:
    benchmark: str
    episode_id: str
    step: int
    actor_id: str
    visible_epistemic_nodes: tuple[dict[str, Any], ...]
    visible_candidates: tuple[dict[str, Any], ...]
    legal_actions: tuple[ActionSpec, ...]
    remaining_budget: BudgetState
    recent_public_events: tuple[TraceEvent, ...] = ()

    def validate_agent_facing(self) -> None:
        forbidden = {"adapter", "environment", "env", "evaluator_snapshot", "hidden_state"}
        payloads: list[dict[str, Any]] = [
            *self.visible_epistemic_nodes,
            *self.visible_candidates,
            *(event.payload for event in self.recent_public_events),
        ]
        for payload in payloads:
            _reject_forbidden_keys(payload, forbidden)


def _reject_forbidden_keys(value: Any, forbidden: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in forbidden:
                raise ValueError(f"Agent-facing DecisionContext contains forbidden key: {key}")
            _reject_forbidden_keys(item, forbidden)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_forbidden_keys(item, forbidden)
