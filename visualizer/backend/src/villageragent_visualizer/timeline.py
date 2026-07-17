from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path

from villageragent_visualizer.analysis_graph import AnalysisGraphService
from villageragent_visualizer.artifacts import ArtifactRepository
from villageragent_visualizer.dto import (
    ArtifactErrorCode,
    JSONValue,
    RunWarning,
    Timeline,
    TimelineActionStatus,
    TimelineBounds,
    TimelineErrorCode,
    TimelineItem,
    TimelineLane,
    TimelineLoadError,
    TimelineLoadResult,
    TimelineTiming,
)
from villageragent_visualizer.runs import RunRepository


class TimelineService:
    def __init__(
        self,
        *,
        artifacts: ArtifactRepository,
        runs: RunRepository,
        analysis_graphs: AnalysisGraphService,
    ) -> None:
        self.artifacts = artifacts
        self.runs = runs
        self.analysis_graphs = analysis_graphs

    def load(self, run_id: str) -> TimelineLoadResult:
        if self.runs.get_run(run_id) is None:
            return _error(TimelineErrorCode.RUN_NOT_FOUND, "Run not found.")

        result = self.artifacts.load_json(Path(run_id) / "action_log.json")
        if result.error is not None:
            code = (
                TimelineErrorCode.ACTION_LOG_MISSING
                if result.error.code is ArtifactErrorCode.MISSING
                else TimelineErrorCode.ACTION_LOG_INVALID
            )
            return _error(code, "Action log is unavailable.", [RunWarning(
                code=result.error.code.value,
                message=result.error.message,
                artifact="action_log",
            )])
        if result.artifact is None or not isinstance(result.artifact.data, dict):
            return _error(TimelineErrorCode.ACTION_LOG_INVALID, "Action log must contain a JSON object.")

        warnings = [
            RunWarning(
                code=warning.code.value,
                message=warning.message,
                artifact="action_log",
            )
            for warning in result.artifact.warnings
        ]
        relations = _analysis_relations(run_id, self.analysis_graphs, warnings)
        lanes: list[TimelineLane] = []
        exact_items: list[tuple[TimelineItem, datetime, datetime]] = []
        for agent, records in result.artifact.data.items():
            if not isinstance(records, list):
                warnings.append(RunWarning(
                    code="invalid_agent_records",
                    message=f"Action records for agent {agent!r} are not an array and were skipped.",
                    artifact="action_log",
                ))
                continue
            items: list[TimelineItem] = []
            for index, record in enumerate(records):
                if not isinstance(record, dict):
                    warnings.append(RunWarning(
                        code="invalid_action_record",
                        message=f"Action record {index} for agent {agent!r} is not an object and was skipped.",
                        artifact="action_log",
                    ))
                    continue
                item, parsed = _timeline_item(agent, index, record, relations, warnings)
                items.append(item)
                if parsed is not None:
                    exact_items.append((item, parsed[0], parsed[1]))
            lanes.append(TimelineLane(agent=agent, items=tuple(items)))

        bounds = _timeline_bounds(exact_items, warnings)
        return TimelineLoadResult(timeline=Timeline(
            lanes=tuple(lanes),
            bounds=bounds,
            warnings=tuple(warnings),
        ))


def _timeline_item(
    agent: str,
    index: int,
    record: dict[str, JSONValue],
    relations: dict[str, dict[str, tuple[str, ...]]],
    warnings: list[RunWarning],
) -> tuple[TimelineItem, tuple[datetime, datetime] | None]:
    action_id = f"minecraft:action:{agent}:{index}"
    start_raw = record.get("start_time")
    end_raw = record.get("end_time")
    start_text = start_raw if isinstance(start_raw, str) and start_raw.strip() else None
    end_text = end_raw if isinstance(end_raw, str) and end_raw.strip() else None
    start = _parse_timestamp(start_text)
    end = _parse_timestamp(end_text)
    duration = _duration(record.get("duration"))
    raw_duration = record.get("duration")
    if raw_duration is not None and duration is None:
        warnings.append(RunWarning(
            code="invalid_duration",
            message=f"Action {action_id} has an invalid or negative duration.",
            artifact="action_log",
        ))

    parsed: tuple[datetime, datetime] | None = None
    if start is not None and end is not None:
        timing = TimelineTiming.EXACT
        parsed = (start, end)
        if _comparable(start, end) and end < start:
            warnings.append(RunWarning(
                code="negative_time_range",
                message=f"Action {action_id} ends before it starts; raw timestamps were retained.",
                artifact="action_log",
            ))
        elif duration is None and _comparable(start, end):
            duration = (end - start).total_seconds()
    elif duration is not None:
        timing = TimelineTiming.DURATION_ONLY
        if start_text is not None or end_text is not None:
            warnings.append(RunWarning(
                code="incomplete_timestamp",
                message=f"Action {action_id} has incomplete or malformed timestamps and uses duration only.",
                artifact="action_log",
            ))
    else:
        timing = TimelineTiming.UNTIMED
        if start_raw is not None or end_raw is not None:
            warnings.append(RunWarning(
                code="invalid_timestamp",
                message=f"Action {action_id} has incomplete or malformed timestamps and remains untimed.",
                artifact="action_log",
            ))

    relation = relations.get(action_id, {})
    result = record.get("result")
    result_status = result.get("status") if isinstance(result, dict) else None
    status = (
        TimelineActionStatus.SUCCESS
        if result_status is True
        else TimelineActionStatus.FAILURE
        if result_status is False
        else TimelineActionStatus.UNKNOWN
    )
    arguments = record.get("kwargs")
    return TimelineItem(
        action_id=action_id,
        agent=agent,
        record_index=index,
        tool=_text(record.get("action")) or "unknown",
        status=status,
        timing=timing,
        start_time=start_text if timing is TimelineTiming.EXACT else None,
        end_time=end_text if timing is TimelineTiming.EXACT else None,
        duration_seconds=duration,
        arguments=dict(arguments) if isinstance(arguments, dict) else {},
        related_task_ids=relation.get("tasks", ()),
        observation_ids=relation.get("observations", ()),
        claim_ids=relation.get("claims", ()),
    ), parsed


def _analysis_relations(
    run_id: str,
    analysis_graphs: AnalysisGraphService,
    warnings: list[RunWarning],
) -> dict[str, dict[str, tuple[str, ...]]]:
    result = analysis_graphs.load(run_id)
    if result.graph is None:
        warnings.append(RunWarning(
            code="analysis_relations_unavailable",
            message="Analysis graph is unavailable; related entity IDs could not be resolved.",
            artifact="analysis_graph",
        ))
        return {}
    relations: dict[str, dict[str, list[str]]] = {}
    for edge in result.graph.edges:
        if edge.edge_type == "task_invokes_action":
            relations.setdefault(edge.target_id, {}).setdefault("tasks", []).append(edge.source_id)
        elif edge.edge_type == "produces_observation":
            relations.setdefault(edge.source_id, {}).setdefault("observations", []).append(edge.target_id)
        elif edge.edge_type == "reports_claim":
            relations.setdefault(edge.source_id, {}).setdefault("claims", []).append(edge.target_id)
    return {
        action_id: {
            kind: tuple(dict.fromkeys(entity_ids))
            for kind, entity_ids in related.items()
        }
        for action_id, related in relations.items()
    }


def _timeline_bounds(
    exact_items: list[tuple[TimelineItem, datetime, datetime]],
    warnings: list[RunWarning],
) -> TimelineBounds | None:
    if not exact_items:
        return None
    awareness = {_is_aware(start) for _, start, _ in exact_items} | {_is_aware(end) for _, _, end in exact_items}
    if len(awareness) != 1:
        warnings.append(RunWarning(
            code="mixed_timezone_bounds",
            message="Exact timestamps mix offset-aware and timezone-naive values; global bounds were omitted.",
            artifact="action_log",
        ))
        return None
    valid_items = [
        item
        for item in exact_items
        if not _comparable(item[1], item[2]) or item[2] >= item[1]
    ]
    if not valid_items:
        return None
    first = min(valid_items, key=lambda value: value[1])
    last = max(valid_items, key=lambda value: value[2])
    return TimelineBounds(
        start_time=first[0].start_time or "",
        end_time=last[0].end_time or "",
        timezone_kind="offset_aware" if next(iter(awareness)) else "naive_local",
    )


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    duration = float(value)
    return duration if math.isfinite(duration) and duration >= 0 else None


def _is_aware(value: datetime) -> bool:
    return value.utcoffset() is not None


def _comparable(start: datetime, end: datetime) -> bool:
    return _is_aware(start) == _is_aware(end)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _error(
    code: TimelineErrorCode,
    message: str,
    warnings: list[RunWarning] | None = None,
) -> TimelineLoadResult:
    return TimelineLoadResult(error=TimelineLoadError(
        code=code,
        message=message,
        warnings=tuple(warnings or ()),
    ))
