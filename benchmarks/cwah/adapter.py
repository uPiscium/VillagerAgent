from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from benchmarks.common.actions import ActionSpec, InformationActionSpec, StepResult
from benchmarks.common.decision import BudgetState, DecisionContext, TraceEvent
from benchmarks.common.episode import AgentCapabilities, EpisodeContext
from benchmarks.common.observation import ObservationRecord
from benchmarks.common.visibility import Visibility


@dataclass(frozen=True)
class CWAHConfig:
    episode_id: str = "cwah-symbolic-smoke"
    seed: int = 0
    task_id: int = 0
    max_steps: int = 250
    observation_type: str = "partial"
    agent_count: int = 2
    communication_base_cost: float = 1.0
    communication_cooldown_steps: int = 2
    metadata: dict[str, Any] = field(default_factory=dict)


class CWAHSymbolicAdapter:
    benchmark_name = "cwah"

    def __init__(self, *, config: CWAHConfig, env_factory: Callable[[CWAHConfig], Any]):
        self.config = config
        self.env_factory = env_factory
        self.env = None
        self.episode_id = config.episode_id
        self.step_index = 0
        self._last_observations: dict[str, dict[str, Any]] = {}
        self._public_events: list[TraceEvent] = []
        self._last_info: dict[str, Any] = {}
        self._terminal = False
        self._progress: float | None = None

    def reset(self, *, episode_id: str, seed: int) -> EpisodeContext:
        self.episode_id = episode_id
        self.step_index = 0
        self._public_events = []
        self._last_info = {}
        self._terminal = False
        self._progress = None
        self.env = self.env_factory(self.config)
        seed_fn = getattr(self.env, "seed", None)
        if callable(seed_fn):
            seed_fn(seed)
        observations = self.env.reset(task_id=self.config.task_id)
        self._last_observations = _normalize_observation_dict(observations, self.agent_ids())
        return EpisodeContext(
            benchmark=self.benchmark_name,
            episode_id=episode_id,
            seed=seed,
            agent_ids=self.agent_ids(),
            metadata={"task_id": self.config.task_id, **self.config.metadata},
        )

    def agent_ids(self) -> tuple[str, ...]:
        return tuple(f"agent_{index}" for index in range(self.config.agent_count))

    def capabilities(self, agent_id: str) -> AgentCapabilities:
        return AgentCapabilities(
            agent_id=agent_id,
            can_act=True,
            can_communicate=True,
            action_types=("walktowards", "grab", "open", "close", "putin", "putback", "wait"),
            information_action_types=("send_message",),
        )

    def get_observation(self, agent_id: str) -> tuple[ObservationRecord, ...]:
        obs = self._last_observations.get(agent_id, {})
        records = [_record_from_node(agent_id, self.episode_id, self.step_index, node) for node in obs.get("nodes", [])]
        records += [_record_from_edge(agent_id, self.episode_id, self.step_index, edge) for edge in obs.get("edges", [])]
        for sender_index, message in enumerate(obs.get("messages", []) or []):
            if message is None:
                continue
            records.append(ObservationRecord(
                observation_id=f"cwah:{self.episode_id}:{self.step_index}:message:{sender_index}:{agent_id}",
                benchmark=self.benchmark_name,
                episode_id=self.episode_id,
                step=self.step_index,
                observer_id=agent_id,
                visibility=Visibility(public=True),
                source_kind="agent_message",
                proposition={"predicate": "reported_message", "subject": f"agent_{sender_index}", "object": str(message)},
                confidence=1.0,
                grounding={"message": message},
            ))
        return tuple(records)

    def get_public_observation(self) -> tuple[ObservationRecord, ...]:
        records = []
        for event in self._public_events:
            if event.source_id is None:
                continue
            records.append(ObservationRecord(
                observation_id=event.source_id,
                benchmark=self.benchmark_name,
                episode_id=self.episode_id,
                step=event.step,
                observer_id="public",
                visibility=Visibility(public=True),
                source_kind=event.event_type,
                proposition=event.payload,
            ))
        return tuple(records)

    def get_legal_actions(self, agent_id: str) -> tuple[ActionSpec, ...]:
        if self.env is None:
            return ()
        action_space = self.env.get_action_space() if hasattr(self.env, "get_action_space") else {}
        agent_index = _agent_index(agent_id)
        visible_object_ids = action_space.get(agent_index, []) if isinstance(action_space, dict) else []
        observation = self._last_observations.get(agent_id, {})
        object_names = _visible_object_names(observation)
        actions = [ActionSpec(action_id=f"wait:{agent_id}", action_type="wait", parameters={})]
        actions.extend(
            ActionSpec(
                action_id=f"walktowards:{agent_id}:{object_id}",
                action_type="walktowards",
                parameters={"object_id": object_id, "object_name": object_names.get(object_id, "object")},
            )
            for object_id in visible_object_ids
        )
        actions.extend(_object_interaction_actions(agent_id, observation, visible_object_ids, object_names))
        actions.append(InformationActionSpec(
            action_id=f"send_message:{agent_id}",
            action_type="send_message",
            parameters={"message": ""},
            information_subtype="send_message",
        ))
        return tuple(actions)

    def decision_context(self, agent_id: str) -> DecisionContext:
        context = DecisionContext(
            benchmark=self.benchmark_name,
            episode_id=self.episode_id,
            step=self.step_index,
            actor_id=agent_id,
            visible_epistemic_nodes=tuple(record.__dict__ for record in self.get_observation(agent_id)),
            visible_candidates=tuple(_candidate_from_action(action) for action in self.get_legal_actions(agent_id)),
            legal_actions=self.get_legal_actions(agent_id),
            remaining_budget=BudgetState(remaining_steps=max(self.config.max_steps - self.step_index, 0)),
            recent_public_events=tuple(self._public_events[-10:]),
        )
        context.validate_agent_facing()
        return context

    def execute_action(self, agent_id: str, action: ActionSpec) -> StepResult:
        if action.action_type == "send_message":
            return self.execute_information_action(agent_id, InformationActionSpec(
                action_id=action.action_id,
                action_type=action.action_type,
                parameters=action.parameters,
                information_subtype="send_message",
            ))
        return self._step({ _agent_index(agent_id): _action_to_cwah_string(action) })

    def execute_information_action(self, agent_id: str, action: InformationActionSpec) -> StepResult:
        message = str(action.parameters.get("message", ""))
        if not message:
            message = "I need more information."
        return self._step({_agent_index(agent_id): f"[send_message] <{message}>"})

    def is_terminal(self) -> bool:
        return self._terminal

    def task_progress(self) -> float | None:
        return self._progress

    def final_metrics(self) -> dict[str, float | int | bool]:
        return {
            "task_success": bool(self._last_info.get("finished", self._terminal)),
            "normalized_progress": self._progress if self._progress is not None else 0.0,
            "episode_steps": self.step_index,
        }

    def _step(self, action_dict: dict[int, str]) -> StepResult:
        if self.env is None:
            raise RuntimeError("CWAHSymbolicAdapter.reset() must be called before execute_action().")
        result = self.env.step({agent_index: action_dict.get(agent_index) for agent_index in range(self.config.agent_count)})
        observations, _reward, done, info, messages = _unpack_step_result(result)
        self.step_index += 1
        self._terminal = bool(done)
        self._last_info = info
        self._progress = _progress_from_info(info)
        if observations is None and hasattr(self.env, "get_observations"):
            observations = self.env.get_observations()
        self._last_observations = _normalize_observation_dict(observations, self.agent_ids())
        for sender_index, message in enumerate(messages or []):
            if message is None:
                continue
            self._public_events.append(TraceEvent(
                event_type="public_message_sent",
                step=self.step_index,
                source_id=f"cwah:{self.episode_id}:{self.step_index}:message:{sender_index}",
                payload={"sender_id": f"agent_{sender_index}", "message": str(message)},
            ))
        return StepResult(
            step=self.step_index,
            succeeded=not bool(info.get("failed_exec", False)),
            observations=tuple(record.observation_id for agent_id in self.agent_ids() for record in self.get_observation(agent_id)),
            metrics={"communication_count": sum(1 for message in messages or [] if message is not None)},
            error=str(info.get("error", "")) or None,
        )


def _record_from_node(agent_id: str, episode_id: str, step: int, node: dict[str, Any]) -> ObservationRecord:
    node_id = node.get("id", node.get("node_id", "unknown"))
    return ObservationRecord(
        observation_id=f"cwah:{episode_id}:{step}:{agent_id}:node:{node_id}",
        benchmark="cwah",
        episode_id=episode_id,
        step=step,
        observer_id=agent_id,
        visibility=Visibility(visible_to=frozenset([agent_id])),
        source_kind="environment_observation",
        proposition={"predicate": "object_visible", "subject": str(node_id), "object": node.get("class_name", "object")},
        confidence=1.0,
        grounding={"node": node},
    )


def _record_from_edge(agent_id: str, episode_id: str, step: int, edge: dict[str, Any]) -> ObservationRecord:
    relation = str(edge.get("relation_type", "related_to")).lower()
    return ObservationRecord(
        observation_id=f"cwah:{episode_id}:{step}:{agent_id}:edge:{edge.get('from_id')}:{relation}:{edge.get('to_id')}",
        benchmark="cwah",
        episode_id=episode_id,
        step=step,
        observer_id=agent_id,
        visibility=Visibility(visible_to=frozenset([agent_id])),
        source_kind="environment_observation",
        proposition={"predicate": relation, "subject": str(edge.get("from_id")), "object": str(edge.get("to_id"))},
        confidence=1.0,
        grounding={"edge": edge},
    )


def _candidate_from_action(action: ActionSpec) -> dict[str, Any]:
    return {
        "candidate_id": action.action_id,
        "action_type": action.action_type,
        "parameters": dict(action.parameters),
        "state": "ready",
        "confidence": 1.0,
    }


def _visible_object_names(observation: dict[str, Any]) -> dict[Any, str]:
    return {
        node.get("id"): str(node.get("class_name", "object"))
        for node in observation.get("nodes", [])
        if isinstance(node, dict) and node.get("id") is not None
    }


def _object_interaction_actions(
    agent_id: str,
    observation: dict[str, Any],
    visible_object_ids: list[Any],
    object_names: dict[Any, str],
) -> list[ActionSpec]:
    visible_ids = set(visible_object_ids)
    nodes = [node for node in observation.get("nodes", []) if isinstance(node, dict)]
    held_ids = _held_object_ids(observation)
    receptacle_ids = {
        node.get("id")
        for node in nodes
        if node.get("id") in visible_ids and _has_any_token(node, {"CONTAINERS", "SURFACES", "RECIPIENT", "PLACEABLE"})
    }
    actions: list[ActionSpec] = []
    for node in nodes:
        object_id = node.get("id")
        if object_id not in visible_ids or node.get("category") in {"Rooms", "Characters"}:
            continue
        object_name = object_names.get(object_id, "object")
        if _has_any_token(node, {"GRABBABLE"}) and object_id not in held_ids:
            actions.append(ActionSpec(
                action_id=f"grab:{agent_id}:{object_id}",
                action_type="grab",
                parameters={"object_id": object_id, "object_name": object_name},
            ))
        if _has_any_token(node, {"CAN_OPEN", "OPENABLE", "CLOSED"}):
            actions.append(ActionSpec(
                action_id=f"open:{agent_id}:{object_id}",
                action_type="open",
                parameters={"object_id": object_id, "object_name": object_name},
            ))
        if _has_any_token(node, {"OPEN"}):
            actions.append(ActionSpec(
                action_id=f"close:{agent_id}:{object_id}",
                action_type="close",
                parameters={"object_id": object_id, "object_name": object_name},
            ))
    for held_id in held_ids:
        if held_id not in visible_ids:
            continue
        for target_id in receptacle_ids:
            if target_id == held_id:
                continue
            held_name = object_names.get(held_id, "object")
            target_name = object_names.get(target_id, "object")
            actions.append(ActionSpec(
                action_id=f"putin:{agent_id}:{held_id}:{target_id}",
                action_type="putin",
                parameters={"object_id": held_id, "object_name": held_name, "target_id": target_id, "target_name": target_name},
            ))
            actions.append(ActionSpec(
                action_id=f"putback:{agent_id}:{held_id}:{target_id}",
                action_type="putback",
                parameters={"object_id": held_id, "object_name": held_name, "target_id": target_id, "target_name": target_name},
            ))
    return actions


def _held_object_ids(observation: dict[str, Any]) -> set[Any]:
    character_ids = {
        node.get("id")
        for node in observation.get("nodes", [])
        if isinstance(node, dict) and node.get("category") == "Characters"
    }
    held_ids: set[Any] = set()
    for edge in observation.get("edges", []):
        if not isinstance(edge, dict):
            continue
        relation = str(edge.get("relation_type", "")).upper()
        if "HOLD" not in relation:
            continue
        from_id = edge.get("from_id")
        to_id = edge.get("to_id")
        if from_id in character_ids and to_id is not None:
            held_ids.add(to_id)
        elif to_id in character_ids and from_id is not None:
            held_ids.add(from_id)
    return held_ids


def _has_any_token(node: dict[str, Any], tokens: set[str]) -> bool:
    values = [*(node.get("properties") or []), *(node.get("states") or [])]
    return any(str(value).upper() in tokens for value in values)


def _action_to_cwah_string(action: ActionSpec) -> str:
    if action.action_type == "wait":
        return "[wait]"
    object_id = action.parameters.get("object_id")
    object_name = action.parameters.get("object_name", "object")
    if object_id is None:
        return f"[{action.action_type}]"
    target_id = action.parameters.get("target_id")
    if target_id is not None:
        target_name = action.parameters.get("target_name", "object")
        return f"[{action.action_type}] <{object_name}> ({object_id}) <{target_name}> ({target_id})"
    return f"[{action.action_type}] <{object_name}> ({object_id})"


def _normalize_observation_dict(observations: Any, agent_ids: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    if observations is None:
        return {agent_id: {"nodes": [], "edges": [], "messages": []} for agent_id in agent_ids}
    if isinstance(observations, dict):
        if all(isinstance(key, int) for key in observations):
            return {f"agent_{key}": value for key, value in observations.items()}
        if "nodes" in observations or "edges" in observations:
            return {agent_ids[0]: observations}
    if isinstance(observations, list | tuple):
        return {agent_ids[index]: observations[index] for index in range(min(len(agent_ids), len(observations)))}
    return {agent_id: {"nodes": [], "edges": [], "messages": []} for agent_id in agent_ids}


def _unpack_step_result(result: Any) -> tuple[Any, Any, bool, dict[str, Any], list[str | None]]:
    if isinstance(result, tuple) and len(result) == 5:
        observations, reward, done, info, messages = result
        return observations, reward, bool(done), info or {}, list(messages or [])
    if isinstance(result, tuple) and len(result) == 4:
        observations, reward, done, info = result
        return observations, reward, bool(done), info or {}, list((info or {}).get("messages", []) or [])
    raise ValueError(f"Unsupported C-WAH step result shape: {type(result)!r}")


def _progress_from_info(info: dict[str, Any]) -> float | None:
    progress = info.get("progress") if isinstance(info, dict) else None
    if not isinstance(progress, dict):
        return None
    satisfied = progress.get("satisfied", {}) or {}
    unsatisfied = progress.get("unsatisfied", {}) or {}
    satisfied_count = sum(len(value) if isinstance(value, list) else int(bool(value)) for value in satisfied.values())
    unsatisfied_count = sum(int(value) for value in unsatisfied.values() if isinstance(value, int | float))
    total = satisfied_count + unsatisfied_count
    if total == 0:
        return None
    return satisfied_count / total


def _agent_index(agent_id: str) -> int:
    return int(agent_id.rsplit("_", 1)[-1])
