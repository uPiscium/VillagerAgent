from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchmarks.common.actions import ActionSpec
from benchmarks.common.decision import BudgetState, DecisionContext
from benchmarks.common.episode import AgentCapabilities, EpisodeContext
from benchmarks.common.observation import ObservationRecord
from benchmarks.common.visibility import Visibility
from env.minecraft_dual_dag import sanitize_public_value


EVALUATOR_AGENT_FACING_KEYS = {
    "artifact_summary",
    "dual_dag_artifact",
    "evaluator_snapshot",
    "final_score",
    "metrics",
    "progress",
    "score",
    "summary",
    "task_graph_snapshot",
    "timed_out",
}
EVALUATOR_AGENT_FACING_MARKERS = ("score", "progress", "evaluator", "artifact")
DEFAULT_AGENT_NAMES = ("Alice", "Bob", "Cindy", "David", "Eve", "Frank")


class MinecraftBenchmarkMetadataAdapter:
    """Read-only common-protocol metadata adapter for Minecraft artifacts.

    The adapter does not execute the legacy controller or mutate VillagerBench. It
    only exposes sanitized metadata and observations from launch config/action logs.
    """

    benchmark_name = "minecraft"

    def __init__(
        self,
        *,
        launch_config: dict[str, Any],
        action_log: dict[str, Any] | None = None,
        summary: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
        episode_id: str | None = None,
        seed: int = 0,
    ) -> None:
        self.launch_config = sanitize_public_value(launch_config)
        self.action_log = sanitize_public_value(action_log or {})
        self.summary = sanitize_public_value(summary or {})
        self.metrics = sanitize_public_value(metrics or {})
        self.episode_id = episode_id or str(
            self.summary.get("run_name") or self.launch_config.get("task_name") or "minecraft_episode"
        )
        self.seed = int(seed)
        self._agent_ids = _agent_ids(self.launch_config, self.action_log)

    @classmethod
    def from_run_dir(cls, run_dir: str | Path, *, seed: int = 0) -> "MinecraftBenchmarkMetadataAdapter":
        run_path = Path(run_dir)
        return cls(
            launch_config=_read_json(run_path / "launch_config.json", default={}),
            action_log=_read_json(run_path / "action_log.json", default={}),
            summary=_read_json(run_path / "summary.json", default={}),
            metrics=_read_json(run_path / "metrics.json", default={}),
            seed=seed,
        )

    def reset(self, *, episode_id: str, seed: int) -> EpisodeContext:
        self.episode_id = episode_id
        self.seed = int(seed)
        return self.episode_context()

    def episode_context(self) -> EpisodeContext:
        return EpisodeContext(
            benchmark=self.benchmark_name,
            episode_id=self.episode_id,
            seed=self.seed,
            agent_ids=self._agent_ids,
            metadata=_sanitize_agent_facing_value({
                "task_name": self.launch_config.get("task_name", ""),
                "task_type": self.launch_config.get("task_type", ""),
                "task_idx": self.launch_config.get("task_idx"),
                "agent_num": self.launch_config.get("agent_num"),
                "mode": self.summary.get("mode", ""),
            }),
        )

    def agent_ids(self) -> tuple[str, ...]:
        return self._agent_ids

    def capabilities(self, agent_id: str) -> AgentCapabilities:
        action_types = _action_types_for_agent(agent_id, self.action_log)
        return AgentCapabilities(
            agent_id=agent_id,
            can_act=True,
            can_communicate="talkTo" in action_types,
            action_types=tuple(action_types),
            information_action_types=tuple(action for action in action_types if action in {"read", "scanNearbyEntities"}),
        )

    def get_observation(self, agent_id: str) -> tuple[ObservationRecord, ...]:
        observations = []
        for owner_id, entries in self._action_entries():
            for step, entry in entries:
                if not _entry_visible_to_agent(agent_id, owner_id, entry):
                    continue
                observations.append(_observation_record(
                    benchmark=self.benchmark_name,
                    episode_id=self.episode_id,
                    step=step,
                    observer_id=agent_id,
                    owner_id=owner_id,
                    entry=entry,
                ))
        return tuple(observations)

    def get_public_observation(self) -> tuple[ObservationRecord, ...]:
        observations = []
        for owner_id, entries in self._action_entries():
            for step, entry in entries:
                if entry.get("visibility") != "public":
                    continue
                observations.append(_observation_record(
                    benchmark=self.benchmark_name,
                    episode_id=self.episode_id,
                    step=step,
                    observer_id="public",
                    owner_id=owner_id,
                    entry=entry,
                    public=True,
                ))
        return tuple(observations)

    def get_legal_actions(self, agent_id: str) -> tuple[ActionSpec, ...]:
        return tuple(
            ActionSpec(action_id=f"minecraft:{agent_id}:{action_type}", action_type=action_type)
            for action_type in self.capabilities(agent_id).action_types
        )

    def decision_context(self, agent_id: str) -> DecisionContext:
        observations = self.get_observation(agent_id)
        context = DecisionContext(
            benchmark=self.benchmark_name,
            episode_id=self.episode_id,
            step=max((observation.step for observation in observations), default=0),
            actor_id=agent_id,
            visible_epistemic_nodes=tuple(observation.proposition for observation in observations),
            visible_candidates=(_sanitize_agent_facing_value({
                "task_goal": self.launch_config.get("task_goal", ""),
                "task_name": self.launch_config.get("task_name", ""),
                "task_type": self.launch_config.get("task_type", ""),
            }),),
            legal_actions=self.get_legal_actions(agent_id),
            remaining_budget=BudgetState(),
        )
        context.validate_agent_facing()
        return context

    def is_terminal(self) -> bool:
        return bool(self.summary.get("error") or self.metrics)

    def task_progress(self) -> float | None:
        progress = self.metrics.get("progress", self.summary.get("progress"))
        return float(progress) if isinstance(progress, int | float) else None

    def final_metrics(self) -> dict[str, float | int | bool]:
        return {
            key: value
            for key, value in self.metrics.items()
            if isinstance(value, bool | int | float)
        }

    def _action_entries(self) -> list[tuple[str, list[tuple[int, dict[str, Any]]]]]:
        rows = []
        for owner_id, entries in self.action_log.items():
            if not isinstance(entries, list):
                continue
            rows.append((str(owner_id), [(index, entry) for index, entry in enumerate(entries) if isinstance(entry, dict)]))
        return rows


def _agent_ids(launch_config: dict[str, Any], action_log: dict[str, Any]) -> tuple[str, ...]:
    names = list(action_log.keys())
    agent_num = int(launch_config.get("agent_num") or 0)
    for name in DEFAULT_AGENT_NAMES[:agent_num]:
        if name not in names:
            names.append(name)
    return tuple(str(name) for name in names)


def _action_types_for_agent(agent_id: str, action_log: dict[str, Any]) -> tuple[str, ...]:
    entries = action_log.get(agent_id, []) if isinstance(action_log, dict) else []
    action_types = []
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and entry.get("action"):
                action_type = str(entry["action"])
                if action_type not in action_types:
                    action_types.append(action_type)
    return tuple(action_types)


def _entry_visible_to_agent(agent_id: str, owner_id: str, entry: dict[str, Any]) -> bool:
    if owner_id == agent_id:
        return True
    if entry.get("visibility") == "public":
        return True
    if entry.get("action") != "talkTo":
        return False
    kwargs = entry.get("kwargs", {}) if isinstance(entry.get("kwargs"), dict) else {}
    return kwargs.get("entity_name") == agent_id or kwargs.get("player_name") == agent_id


def _observation_record(
    *,
    benchmark: str,
    episode_id: str,
    step: int,
    observer_id: str,
    owner_id: str,
    entry: dict[str, Any],
    public: bool = False,
) -> ObservationRecord:
    proposition = _sanitize_agent_facing_value({
        "agent_id": owner_id,
        "action": entry.get("action", ""),
        "kwargs": entry.get("kwargs", {}),
        "result": entry.get("result", {}),
        "feedback": entry.get("feedback", ""),
        "final_answer": entry.get("final_answer", ""),
    })
    return ObservationRecord(
        observation_id=f"minecraft:observation:{owner_id}:{step}:{observer_id}",
        benchmark=benchmark,
        episode_id=episode_id,
        step=step,
        observer_id=observer_id,
        visibility=Visibility(public=public, visible_to=frozenset() if public else frozenset({observer_id})),
        source_kind="minecraft_action_log",
        proposition=proposition,
        grounding={"owner_id": owner_id, "action_index": step},
    )


def _sanitize_agent_facing_value(value: Any) -> Any:
    return _drop_agent_forbidden_keys(sanitize_public_value(value))


def _drop_agent_forbidden_keys(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.startswith("_") or key_text in EVALUATOR_AGENT_FACING_KEYS:
                continue
            if any(marker in key_text.lower() for marker in EVALUATOR_AGENT_FACING_MARKERS):
                continue
            sanitized[key] = _drop_agent_forbidden_keys(item)
        return sanitized
    if isinstance(value, list):
        return [_drop_agent_forbidden_keys(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_drop_agent_forbidden_keys(item) for item in value)
    return value


def _read_json(path: Path, *, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)
