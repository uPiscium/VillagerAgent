from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias


JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


class ArtifactErrorCode(str, Enum):
    MISSING = "missing"
    MALFORMED = "malformed"
    OVERSIZED = "oversized"
    INVALID_ENCODING = "invalid_encoding"
    INVALID_PATH = "invalid_path"
    INVALID_SCHEMA_VERSION = "invalid_schema_version"
    UNSUPPORTED_SCHEMA_MAJOR = "unsupported_schema_major"
    IO_ERROR = "io_error"


class ArtifactWarningCode(str, Enum):
    PRODUCER_VERSIONED = "producer_versioned"
    FUTURE_SCHEMA_VERSION = "future_schema_version"


class RunState(str, Enum):
    LIVE = "live"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    PARTIAL = "partial"
    INVALID = "invalid"


class RuntimeGraphErrorCode(str, Enum):
    RUN_NOT_FOUND = "run_not_found"
    SNAPSHOT_MISSING = "runtime_snapshot_missing"
    SNAPSHOT_INVALID = "runtime_snapshot_invalid"


class AnalysisGraphErrorCode(str, Enum):
    RUN_NOT_FOUND = "run_not_found"
    ARTIFACT_MISSING = "analysis_artifact_missing"
    ARTIFACT_INVALID = "analysis_artifact_invalid"


class TimelineErrorCode(str, Enum):
    RUN_NOT_FOUND = "run_not_found"
    ACTION_LOG_MISSING = "action_log_missing"
    ACTION_LOG_INVALID = "action_log_invalid"


class TimelineTiming(str, Enum):
    EXACT = "exact"
    DURATION_ONLY = "duration_only"
    UNTIMED = "untimed"


class TimelineActionStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ArtifactWarning:
    code: ArtifactWarningCode
    message: str


@dataclass(frozen=True, slots=True)
class ArtifactLoadError:
    code: ArtifactErrorCode
    message: str


@dataclass(frozen=True, slots=True)
class ArtifactDocument:
    data: JSONValue
    schema_version: str | None
    warnings: tuple[ArtifactWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class ArtifactLoadResult:
    artifact: ArtifactDocument | None = None
    error: ArtifactLoadError | None = None

    def __post_init__(self) -> None:
        if (self.artifact is None) == (self.error is None):
            raise ValueError("ArtifactLoadResult must contain exactly one of artifact or error")

    @property
    def ok(self) -> bool:
        return self.artifact is not None


@dataclass(frozen=True, slots=True)
class RunTaskMetadata:
    name: str = ""
    task_type: str = ""
    index: JSONScalar = None


@dataclass(frozen=True, slots=True)
class RunSourceMetadata:
    producer: str = ""
    task_state: str = ""
    snapshot: str = ""
    source_of_truth: str = ""


@dataclass(frozen=True, slots=True)
class RunWarning:
    code: str
    message: str
    artifact: str | None = None


@dataclass(frozen=True, slots=True)
class RunManifest:
    run_id: str
    name: str
    state: RunState
    started_at: str | None
    mode: str
    task: RunTaskMetadata
    policy: str
    source: RunSourceMetadata
    progress: JSONScalar
    error: str | None
    artifacts: dict[str, bool]
    warnings: tuple[RunWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeGraphNode:
    node_id: str
    node_type: str
    content: dict[str, object]
    lifecycle: dict[str, object]
    derived: dict[str, object]
    provenance: dict[str, object]
    extra: dict[str, object]


@dataclass(frozen=True, slots=True)
class RuntimeGraphEdge:
    edge_id: str
    source_id: str
    target_id: str
    edge_type: str
    metadata: dict[str, object]
    extra: dict[str, object]


@dataclass(frozen=True, slots=True)
class RuntimeGraph:
    authority: str
    schema_version: str | None
    snapshot_source: str
    source_of_truth: str
    summary: dict[str, object]
    nodes: tuple[RuntimeGraphNode, ...]
    edges: tuple[RuntimeGraphEdge, ...]
    mutation_history: tuple[dict[str, object], ...]
    warnings: tuple[RunWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeGraphLoadError:
    code: RuntimeGraphErrorCode
    message: str
    warnings: tuple[RunWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeGraphLoadResult:
    graph: RuntimeGraph | None = None
    error: RuntimeGraphLoadError | None = None

    def __post_init__(self) -> None:
        if (self.graph is None) == (self.error is None):
            raise ValueError("RuntimeGraphLoadResult must contain exactly one of graph or error")


@dataclass(frozen=True, slots=True)
class AnalysisGraphFilters:
    node_types: frozenset[str] = frozenset()
    edge_types: frozenset[str] = frozenset()
    agents: frozenset[str] = frozenset()
    task_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class AnalysisGraphNode:
    node_id: str
    node_type: str
    content: dict[str, object]
    provenance: dict[str, object]
    confidence: float | None
    runtime_task_id: str | None
    extra: dict[str, object]


@dataclass(frozen=True, slots=True)
class AnalysisGraphEdge:
    edge_id: str
    source_id: str
    target_id: str
    edge_type: str
    metadata: dict[str, object]
    extra: dict[str, object]


@dataclass(frozen=True, slots=True)
class AnalysisGraph:
    authority: str
    schema_version: str | None
    task_state_source: str
    summary: dict[str, object]
    schema: dict[str, object]
    mapping: dict[str, object]
    nodes: tuple[AnalysisGraphNode, ...]
    edges: tuple[AnalysisGraphEdge, ...]
    applied_filters: dict[str, tuple[str, ...]]
    warnings: tuple[RunWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class AnalysisGraphLoadError:
    code: AnalysisGraphErrorCode
    message: str
    warnings: tuple[RunWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class AnalysisGraphLoadResult:
    graph: AnalysisGraph | None = None
    error: AnalysisGraphLoadError | None = None

    def __post_init__(self) -> None:
        if (self.graph is None) == (self.error is None):
            raise ValueError("AnalysisGraphLoadResult must contain exactly one of graph or error")


@dataclass(frozen=True, slots=True)
class TimelineItem:
    action_id: str
    agent: str
    record_index: int
    tool: str
    status: TimelineActionStatus
    timing: TimelineTiming
    start_time: str | None
    end_time: str | None
    duration_seconds: float | None
    arguments: dict[str, object]
    related_task_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    claim_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TimelineLane:
    agent: str
    items: tuple[TimelineItem, ...]


@dataclass(frozen=True, slots=True)
class TimelineBounds:
    start_time: str
    end_time: str
    timezone_kind: str


@dataclass(frozen=True, slots=True)
class Timeline:
    lanes: tuple[TimelineLane, ...]
    bounds: TimelineBounds | None
    warnings: tuple[RunWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class TimelineLoadError:
    code: TimelineErrorCode
    message: str
    warnings: tuple[RunWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class TimelineLoadResult:
    timeline: Timeline | None = None
    error: TimelineLoadError | None = None

    def __post_init__(self) -> None:
        if (self.timeline is None) == (self.error is None):
            raise ValueError("TimelineLoadResult must contain exactly one of timeline or error")
