from __future__ import annotations

import json
from pathlib import Path

from villageragent_visualizer.dto import (
    ArtifactDocument,
    ArtifactErrorCode,
    ArtifactLoadError,
    ArtifactLoadResult,
    JSONValue,
)
from villageragent_visualizer.sanitization import sanitize_public_value
from villageragent_visualizer.versioning import validate_schema_version


DEFAULT_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024


class ArtifactRepository:
    def __init__(self, root: str | Path, *, max_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.root = Path(root).expanduser().resolve()
        self.max_bytes = max_bytes

    def load_json(
        self,
        relative_path: str | Path,
        *,
        supported_schema_version: str | None = None,
    ) -> ArtifactLoadResult:
        path_or_error = self._resolve_path(relative_path)
        if isinstance(path_or_error, ArtifactLoadError):
            return ArtifactLoadResult(error=path_or_error)
        path = path_or_error

        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return _error(ArtifactErrorCode.MISSING, "Artifact does not exist.")
        except (OSError, ValueError):
            return _error(ArtifactErrorCode.IO_ERROR, "Artifact metadata could not be read.")

        if size > self.max_bytes:
            return _error(
                ArtifactErrorCode.OVERSIZED,
                f"Artifact exceeds the {self.max_bytes}-byte size limit.",
            )

        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return _error(ArtifactErrorCode.MISSING, "Artifact does not exist.")
        except (OSError, ValueError):
            return _error(ArtifactErrorCode.IO_ERROR, "Artifact could not be read.")

        if len(raw) > self.max_bytes:
            return _error(
                ArtifactErrorCode.OVERSIZED,
                f"Artifact exceeds the {self.max_bytes}-byte size limit.",
            )

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return _error(ArtifactErrorCode.INVALID_ENCODING, "Artifact is not valid UTF-8.")

        try:
            data: JSONValue = json.loads(text, parse_constant=_reject_json_constant)
        except (json.JSONDecodeError, RecursionError, ValueError):
            return _error(ArtifactErrorCode.MALFORMED, "Artifact is not valid JSON.")

        schema_version = data.get("schema_version") if isinstance(data, dict) else None
        validation = validate_schema_version(
            schema_version,
            supported_version=supported_schema_version,
        )
        if validation.error is not None:
            return ArtifactLoadResult(error=validation.error)

        try:
            sanitized = sanitize_public_value(data)
        except RecursionError:
            return _error(ArtifactErrorCode.MALFORMED, "Artifact nesting is too deep.")
        return ArtifactLoadResult(artifact=ArtifactDocument(
            data=sanitized,
            schema_version=validation.version,
            warnings=validation.warnings,
        ))

    def _resolve_path(self, relative_path: str | Path) -> Path | ArtifactLoadError:
        candidate = Path(relative_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            return ArtifactLoadError(
                code=ArtifactErrorCode.INVALID_PATH,
                message="Artifact path must remain under the configured result root.",
            )
        if not candidate.parts or any(part.endswith(".tmp") for part in candidate.parts):
            return ArtifactLoadError(
                code=ArtifactErrorCode.INVALID_PATH,
                message="Temporary artifact paths are not readable.",
            )

        path = self.root / candidate
        try:
            resolved = path.resolve()
        except (OSError, ValueError):
            return ArtifactLoadError(
                code=ArtifactErrorCode.IO_ERROR,
                message="Artifact path could not be resolved.",
            )
        if not resolved.is_relative_to(self.root) or _contains_symlink(self.root, path):
            return ArtifactLoadError(
                code=ArtifactErrorCode.INVALID_PATH,
                message="Artifact path must not escape the configured result root or use symlinks.",
            )
        return resolved


def _contains_symlink(root: Path, path: Path) -> bool:
    current = path
    while current != root:
        if current.is_symlink():
            return True
        if current.parent == current:
            return True
        current = current.parent
    return root.is_symlink()


def _error(code: ArtifactErrorCode, message: str) -> ArtifactLoadResult:
    return ArtifactLoadResult(error=ArtifactLoadError(code=code, message=message))


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON constant: {value}")
