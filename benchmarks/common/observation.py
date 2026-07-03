from dataclasses import dataclass, field
from typing import Any

from benchmarks.common.visibility import Visibility


@dataclass(frozen=True)
class ObservationRecord:
    observation_id: str
    benchmark: str
    episode_id: str
    step: int
    observer_id: str
    visibility: Visibility
    source_kind: str
    proposition: dict[str, Any]
    confidence: float = 1.0
    grounding: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
