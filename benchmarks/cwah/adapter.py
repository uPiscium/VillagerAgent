from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from benchmarks.common.actions import ActionSpec, InformationActionSpec, StepResult
from benchmarks.common.decision import BudgetState, DecisionContext, TraceEvent
from benchmarks.common.episode import AgentCapabilities, EpisodeContext
from benchmarks.common.observation import ObservationRecord
from benchmarks.common.visibility import Visibility
from benchmarks.cwah.dual_dag import CWAHDualDAGRuntime


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
        self._task_goal_hints: dict[int, tuple[dict[str, Any], ...]] = {}
        self.dual_dag = CWAHDualDAGRuntime(episode_id=self.episode_id)

    def reset(self, *, episode_id: str, seed: int) -> EpisodeContext:
        self.episode_id = episode_id
        self.step_index = 0
        self._public_events = []
        self._last_info = {}
        self._terminal = False
        self._progress = None
        self.dual_dag = CWAHDualDAGRuntime(episode_id=episode_id)
        self.env = self.env_factory(self.config)
        seed_fn = getattr(self.env, "seed", None)
        if callable(seed_fn):
            seed_fn(seed)
        observations = self.env.reset(task_id=self.config.task_id)
        self._last_observations = _normalize_observation_dict(observations, self.agent_ids())
        self._task_goal_hints = _task_goal_hints(self.env)
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
        agent_goal_hints = self._task_goal_hints.get(_agent_index(agent_id), ())
        records += [_record_from_goal_hint(agent_id, self.episode_id, self.step_index, index, hint) for index, hint in enumerate(agent_goal_hints)]
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
        nodes = [node for node in observation.get("nodes", []) if isinstance(node, dict)]
        nodes_by_id = {node.get("id"): node for node in nodes}
        held_object_ids = _held_object_ids(observation, character_id=agent_index + 1)
        agent_goal_hints = self._task_goal_hints.get(agent_index, ())
        actions = [ActionSpec(action_id=f"wait:{agent_id}", action_type="wait", parameters={})]
        actions.extend(
            ActionSpec(
                action_id=f"walktowards:{agent_id}:{object_id}",
                action_type="walktowards",
                parameters={
                    "object_id": object_id,
                    "object_name": object_names.get(object_id, "object"),
                    **_navigation_preconditions(object_id, held_object_ids),
                    **_goal_relevance_for_object(object_id, object_names.get(object_id, "object"), agent_goal_hints),
                    **_navigation_search_metadata(object_id, object_names, nodes_by_id, agent_goal_hints),
                },
            )
            for object_id in visible_object_ids
        )
        actions.extend(_object_interaction_actions(
            agent_id,
            observation,
            visible_object_ids,
            object_names,
            agent_goal_hints,
            held_object_ids=held_object_ids,
        ))
        actions.append(InformationActionSpec(
            action_id=f"send_message:{agent_id}",
            action_type="send_message",
            parameters={"message": ""},
            information_subtype="send_message",
        ))
        return tuple(actions)

    def decision_context(self, agent_id: str) -> DecisionContext:
        observations = self.get_observation(agent_id)
        public_observations = self.get_public_observation()
        legal_actions = self.get_legal_actions(agent_id)
        self.dual_dag.update_observations((*observations, *public_observations))
        self.dual_dag.update_action_candidates(agent_id=agent_id, actions=legal_actions)
        context = DecisionContext(
            benchmark=self.benchmark_name,
            episode_id=self.episode_id,
            step=self.step_index,
            actor_id=agent_id,
            visible_epistemic_nodes=self.dual_dag.visible_epistemic_nodes(agent_id),
            visible_candidates=self.dual_dag.visible_action_candidates(agent_id),
            legal_actions=legal_actions,
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
        result = self._step({_agent_index(agent_id): _action_to_cwah_string(action)})
        self.dual_dag.record_action_outcome(agent_id=agent_id, action=action, result=result)
        return result

    def execute_information_action(self, agent_id: str, action: InformationActionSpec) -> StepResult:
        message = str(action.parameters.get("message", ""))
        if not message:
            message = "I need more information."
        result = self._step({_agent_index(agent_id): f"[send_message] <{message}>"})
        self.dual_dag.record_action_outcome(agent_id=agent_id, action=action, result=result)
        return result

    def dual_dag_snapshot(self) -> dict[str, Any]:
        return self.dual_dag.snapshot()

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
            error=_error_from_info(info),
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


def _record_from_goal_hint(agent_id: str, episode_id: str, step: int, index: int, hint: dict[str, Any]) -> ObservationRecord:
    return ObservationRecord(
        observation_id=f"cwah:{episode_id}:{step}:{agent_id}:task_goal:{index}",
        benchmark="cwah",
        episode_id=episode_id,
        step=step,
        observer_id=agent_id,
        visibility=Visibility(visible_to=frozenset([agent_id])),
        source_kind="task_goal",
        proposition={"predicate": "task_goal", **hint},
        confidence=1.0,
        grounding={"task_goal_hint": hint},
    )


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
    task_goal_hints: tuple[dict[str, Any], ...] = (),
    *,
    held_object_ids: set[Any] | None = None,
) -> list[ActionSpec]:
    visible_ids = set(visible_object_ids)
    nodes = [node for node in observation.get("nodes", []) if isinstance(node, dict)]
    held_ids = held_object_ids or set()
    held_object_names = {held_id: object_names.get(held_id, "object") for held_id in held_ids}
    close_ids = _close_object_ids(observation)
    nodes_by_id = {node.get("id"): node for node in nodes}
    container_ids = {
        node.get("id")
        for node in nodes
        if node.get("id") in visible_ids and _has_any_token(node, {"CONTAINERS"})
    }
    surface_ids = {
        node.get("id")
        for node in nodes
        if node.get("id") in visible_ids and _has_any_token(node, {"SURFACES", "RECIPIENT", "PLACEABLE"})
    }
    hand_state = _hand_state_metadata(held_object_names)
    actions: list[ActionSpec] = []
    for node in nodes:
        object_id = node.get("id")
        if object_id not in visible_ids or node.get("category") in {"Rooms", "Characters"}:
            continue
        object_name = object_names.get(object_id, "object")
        goal_relevance = _goal_relevance_for_object(object_id, object_name, task_goal_hints)
        if _has_any_token(node, {"GRABBABLE"}) and object_id not in held_ids:
            preconditions = _grab_preconditions(agent_id, object_id, close_ids, held_object_names)
            actions.append(ActionSpec(
                action_id=f"grab:{agent_id}:{object_id}",
                action_type="grab",
                parameters={"object_id": object_id, "object_name": object_name, **hand_state, **preconditions, **goal_relevance},
            ))
        if _has_any_token(node, {"CLOSED"}) or (_has_any_token(node, {"CAN_OPEN", "OPENABLE"}) and not _has_any_token(node, {"OPEN"})):
            preconditions = _interaction_preconditions(agent_id, object_id, close_ids)
            actions.append(ActionSpec(
                action_id=f"open:{agent_id}:{object_id}",
                action_type="open",
                parameters={"object_id": object_id, "object_name": object_name, **hand_state, **preconditions, **goal_relevance},
            ))
        if _has_any_token(node, {"OPEN"}):
            preconditions = _interaction_preconditions(agent_id, object_id, close_ids)
            actions.append(ActionSpec(
                action_id=f"close:{agent_id}:{object_id}",
                action_type="close",
                parameters={"object_id": object_id, "object_name": object_name, **hand_state, **preconditions, **goal_relevance},
            ))
    for held_id in held_ids:
        if held_id not in visible_ids:
            continue
        for target_id in container_ids:
            if target_id == held_id:
                continue
            held_name = object_names.get(held_id, "object")
            target_name = object_names.get(target_id, "object")
            placement_relevance = _goal_relevance_for_placement(held_id, held_name, target_id, target_name, task_goal_hints)
            target_metadata = _placement_target_metadata("inside", nodes_by_id.get(target_id, {}), placement_relevance)
            preconditions = _placement_preconditions(agent_id, target_id, close_ids, nodes_by_id.get(target_id, {}))
            actions.append(ActionSpec(
                action_id=f"putin:{agent_id}:{held_id}:{target_id}",
                action_type="putin",
                parameters={"object_id": held_id, "object_name": held_name, "target_id": target_id, "target_name": target_name, **hand_state, **preconditions, **placement_relevance, **target_metadata},
            ))
        for target_id in surface_ids:
            if target_id == held_id:
                continue
            held_name = object_names.get(held_id, "object")
            target_name = object_names.get(target_id, "object")
            placement_relevance = _goal_relevance_for_placement(held_id, held_name, target_id, target_name, task_goal_hints)
            target_metadata = _placement_target_metadata("on", nodes_by_id.get(target_id, {}), placement_relevance)
            preconditions = _placement_preconditions(agent_id, target_id, close_ids, nodes_by_id.get(target_id, {}))
            actions.append(ActionSpec(
                action_id=f"putback:{agent_id}:{held_id}:{target_id}",
                action_type="putback",
                parameters={"object_id": held_id, "object_name": held_name, "target_id": target_id, "target_name": target_name, **hand_state, **preconditions, **placement_relevance, **target_metadata},
            ))
    return actions


def _placement_target_metadata(relation: str, target_node: dict[str, Any], placement_relevance: dict[str, Any]) -> dict[str, Any]:
    target_affordance = _target_affordance(target_node)
    relation_matches = set(placement_relevance.get("goal_relation_matches", ()))
    if relation in relation_matches:
        suitability = "goal_relation_match"
    elif relation == "inside" and target_affordance == "container":
        suitability = "compatible_container"
    elif relation == "on" and target_affordance == "surface":
        suitability = "compatible_surface"
    else:
        suitability = "fallback_receptacle"
    return {
        "placement_relation": relation,
        "target_affordance": target_affordance,
        "placement_suitability": suitability,
        "placement_relation_compatibility": _placement_relation_compatibility(relation, relation_matches),
        "container_suitability": _container_suitability(relation, target_node, target_affordance, relation_matches),
    }


def _placement_relation_compatibility(relation: str, relation_matches: set[str]) -> str:
    if relation in relation_matches:
        return "goal_relation_match"
    opposite = {"inside": "on", "on": "inside"}.get(relation, "")
    if opposite and opposite in relation_matches:
        return "goal_relation_mismatch"
    return "goal_relation_unknown"


def _target_affordance(target_node: dict[str, Any]) -> str:
    if _has_any_token(target_node, {"CONTAINERS"}):
        return "container"
    if _has_any_token(target_node, {"SURFACES"}):
        return "surface"
    if _has_any_token(target_node, {"RECIPIENT"}):
        return "recipient"
    if _has_any_token(target_node, {"PLACEABLE"}):
        return "placeable"
    return "unknown"


def _container_suitability(relation: str, target_node: dict[str, Any], target_affordance: str, relation_matches: set[str]) -> str:
    if relation != "inside":
        return ""
    if "on" in relation_matches and "inside" not in relation_matches:
        return "container_likely_unsuitable"
    if target_affordance != "container":
        return "container_likely_unsuitable"
    if _has_any_token(target_node, {"OPEN"}):
        return "container_open"
    if _has_any_token(target_node, {"CLOSED"}) or _has_any_token(target_node, {"CAN_OPEN", "OPENABLE"}):
        return "container_closed_needs_open"
    return "container_unknown"


def _hand_state_metadata(held_object_names: dict[Any, str]) -> dict[str, Any]:
    held_items = tuple(
        {"object_id": object_id, "object_name": held_object_names[object_id]}
        for object_id in sorted(held_object_names, key=str)
    )
    first_held = held_items[0] if held_items else {}
    return {
        "hand_state": "holding" if held_items else "empty",
        "held_objects": held_items,
        "held_object_id": first_held.get("object_id"),
        "held_object_name": first_held.get("object_name"),
    }


def _grab_preconditions(agent_id: str, object_id: Any, close_ids: set[Any], held_object_names: dict[Any, str]) -> dict[str, Any]:
    if held_object_names:
        return {
            "precondition_status": "blocked",
            "precondition_reason": "blocked_by_holding_object",
        }
    return _interaction_preconditions(agent_id, object_id, close_ids)


def _interaction_preconditions(agent_id: str, object_id: Any, close_ids: set[Any]) -> dict[str, Any]:
    if object_id in close_ids:
        return {"precondition_status": "executable_now", "precondition_reason": "actor_close_to_object"}
    return {
        "precondition_status": "setup_required",
        "precondition_reason": "needs_walktowards_object",
        "setup_action_id": f"walktowards:{agent_id}:{object_id}",
    }


def _navigation_preconditions(object_id: Any, held_object_ids: set[Any]) -> dict[str, Any]:
    if object_id in held_object_ids:
        return {
            "precondition_status": "blocked",
            "precondition_reason": "blocked_by_holding_target_object",
        }
    return {
        "precondition_status": "executable_now",
        "precondition_reason": "navigation_action",
    }


def _placement_preconditions(agent_id: str, target_id: Any, close_ids: set[Any], target_node: dict[str, Any]) -> dict[str, Any]:
    if target_id not in close_ids:
        return {
            "precondition_status": "setup_required",
            "precondition_reason": "needs_walktowards_target",
            "setup_action_id": f"walktowards:{agent_id}:{target_id}",
        }
    if _has_any_token(target_node, {"CLOSED"}) or (_has_any_token(target_node, {"CAN_OPEN", "OPENABLE"}) and not _has_any_token(target_node, {"OPEN"})):
        return {
            "precondition_status": "setup_required",
            "precondition_reason": "needs_open_target",
            "setup_action_id": f"open:{agent_id}:{target_id}",
        }
    return {"precondition_status": "executable_now", "precondition_reason": "actor_close_to_target"}


def _task_goal_hints(env: Any) -> dict[int, tuple[dict[str, Any], ...]]:
    goal_spec = getattr(env, "goal_spec", None)
    task_goal = getattr(env, "task_goal", None)
    raw_goals_by_agent: dict[int, list[tuple[str, Any]]] = {}
    if isinstance(goal_spec, dict):
        for agent_id, agent_goal in goal_spec.items():
            if isinstance(agent_goal, dict):
                raw_goals_by_agent[int(agent_id)] = [(str(predicate), value) for predicate, value in agent_goal.items()]
    if not raw_goals_by_agent and isinstance(task_goal, dict):
        for agent_id, agent_goal in task_goal.items():
            if isinstance(agent_goal, dict):
                raw_goals_by_agent[int(agent_id)] = [(str(predicate), value) for predicate, value in agent_goal.items()]
    hints_by_agent: dict[int, tuple[dict[str, Any], ...]] = {}
    for agent_id, raw_goals in raw_goals_by_agent.items():
        hints_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        for predicate, value in raw_goals:
            hint = _parse_goal_predicate(predicate, value)
            if hint is None:
                continue
            key = (str(hint.get("relation", "")), str(hint.get("object_class", "")), str(hint.get("target_id", "")))
            hints_by_key[key] = hint
        hints_by_agent[agent_id] = tuple(hints_by_key[key] for key in sorted(hints_by_key))
    return hints_by_agent


def _parse_goal_predicate(predicate: str, value: Any) -> dict[str, Any] | None:
    parts = predicate.split("_", 2)
    if len(parts) < 3:
        return None
    relation, object_class, target_part = parts
    if relation not in {"on", "inside"}:
        return None
    count = value[0] if isinstance(value, list | tuple) and value else value
    target_id_match = re.search(r"\((\d+)\)$", target_part) or re.search(r"(\d+)$", target_part)
    target_name_match = re.search(r"<([^>]+)>", target_part)
    target_id = int(target_id_match.group(1)) if target_id_match else None
    target_class = target_name_match.group(1) if target_name_match else re.sub(r"\s*\(\d+\)$", "", target_part)
    return {
        "relation": relation,
        "object_class": object_class,
        "target_id": target_id,
        "target_class": target_class,
        "count": int(count or 0),
    }


def _goal_relevance_for_object(object_id: Any, object_name: str, task_goal_hints: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    normalized_name = _normalize_name(object_name)
    goal_object = any(_normalize_name(str(hint.get("object_class", ""))) == normalized_name for hint in task_goal_hints)
    goal_target = any(_goal_target_matches(hint, object_id, normalized_name) for hint in task_goal_hints)
    return {
        "goal_object_match": goal_object,
        "goal_target_match": goal_target,
    }


def _navigation_search_metadata(
    object_id: Any,
    object_names: dict[Any, str],
    nodes_by_id: dict[Any, dict[str, Any]],
    task_goal_hints: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    visible_names = {_normalize_name(name) for name in object_names.values()}
    missing_goal_object = any(_normalize_name(str(hint.get("object_class", ""))) not in visible_names for hint in task_goal_hints)
    missing_goal_target = any(
        not any(
            _goal_target_matches(hint, object_id, _normalize_name(name))
            for object_id, name in object_names.items()
        )
        for hint in task_goal_hints
    )
    node = nodes_by_id.get(object_id, {})
    target_affordance = _target_affordance(node)
    is_room = node.get("category") == "Rooms"
    search_priority = "none"
    search_reason = ""
    if missing_goal_object and is_room:
        search_priority = "search_goal_object_room"
        search_reason = "goal_object_not_visible"
    elif missing_goal_object and target_affordance in {"container", "surface", "recipient", "placeable"}:
        search_priority = "search_goal_object_receptacle"
        search_reason = "goal_object_not_visible"
    elif missing_goal_target and is_room:
        search_priority = "search_goal_target_room"
        search_reason = "goal_target_not_visible"
    elif missing_goal_target and target_affordance in {"container", "surface", "recipient", "placeable"}:
        search_priority = "search_goal_target_receptacle"
        search_reason = "goal_target_not_visible"
    return {
        "search_priority": search_priority,
        "search_reason": search_reason,
        "missing_goal_object": missing_goal_object,
        "missing_goal_target": missing_goal_target,
    }


def _goal_relevance_for_placement(
    object_id: Any,
    object_name: str,
    target_id: Any,
    target_name: str,
    task_goal_hints: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    object_norm = _normalize_name(object_name)
    target_norm = _normalize_name(target_name)
    target_hints = [
        hint
        for hint in task_goal_hints
        if _goal_target_matches(hint, target_id, target_norm)
    ]
    matched_relations = [
        str(hint.get("relation"))
        for hint in target_hints
        if _normalize_name(str(hint.get("object_class", ""))) == object_norm
    ]
    return {
        "goal_object_match": any(_normalize_name(str(hint.get("object_class", ""))) == object_norm for hint in task_goal_hints),
        "goal_target_match": bool(target_hints),
        "goal_relation_matches": tuple(sorted(set(matched_relations))),
    }


def _goal_target_matches(hint: dict[str, Any], target_id: Any, normalized_target_name: str) -> bool:
    if hint.get("target_id") is not None:
        return hint.get("target_id") == target_id
    return _normalize_name(str(hint.get("target_class", ""))) == normalized_target_name


def _normalize_name(value: str) -> str:
    return value.replace("_", "").replace(" ", "").lower()


def _held_object_ids(observation: dict[str, Any], *, character_id: int) -> set[Any]:
    held_ids: set[Any] = set()
    for edge in observation.get("edges", []):
        if not isinstance(edge, dict):
            continue
        relation = str(edge.get("relation_type", "")).upper()
        if "HOLD" not in relation:
            continue
        from_id = edge.get("from_id")
        to_id = edge.get("to_id")
        if from_id == character_id and to_id is not None:
            held_ids.add(to_id)
        elif to_id == character_id and from_id is not None:
            held_ids.add(from_id)
    return held_ids


def _close_object_ids(observation: dict[str, Any]) -> set[Any]:
    character_ids = {
        node.get("id")
        for node in observation.get("nodes", [])
        if isinstance(node, dict) and node.get("category") == "Characters"
    }
    close_ids: set[Any] = set()
    for edge in observation.get("edges", []):
        if not isinstance(edge, dict):
            continue
        if str(edge.get("relation_type", "")).upper() != "CLOSE":
            continue
        from_id = edge.get("from_id")
        to_id = edge.get("to_id")
        if from_id in character_ids and to_id is not None:
            close_ids.add(to_id)
        elif to_id in character_ids and from_id is not None:
            close_ids.add(from_id)
    return close_ids


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


def _error_from_info(info: dict[str, Any]) -> str | None:
    explicit_error = str(info.get("error", ""))
    if explicit_error:
        return explicit_error
    if not info.get("failed_exec"):
        return None
    messages = list(_message_values(info))
    return " | ".join(messages) if messages else "execution_failed"


def _message_values(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"message", "error"} and item:
                yield str(item)
            else:
                yield from _message_values(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _message_values(item)


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
