from typing import Protocol

from benchmarks.common.actions import ActionSpec, InformationActionSpec, StepResult
from benchmarks.common.decision import DecisionContext
from benchmarks.common.episode import AgentCapabilities, EpisodeContext
from benchmarks.common.observation import ObservationRecord


class BenchmarkAdapter(Protocol):
    benchmark_name: str

    def reset(self, *, episode_id: str, seed: int) -> EpisodeContext:
        ...

    def agent_ids(self) -> tuple[str, ...]:
        ...

    def capabilities(self, agent_id: str) -> AgentCapabilities:
        ...

    def get_observation(self, agent_id: str) -> tuple[ObservationRecord, ...]:
        ...

    def get_public_observation(self) -> tuple[ObservationRecord, ...]:
        ...

    def get_legal_actions(self, agent_id: str) -> tuple[ActionSpec, ...]:
        ...

    def decision_context(self, agent_id: str) -> DecisionContext:
        ...

    def execute_action(self, agent_id: str, action: ActionSpec) -> StepResult:
        ...

    def execute_information_action(self, agent_id: str, action: InformationActionSpec) -> StepResult:
        ...

    def is_terminal(self) -> bool:
        ...

    def task_progress(self) -> float | None:
        ...

    def final_metrics(self) -> dict[str, float | int | bool]:
        ...


class BenchmarkEvaluatorAccess(Protocol):
    def evaluator_snapshot(self) -> object:
        ...
