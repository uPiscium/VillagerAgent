from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ActionSpec:
    action_id: str
    action_type: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InformationActionSpec(ActionSpec):
    information_subtype: str = ""


@dataclass(frozen=True)
class StepResult:
    step: int
    succeeded: bool
    observations: tuple[str, ...] = ()
    metrics: dict[str, int | float | bool] = field(default_factory=dict)
    error: str | None = None
