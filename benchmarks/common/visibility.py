from dataclasses import dataclass, field
from typing import Mapping, Any


@dataclass(frozen=True)
class Visibility:
    visible_to: frozenset[str] = field(default_factory=frozenset)
    public: bool = False
    evaluator_only: bool = False

    def allows(self, agent_id: str) -> bool:
        if self.evaluator_only:
            return False
        return self.public or agent_id in self.visible_to


@dataclass(frozen=True)
class PromptArtifact:
    text: str
    included_source_ids: tuple[str, ...]


def visibility_from_value(value: Any) -> Visibility:
    if isinstance(value, Visibility):
        return value
    if isinstance(value, str):
        return Visibility(public=value == "public", evaluator_only=value == "evaluator_only")
    if isinstance(value, Mapping):
        return Visibility(
            visible_to=frozenset(str(agent_id) for agent_id in value.get("visible_to", ()) or ()),
            public=bool(value.get("public", False)),
            evaluator_only=bool(value.get("evaluator_only", False)),
        )
    return Visibility(evaluator_only=True)


def source_visibility_violations(
    *,
    agent_id: str,
    included_source_ids: tuple[str, ...] | list[str],
    source_visibility: Mapping[str, Any],
) -> list[dict]:
    violations = []
    for source_id in included_source_ids:
        visibility_value = source_visibility.get(source_id)
        if visibility_value is None:
            violations.append({
                "label": "source_visibility",
                "source_id": source_id,
                "reason": "unknown_source",
            })
            continue
        visibility = visibility_from_value(visibility_value)
        if not visibility.allows(agent_id):
            violations.append({
                "label": "source_visibility",
                "source_id": source_id,
                "reason": "evaluator_only" if visibility.evaluator_only else "not_visible_to_agent",
            })
    return violations
