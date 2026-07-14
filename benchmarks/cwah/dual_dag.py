from __future__ import annotations

from typing import Any, Iterable

from benchmarks.common.actions import ActionSpec, StepResult
from benchmarks.common.observation import ObservationRecord


DUAL_DAG_SCHEMA_VERSION = 1
_FORBIDDEN_KEYS = {
    "adapter",
    "api_key",
    "credentials",
    "debug",
    "env",
    "environment",
    "evaluator_progress",
    "evaluator_snapshot",
    "full_graph",
    "hidden_state",
    "simulator_debug",
}


class CWAHDualDAGRuntime:
    """Agent-facing projection of sanitized C-WAH observations and actions."""

    def __init__(self, *, episode_id: str):
        self.episode_id = episode_id
        self.epistemic_nodes: dict[str, dict[str, Any]] = {}
        self.action_nodes: dict[str, dict[str, Any]] = {}
        self.action_edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    def update_observations(self, records: Iterable[ObservationRecord]) -> None:
        for record in records:
            visibility = {
                "visible_to": sorted(record.visibility.visible_to),
                "public": record.visibility.public,
                "evaluator_only": record.visibility.evaluator_only,
            }
            self.epistemic_nodes[record.observation_id] = _sanitize({
                "node_id": record.observation_id,
                "node_type": record.source_kind,
                "source_kind": record.source_kind,
                "step": record.step,
                "observer_id": record.observer_id,
                "visibility": visibility,
                "proposition": record.proposition,
                "confidence": record.confidence,
                "grounding": record.grounding,
            })

    def update_action_candidates(self, *, agent_id: str, actions: Iterable[ActionSpec]) -> None:
        current_action_ids = set()
        for action in actions:
            current_action_ids.add(action.action_id)
            previous = self.action_nodes.get(action.action_id, {})
            candidate = _candidate_from_action(agent_id=agent_id, action=action)
            if "last_outcome" in previous:
                candidate["last_outcome"] = previous["last_outcome"]
            self.action_nodes[action.action_id] = candidate
            for edge_key in list(self.action_edges):
                if edge_key[1] == action.action_id and edge_key[2] == "enables":
                    del self.action_edges[edge_key]
            setup_action_id = candidate.get("setup_action_id")
            if setup_action_id:
                edge = {
                    "source_id": setup_action_id,
                    "target_id": action.action_id,
                    "edge_type": "enables",
                    "metadata": {"reason": candidate.get("precondition_reason", "")},
                }
                self.action_edges[(setup_action_id, action.action_id, "enables")] = edge

        for action_id, candidate in self.action_nodes.items():
            if candidate.get("actor_id") == agent_id:
                candidate["currently_legal"] = action_id in current_action_ids

    def record_action_outcome(self, *, agent_id: str, action: ActionSpec, result: StepResult) -> None:
        candidate = self.action_nodes.setdefault(
            action.action_id,
            _candidate_from_action(agent_id=agent_id, action=action),
        )
        candidate["last_outcome"] = _sanitize({
            "step": result.step,
            "succeeded": result.succeeded,
            "error": result.error,
        })

    def visible_epistemic_nodes(self, agent_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(
            node
            for node in self.epistemic_nodes.values()
            if _visible_to(node.get("visibility", {}), agent_id)
        )

    def visible_action_candidates(self, agent_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(
            candidate
            for candidate in self.action_nodes.values()
            if candidate.get("actor_id") == agent_id and candidate.get("currently_legal", False)
        )

    def snapshot(self) -> dict[str, Any]:
        return _sanitize({
            "schema_version": DUAL_DAG_SCHEMA_VERSION,
            "benchmark": "cwah",
            "episode_id": self.episode_id,
            "epistemic_dag": {
                "nodes": list(self.epistemic_nodes.values()),
                "edges": [],
            },
            "action_candidate_dag": {
                "nodes": list(self.action_nodes.values()),
                "edges": list(self.action_edges.values()),
            },
        })


def _candidate_from_action(*, agent_id: str, action: ActionSpec) -> dict[str, Any]:
    parameters = _sanitize(dict(action.parameters))
    precondition_status = str(parameters.get("precondition_status", "unknown"))
    if action.action_type == "send_message":
        state = "information_action"
    elif precondition_status in {"executable_now", "setup_required", "blocked"}:
        state = precondition_status
    else:
        state = "ready"
    return {
        "node_id": action.action_id,
        "candidate_id": action.action_id,
        "actor_id": agent_id,
        "action_type": action.action_type,
        "parameters": parameters,
        "state": state,
        "confidence": 1.0,
        "precondition_status": precondition_status,
        "precondition_reason": parameters.get("precondition_reason", ""),
        "setup_action_id": parameters.get("setup_action_id", ""),
        "currently_legal": True,
    }


def _visible_to(visibility: dict[str, Any], agent_id: str) -> bool:
    if visibility.get("evaluator_only"):
        return False
    return bool(visibility.get("public")) or agent_id in visibility.get("visible_to", ())


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if str(key).lower() not in _FORBIDDEN_KEYS
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_sanitize(item) for item in value]
    return value
