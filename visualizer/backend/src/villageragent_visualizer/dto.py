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
