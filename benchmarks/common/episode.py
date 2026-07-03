from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EpisodeContext:
    benchmark: str
    episode_id: str
    seed: int
    agent_ids: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentCapabilities:
    agent_id: str
    can_act: bool = True
    can_communicate: bool = False
    action_types: tuple[str, ...] = ()
    information_action_types: tuple[str, ...] = ()
