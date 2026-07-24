from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from benchmarks.common.actions import ActionSpec, InformationActionSpec, StepResult
from benchmarks.common.decision import BudgetState, DecisionContext, TraceEvent
from benchmarks.common.episode import AgentCapabilities, EpisodeContext
from benchmarks.common.observation import ObservationRecord
from benchmarks.common.visibility import Visibility


@dataclass(frozen=True)
class TDWMATConfig:
    scene: str = "5a"
    layout: str = "0_0"
    task: str = "food"
    seed: int = 2824
    agent_count: int = 2
    max_frames: int = 3000
    metadata: dict[str, Any] = field(default_factory=dict)

    def options(self) -> dict[str, str]:
        return {"scene": self.scene, "layout": self.layout, "task": self.task}


class TDWMATAdapter:
    benchmark_name = "tdw_mat"

    def __init__(self, *, config: TDWMATConfig, env_factory: Callable[[TDWMATConfig], Any]):
        self.config = config
        self.env_factory = env_factory
        self.env: Any = None
        self.episode_id = "tdw-mat-smoke"
        self.step_index = 0
        self._observations: dict[str, dict[str, Any]] = {}
        self._goal_description: dict[str, int] = {}
        self._public_events: list[TraceEvent] = []
        self._terminal = False
        self._transported = 0
        self._target_total = 0
        self._physical_actions = 0
        self._communication_actions = 0
        self._goal_relevant_communications = 0
        self._invalid_physical_actions = 0
        self._physical_frames = 0
        self._communication_frames = 0
        self._feasibility_true_positive = 0
        self._feasibility_false_positive = 0
        self._feasibility_true_negative = 0
        self._feasibility_false_negative = 0
        self._failed_physical_actions = 0
        self._recovered_failures = 0
        self._pending_failure = False
        self._pending_information_step: int | None = None
        self._information_progress_latencies: list[int] = []

    def reset(self, *, episode_id: str, seed: int) -> EpisodeContext:
        self.episode_id = episode_id
        self.step_index = 0
        self._public_events = []
        self._terminal = False
        self._transported = 0
        self._target_total = 0
        self._physical_actions = 0
        self._communication_actions = 0
        self._goal_relevant_communications = 0
        self._invalid_physical_actions = 0
        self._physical_frames = 0
        self._communication_frames = 0
        self._feasibility_true_positive = 0
        self._feasibility_false_positive = 0
        self._feasibility_true_negative = 0
        self._feasibility_false_negative = 0
        self._failed_physical_actions = 0
        self._recovered_failures = 0
        self._pending_failure = False
        self._pending_information_step = None
        self._information_progress_latencies = []
        self.env = self.env_factory(self.config)
        reset_result = self.env.reset(seed=seed, options=self.config.options())
        observations, info = _unpack_reset(reset_result)
        self._observations = _normalize_observations(observations, self.agent_ids())
        self._goal_description = {
            str(name): int(count) for name, count in (info.get("goal_description") or {}).items()
        }
        self._refresh_goal()
        return EpisodeContext(
            benchmark=self.benchmark_name,
            episode_id=episode_id,
            seed=seed,
            agent_ids=self.agent_ids(),
            metadata={
                "scene": self.config.scene,
                "layout": self.config.layout,
                "task": self.config.task,
                **self.config.metadata,
            },
        )

    def agent_ids(self) -> tuple[str, ...]:
        return tuple(f"agent_{index}" for index in range(self.config.agent_count))

    def capabilities(self, agent_id: str) -> AgentCapabilities:
        return AgentCapabilities(
            agent_id=agent_id,
            can_act=True,
            can_communicate=True,
            action_types=("move_forward", "turn_left", "turn_right", "grasp", "put_in", "drop"),
            information_action_types=("send_message",),
        )

    def get_observation(self, agent_id: str) -> tuple[ObservationRecord, ...]:
        observation = self._observations.get(agent_id, {})
        records: list[ObservationRecord] = []
        for object_index, visible_object in enumerate(observation.get("visible_objects", ())):
            if not isinstance(visible_object, dict) or visible_object.get("id") is None:
                continue
            records.append(self._record(
                agent_id=agent_id,
                suffix=f"visible:{object_index}:{visible_object['id']}",
                source_kind="environment_observation",
                proposition={
                    "predicate": "object_visible",
                    "subject": str(visible_object["id"]),
                    "object": str(visible_object.get("name", "object")),
                    "object_type": visible_object.get("type"),
                },
                grounding={
                    "object_id": visible_object["id"],
                    "object_name": visible_object.get("name"),
                    "object_type": visible_object.get("type"),
                },
            ))
        records.extend(self._held_records(agent_id, observation, "held_objects", "actor_holds"))
        records.extend(self._held_records(agent_id, observation, "oppo_held_objects", "teammate_holds"))
        for goal_name, goal_count in sorted(self._goal_description.items()):
            records.append(self._record(
                agent_id=agent_id,
                suffix=f"goal:{goal_name}",
                source_kind="task_goal",
                proposition={"predicate": "transport_goal", "subject": goal_name, "count": goal_count},
                grounding={"target_name": goal_name, "target_count": goal_count},
                public=True,
            ))
        for sender_index, message in enumerate(observation.get("messages", ())):
            if message is None:
                continue
            records.append(self._record(
                agent_id=agent_id,
                suffix=f"message:{sender_index}",
                source_kind="agent_message",
                proposition={
                    "predicate": "reported_message",
                    "subject": f"agent_{sender_index}",
                    "object": str(message),
                },
                grounding={"message": str(message)},
                public=True,
            ))
        return tuple(records)

    def get_public_observation(self) -> tuple[ObservationRecord, ...]:
        return tuple(
            ObservationRecord(
                observation_id=str(event.source_id),
                benchmark=self.benchmark_name,
                episode_id=self.episode_id,
                step=event.step,
                observer_id="public",
                visibility=Visibility(public=True),
                source_kind=event.event_type,
                proposition=event.payload,
            )
            for event in self._public_events
            if event.source_id is not None
        )

    def get_legal_actions(self, agent_id: str) -> tuple[ActionSpec, ...]:
        observation = self._observations.get(agent_id, {})
        actions = [
            ActionSpec(action_id=f"move_forward:{agent_id}", action_type="move_forward"),
            ActionSpec(action_id=f"turn_left:{agent_id}", action_type="turn_left"),
            ActionSpec(action_id=f"turn_right:{agent_id}", action_type="turn_right"),
        ]
        held = [row for row in observation.get("held_objects", ()) if _held_object(row)]
        free_arms = [arm for arm, row in zip(("left", "right"), observation.get("held_objects", ())) if not _held_object(row)]
        if free_arms:
            for visible_object in observation.get("visible_objects", ()):
                if not isinstance(visible_object, dict) or visible_object.get("id") is None:
                    continue
                if visible_object.get("type") not in {0, 1}:
                    continue
                object_id = visible_object["id"]
                actions.append(ActionSpec(
                    action_id=f"grasp:{agent_id}:{object_id}:{free_arms[0]}",
                    action_type="grasp",
                    parameters={
                        "object_id": object_id,
                        "object_name": visible_object.get("name", "object"),
                        "arm": free_arms[0],
                        "precondition_status": "uncertain",
                        "precondition_reason": "visibility_does_not_guarantee_reachability",
                        "goal_object_match": visible_object.get("name") in self._goal_description,
                    },
                ))
        for arm, held_object in zip(("left", "right"), observation.get("held_objects", ())):
            if not _held_object(held_object):
                continue
            actions.append(ActionSpec(
                action_id=f"drop:{agent_id}:{arm}",
                action_type="drop",
                parameters={"arm": arm, "object_id": held_object.get("id"), "precondition_status": "executable_now"},
            ))
        if any(row.get("type") == 0 for row in held) and any(row.get("type") == 1 for row in held):
            actions.append(ActionSpec(
                action_id=f"put_in:{agent_id}",
                action_type="put_in",
                parameters={"precondition_status": "executable_now"},
            ))
        actions.append(InformationActionSpec(
            action_id=f"send_message:{agent_id}",
            action_type="send_message",
            parameters={"message": ""},
            information_subtype="send_message",
        ))
        return tuple(actions)

    def decision_context(self, agent_id: str) -> DecisionContext:
        observations = (*self.get_observation(agent_id), *self.get_public_observation())
        actions = self.get_legal_actions(agent_id)
        context = DecisionContext(
            benchmark=self.benchmark_name,
            episode_id=self.episode_id,
            step=self.step_index,
            actor_id=agent_id,
            visible_epistemic_nodes=tuple(_observation_node(record) for record in observations),
            visible_candidates=tuple(_candidate_node(agent_id, action) for action in actions),
            legal_actions=actions,
            remaining_budget=BudgetState(
                metadata={"remaining_frames": max(self.config.max_frames - self._current_frames(), 0)}
            ),
            recent_public_events=tuple(self._public_events[-10:]),
        )
        context.validate_agent_facing()
        return context

    def dual_dag_snapshot(self, agent_id: str) -> dict[str, Any]:
        context = self.decision_context(agent_id)
        return {
            "schema_version": 1,
            "benchmark": self.benchmark_name,
            "episode_id": self.episode_id,
            "actor_id": agent_id,
            "epistemic_dag": {"nodes": list(context.visible_epistemic_nodes), "edges": []},
            "action_candidate_dag": {"nodes": list(context.visible_candidates), "edges": []},
        }

    def execute_action(self, agent_id: str, action: ActionSpec) -> StepResult:
        if action.action_type == "send_message":
            return self.execute_information_action(agent_id, InformationActionSpec(
                action_id=action.action_id,
                action_type=action.action_type,
                parameters=action.parameters,
                information_subtype="send_message",
            ))
        self._physical_actions += 1
        result = self._step(agent_id, action, _physical_action_payload(action))
        self._record_physical_feedback(action, succeeded=result.succeeded)
        return result

    def execute_information_action(self, agent_id: str, action: InformationActionSpec) -> StepResult:
        message = str(action.parameters.get("message") or "I have no new target information.")
        self._communication_actions += 1
        if any(goal_name.lower() in message.lower() for goal_name in self._goal_description):
            self._goal_relevant_communications += 1
        result = self._step(agent_id, action, {"type": 6, "message": message})
        if result.succeeded:
            self._pending_information_step = result.step
        self._public_events.append(TraceEvent(
            event_type="public_message_sent",
            step=self.step_index,
            source_id=f"tdw_mat:{self.episode_id}:{self.step_index}:message:{_agent_index(agent_id)}",
            payload={"sender_id": agent_id, "message": message},
        ))
        return result

    def is_terminal(self) -> bool:
        return self._terminal

    def task_progress(self) -> float | None:
        return self._transported / self._target_total if self._target_total else 0.0

    def final_metrics(self) -> dict[str, float | int | bool]:
        attempted = self._physical_actions + self._communication_actions
        return {
            "task_success": bool(self._target_total and self._transported == self._target_total),
            "transport_rate": self.task_progress() or 0.0,
            "transported_objects": self._transported,
            "target_objects": self._target_total,
            "episode_steps": self.step_index,
            "physical_action_count": self._physical_actions,
            "communication_count": self._communication_actions,
            "communication_rate": _ratio(self._communication_actions, attempted),
            "goal_relevant_communication_rate": _ratio(
                self._goal_relevant_communications, self._communication_actions
            ),
            "communication_utility_proxy": _ratio(
                self._goal_relevant_communications, self._communication_actions
            ),
            "communication_utility": _ratio(
                len(self._information_progress_latencies), self._communication_actions
            ),
            "invalid_physical_action_count": self._invalid_physical_actions,
            "false_feasible_action_rate": _ratio(
                self._feasibility_false_positive,
                self._feasibility_true_positive + self._feasibility_false_positive,
            ),
            "false_infeasible_action_rate": _ratio(
                self._feasibility_false_negative,
                self._feasibility_true_negative + self._feasibility_false_negative,
            ),
            "feasibility_prediction_precision": _ratio(
                self._feasibility_true_positive,
                self._feasibility_true_positive + self._feasibility_false_positive,
            ),
            "feasibility_prediction_recall": _ratio(
                self._feasibility_true_positive,
                self._feasibility_true_positive + self._feasibility_false_negative,
            ),
            "feasibility_true_positive": self._feasibility_true_positive,
            "feasibility_false_positive": self._feasibility_false_positive,
            "feasibility_true_negative": self._feasibility_true_negative,
            "feasibility_false_negative": self._feasibility_false_negative,
            "recovery_after_failure_rate": _ratio(
                self._recovered_failures, self._failed_physical_actions
            ),
            "recovered_failure_count": self._recovered_failures,
            "physical_execution_frames": self._physical_frames,
            "mean_physical_action_frames": _ratio(self._physical_frames, self._physical_actions),
            "communication_execution_frames": self._communication_frames,
            "mean_communication_action_frames": _ratio(
                self._communication_frames, self._communication_actions
            ),
            "total_execution_frames": self._physical_frames + self._communication_frames,
            "action_throughput": _ratio(
                attempted, self._physical_frames + self._communication_frames
            ),
            "information_action_to_progress_latency": _mean(
                self._information_progress_latencies
            ),
        }

    def _step(self, agent_id: str, action: ActionSpec, payload: dict[str, Any]) -> StepResult:
        if self.env is None:
            raise RuntimeError("TDWMATAdapter.reset() must be called before execute_action().")
        actions = {str(index): {"type": "ongoing"} for index in range(self.config.agent_count)}
        actions[str(_agent_index(agent_id))] = payload
        previous_transported = self._transported
        observations, _reward, done, info = self.env.step(actions)
        self.step_index += 1
        self._terminal = bool(done)
        self._observations = _normalize_observations(observations, self.agent_ids())
        valid = bool(self._observations.get(agent_id, {}).get("valid", True))
        frames = int(info.get("num_frames_for_step", 0) or 0)
        if action.action_type != "send_message":
            self._physical_frames += frames
            if not valid:
                self._invalid_physical_actions += 1
        else:
            self._communication_frames += frames
        self._refresh_goal()
        if self._transported > previous_transported and self._pending_information_step is not None:
            self._information_progress_latencies.append(
                self.step_index - self._pending_information_step
            )
            self._pending_information_step = None
        return StepResult(
            step=self.step_index,
            succeeded=valid,
            observations=tuple(
                record.observation_id
                for current_agent in self.agent_ids()
                for record in self.get_observation(current_agent)
            ),
            metrics={
                "num_frames_for_step": frames,
                "communication_count": int(action.action_type == "send_message"),
                "transport_rate": self.task_progress() or 0.0,
            },
            error=None if valid else "TDW-MAT marked the action invalid",
        )

    def _record_physical_feedback(self, action: ActionSpec, *, succeeded: bool) -> None:
        predicted_feasible = action.parameters.get("predicted_feasible")
        if predicted_feasible is None:
            predicted_feasible = action.parameters.get("precondition_status") != "known_infeasible"
        if bool(predicted_feasible) and succeeded:
            self._feasibility_true_positive += 1
        elif bool(predicted_feasible):
            self._feasibility_false_positive += 1
        elif succeeded:
            self._feasibility_false_negative += 1
        else:
            self._feasibility_true_negative += 1

        if succeeded and self._pending_failure:
            self._recovered_failures += 1
            self._pending_failure = False
        elif not succeeded:
            self._failed_physical_actions += 1
            self._pending_failure = True

    def _record(
        self,
        *,
        agent_id: str,
        suffix: str,
        source_kind: str,
        proposition: dict[str, Any],
        grounding: dict[str, Any],
        public: bool = False,
    ) -> ObservationRecord:
        return ObservationRecord(
            observation_id=f"tdw_mat:{self.episode_id}:{self.step_index}:{agent_id}:{suffix}",
            benchmark=self.benchmark_name,
            episode_id=self.episode_id,
            step=self.step_index,
            observer_id=agent_id,
            visibility=Visibility(
                public=public,
                visible_to=frozenset() if public else frozenset([agent_id]),
            ),
            source_kind=source_kind,
            proposition=proposition,
            grounding=grounding,
        )

    def _held_records(
        self,
        agent_id: str,
        observation: dict[str, Any],
        field_name: str,
        predicate: str,
    ) -> list[ObservationRecord]:
        return [
            self._record(
                agent_id=agent_id,
                suffix=f"{field_name}:{hand_index}:{held_object['id']}",
                source_kind="environment_observation",
                proposition={
                    "predicate": predicate,
                    "subject": agent_id if predicate == "actor_holds" else "teammate",
                    "object": str(held_object.get("name", held_object["id"])),
                },
                grounding={
                    "object_id": held_object["id"],
                    "object_name": held_object.get("name"),
                    "object_type": held_object.get("type"),
                    "contained": [value for value in held_object.get("contained", ()) if value is not None],
                },
            )
            for hand_index, held_object in enumerate(observation.get(field_name, ()))
            if _held_object(held_object)
        ]

    def _refresh_goal(self) -> None:
        if self.env is None or not hasattr(self.env, "check_goal"):
            return
        transported, total, completed = self.env.check_goal()
        self._transported = int(transported)
        self._target_total = int(total)
        self._terminal = self._terminal or bool(completed)

    def _current_frames(self) -> int:
        return max(
            (int(obs.get("current_frames", 0) or 0) for obs in self._observations.values()),
            default=0,
        )


def _unpack_reset(result: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(result, tuple) or len(result) < 2:
        raise ValueError("TDW-MAT reset must return (observations, info[, env_api])")
    return result[0], result[1]


def _normalize_observations(
    observations: dict[str, Any], agent_ids: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    return {
        agent_id: dict(observations.get(str(index), observations.get(index, {})) or {})
        for index, agent_id in enumerate(agent_ids)
    }


def _observation_node(record: ObservationRecord) -> dict[str, Any]:
    node_type = {
        "agent_message": "reported_claim",
        "public_message_sent": "reported_claim",
        "environment_observation": "observed_fact",
        "task_goal": "resolved_fact",
    }.get(record.source_kind, record.source_kind)
    return {
        "node_id": record.observation_id,
        "node_type": node_type,
        "source_kind": record.source_kind,
        "observer_id": record.observer_id,
        "step": record.step,
        "visibility": {
            "visible_to": sorted(record.visibility.visible_to),
            "public": record.visibility.public,
            "evaluator_only": record.visibility.evaluator_only,
        },
        "proposition": record.proposition,
        "grounding": record.grounding,
        "confidence": record.confidence,
    }


def _candidate_node(agent_id: str, action: ActionSpec) -> dict[str, Any]:
    precondition_status = str(action.parameters.get("precondition_status", "executable_now"))
    if action.action_type == "send_message":
        state = "information_action"
    elif precondition_status == "uncertain":
        state = "uncertain_feasibility"
    else:
        state = precondition_status
    return {
        "node_id": action.action_id,
        "candidate_id": action.action_id,
        "node_type": "action_candidate",
        "actor_id": agent_id,
        "action_type": action.action_type,
        "state": state,
        "currently_legal": True,
        "parameters": dict(action.parameters),
    }


def _physical_action_payload(action: ActionSpec) -> dict[str, Any]:
    action_types = {"move_forward": 0, "turn_left": 1, "turn_right": 2, "grasp": 3, "put_in": 4, "drop": 5}
    if action.action_type not in action_types:
        raise ValueError(f"Unsupported TDW-MAT action type: {action.action_type}")
    payload: dict[str, Any] = {"type": action_types[action.action_type]}
    if action.action_type == "grasp":
        payload.update({"object": action.parameters["object_id"], "arm": action.parameters["arm"]})
    elif action.action_type == "drop":
        payload["arm"] = action.parameters["arm"]
    return payload


def _held_object(value: Any) -> bool:
    return isinstance(value, dict) and value.get("id") is not None


def _agent_index(agent_id: str) -> int:
    return int(agent_id.rsplit("_", 1)[-1])


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _mean(values: list[int]) -> float:
    return sum(values) / len(values) if values else 0.0
