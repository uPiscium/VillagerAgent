from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable

from benchmarks.common.actions import ActionSpec, InformationActionSpec, StepResult
from benchmarks.common.decision import BudgetState, DecisionContext, TraceEvent
from benchmarks.common.episode import AgentCapabilities, EpisodeContext
from benchmarks.common.observation import ObservationRecord
from benchmarks.common.visibility import Visibility


INFORMATION_TOOLS = {
    "FindAgentActionTool",
    "FindObjectTool",
    "FindReceptacleTool",
    "FindRoomTool",
}


@dataclass(frozen=True)
class PARTNRConfig:
    instruction: str
    scene_id: str
    agent_ids: tuple[str, ...] = ("agent_0", "agent_1")
    max_steps: int = 200
    metadata: dict[str, Any] = field(default_factory=dict)


class PARTNRAdapter:
    benchmark_name = "partnr"

    def __init__(self, *, config: PARTNRConfig, env_factory: Callable[[PARTNRConfig], Any]):
        self.config = config
        self.env_factory = env_factory
        self.env: Any = None
        self.episode_id = "partnr-fixture"
        self.step_index = 0
        self._observations: dict[str, dict[str, Any]] = {}
        self._tools: dict[str, tuple[str, ...]] = {}
        self._evaluator: dict[str, Any] = {}
        self._public_events: list[TraceEvent] = []
        self._terminal = False
        self._physical_actions = 0
        self._information_actions = 0
        self._failed_actions = 0
        self._recovered_failures = 0
        self._pending_failure = False

    def reset(self, *, episode_id: str, seed: int) -> EpisodeContext:
        self.episode_id = episode_id
        self.step_index = 0
        self._public_events = []
        self._terminal = False
        self._physical_actions = 0
        self._information_actions = 0
        self._failed_actions = 0
        self._recovered_failures = 0
        self._pending_failure = False
        self.env = self.env_factory(self.config)
        observations, info = self.env.reset(seed=seed)
        self._observations = _normalize_observations(observations, self.agent_ids())
        self._tools = {
            agent_id: tuple(str(name) for name in info.get("tools", {}).get(agent_id, ()))
            for agent_id in self.agent_ids()
        }
        self._evaluator = _evaluator_metrics(info)
        return EpisodeContext(
            benchmark=self.benchmark_name,
            episode_id=episode_id,
            seed=seed,
            agent_ids=self.agent_ids(),
            metadata={
                "instruction": self.config.instruction,
                "scene_id": self.config.scene_id,
                **self.config.metadata,
            },
        )

    def agent_ids(self) -> tuple[str, ...]:
        return self.config.agent_ids

    def capabilities(self, agent_id: str) -> AgentCapabilities:
        tools = self._tools.get(agent_id, ())
        return AgentCapabilities(
            agent_id=agent_id,
            can_act=bool(tools),
            can_communicate="FindAgentActionTool" in tools,
            action_types=tuple(name for name in tools if name not in INFORMATION_TOOLS),
            information_action_types=tuple(name for name in tools if name in INFORMATION_TOOLS),
        )

    def get_observation(self, agent_id: str) -> tuple[ObservationRecord, ...]:
        observation = self._observations.get(agent_id, {})
        records = [self._instruction_record(agent_id)]
        for entity in observation.get("entities", ()):
            if not isinstance(entity, dict) or not entity.get("name"):
                continue
            records.append(self._record(
                agent_id=agent_id,
                suffix=f"entity:{entity['name']}",
                source_kind="environment_observation",
                proposition={
                    "predicate": "entity_observed",
                    "subject": str(entity["name"]),
                    "entity_type": str(entity.get("entity_type", "entity")),
                    "relation": entity.get("relation"),
                    "object": entity.get("target"),
                    "states": dict(entity.get("states") or {}),
                },
                grounding={
                    "entity_name": str(entity["name"]),
                    "sim_handle": entity.get("sim_handle"),
                    "translation": entity.get("translation"),
                },
            ))
        for index, feedback in enumerate(observation.get("action_feedback", ())):
            if not isinstance(feedback, dict):
                continue
            records.append(self._record(
                agent_id=agent_id,
                suffix=f"feedback:{index}:{feedback.get('tool', 'unknown')}",
                source_kind="resolved_tool_feedback",
                proposition={
                    "predicate": "tool_result",
                    "tool": feedback.get("tool"),
                    "succeeded": bool(feedback.get("succeeded")),
                    "response": str(feedback.get("response", "")),
                },
                grounding={"arguments": dict(feedback.get("arguments") or {})},
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
        rows = self._observations.get(agent_id, {}).get("action_candidates", ())
        actions: list[ActionSpec] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not row.get("tool"):
                continue
            tool = str(row["tool"])
            parameters = {
                "tool_name": tool,
                "arguments": dict(row.get("arguments") or {}),
                "precondition_status": str(row.get("precondition_status", "uncertain")),
                "precondition_reason": row.get("precondition_reason"),
                "requires": list(row.get("requires") or []),
            }
            action_id = str(row.get("candidate_id") or f"{tool}:{agent_id}:{index}")
            if tool in INFORMATION_TOOLS:
                actions.append(InformationActionSpec(
                    action_id=action_id,
                    action_type=tool,
                    parameters=parameters,
                    information_subtype=tool,
                ))
            else:
                actions.append(ActionSpec(
                    action_id=action_id,
                    action_type=tool,
                    parameters=parameters,
                ))
        return tuple(actions)

    def decision_context(self, agent_id: str) -> DecisionContext:
        records = (*self.get_observation(agent_id), *self.get_public_observation())
        actions = self.get_legal_actions(agent_id)
        context = DecisionContext(
            benchmark=self.benchmark_name,
            episode_id=self.episode_id,
            step=self.step_index,
            actor_id=agent_id,
            visible_epistemic_nodes=tuple(_observation_node(record) for record in records),
            visible_candidates=tuple(_candidate_node(agent_id, action) for action in actions),
            legal_actions=actions,
            remaining_budget=BudgetState(
                metadata={"remaining_steps": max(self.config.max_steps - self.step_index, 0)}
            ),
            recent_public_events=tuple(self._public_events[-10:]),
        )
        context.validate_agent_facing()
        return context

    def dual_dag_snapshot(self, agent_id: str) -> dict[str, Any]:
        context = self.decision_context(agent_id)
        epistemic_ids = {node["node_id"] for node in context.visible_epistemic_nodes}
        candidate_edges = [
            {"source": source, "target": node["node_id"], "relation": "requires"}
            for node in context.visible_candidates
            for requirement in node["parameters"].get("requires", ())
            for source in _resolve_requirement(str(requirement), epistemic_ids)
        ]
        return {
            "schema_version": 1,
            "benchmark": self.benchmark_name,
            "episode_id": self.episode_id,
            "actor_id": agent_id,
            "epistemic_dag": {"nodes": list(context.visible_epistemic_nodes), "edges": []},
            "action_candidate_dag": {
                "nodes": list(context.visible_candidates),
                "edges": candidate_edges,
            },
        }

    def execute_action(self, agent_id: str, action: ActionSpec) -> StepResult:
        if action.action_type in INFORMATION_TOOLS:
            return self.execute_information_action(agent_id, InformationActionSpec(
                action_id=action.action_id,
                action_type=action.action_type,
                parameters=action.parameters,
                information_subtype=action.action_type,
            ))
        self._physical_actions += 1
        return self._step(agent_id, action)

    def execute_information_action(self, agent_id: str, action: InformationActionSpec) -> StepResult:
        self._information_actions += 1
        return self._step(agent_id, action)

    def is_terminal(self) -> bool:
        return self._terminal

    def task_progress(self) -> float | None:
        return float(self._evaluator.get("task_percent_complete", 0.0))

    def evaluator_snapshot(self) -> object:
        return deepcopy(self._evaluator)

    def final_metrics(self) -> dict[str, float | int | bool]:
        return {
            "task_percent_complete": self.task_progress() or 0.0,
            "task_state_success": float(self._evaluator.get("task_state_success", 0.0)),
            "task_success": bool(self._evaluator.get("task_state_success", 0.0)),
            "episode_steps": self.step_index,
            "physical_action_count": self._physical_actions,
            "information_action_count": self._information_actions,
            "failed_action_count": self._failed_actions,
            "recovered_failure_count": self._recovered_failures,
            "recovery_after_failure_rate": _ratio(self._recovered_failures, self._failed_actions),
        }

    def _step(self, agent_id: str, action: ActionSpec) -> StepResult:
        if self.env is None:
            raise RuntimeError("PARTNRAdapter.reset() must be called before action execution.")
        observations, done, info = self.env.step(
            agent_id=agent_id,
            tool_name=str(action.parameters.get("tool_name") or action.action_type),
            arguments=dict(action.parameters.get("arguments") or {}),
        )
        self.step_index += 1
        self._terminal = bool(done)
        self._observations = _normalize_observations(observations, self.agent_ids())
        self._evaluator = _evaluator_metrics(info)
        succeeded = bool(info.get("action_succeeded", False))
        if succeeded and self._pending_failure:
            self._recovered_failures += 1
            self._pending_failure = False
        elif not succeeded:
            self._failed_actions += 1
            self._pending_failure = True
        shared_event = info.get("shared_event")
        if isinstance(shared_event, dict):
            self._public_events.append(TraceEvent(
                event_type="shared_action_event",
                step=self.step_index,
                source_id=f"partnr:{self.episode_id}:{self.step_index}:shared",
                payload=dict(shared_event),
            ))
        return StepResult(
            step=self.step_index,
            succeeded=succeeded,
            observations=tuple(record.observation_id for record in self.get_observation(agent_id)),
            metrics={
                "sim_step_count": int(info.get("sim_step_count", self.step_index)),
                "task_percent_complete": self.task_progress() or 0.0,
                "task_state_success": float(self._evaluator.get("task_state_success", 0.0)),
            },
            error=None if succeeded else str(info.get("response") or "PARTNR tool failed"),
        )

    def _instruction_record(self, agent_id: str) -> ObservationRecord:
        return self._record(
            agent_id=agent_id,
            suffix="instruction",
            source_kind="task_instruction",
            proposition={"predicate": "task_instruction", "text": self.config.instruction},
            grounding={"scene_id": self.config.scene_id},
            public=True,
        )

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
            observation_id=f"partnr:{self.episode_id}:{self.step_index}:{agent_id}:{suffix}",
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


def _normalize_observations(
    observations: dict[str, Any], agent_ids: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    return {
        agent_id: dict(observations.get(agent_id, observations.get(str(index), {})) or {})
        for index, agent_id in enumerate(agent_ids)
    }


def _evaluator_metrics(info: dict[str, Any]) -> dict[str, Any]:
    evaluator = dict(info.get("evaluator") or {})
    return {
        "task_percent_complete": float(evaluator.get("task_percent_complete", 0.0)),
        "task_state_success": float(evaluator.get("task_state_success", 0.0)),
        "task_explanation": evaluator.get("task_explanation"),
        "evaluation_propositions": deepcopy(evaluator.get("evaluation_propositions", [])),
    }


def _observation_node(record: ObservationRecord) -> dict[str, Any]:
    node_type = {
        "environment_observation": "observed_fact",
        "resolved_tool_feedback": "resolved_fact",
        "shared_action_event": "reported_claim",
        "task_instruction": "public_fact",
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
    precondition_status = str(action.parameters.get("precondition_status", "uncertain"))
    return {
        "node_id": action.action_id,
        "candidate_id": action.action_id,
        "node_type": "action_candidate",
        "actor_id": agent_id,
        "action_type": action.action_type,
        "state": "information_action" if action.action_type in INFORMATION_TOOLS else precondition_status,
        "currently_legal": True,
        "parameters": dict(action.parameters),
    }


def _resolve_requirement(requirement: str, epistemic_ids: set[str]) -> tuple[str, ...]:
    if requirement in epistemic_ids:
        return (requirement,)
    matches = sorted(node_id for node_id in epistemic_ids if node_id.endswith(f":{requirement}"))
    return tuple(matches[:1])


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
