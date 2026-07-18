from __future__ import annotations

import os
from pathlib import Path

from villageragent_visualizer.artifacts import ArtifactRepository
from villageragent_visualizer.dto import (
    ArtifactErrorCode,
    JSONScalar,
    JSONValue,
    RunManifest,
    RunSourceMetadata,
    RunState,
    RunTaskMetadata,
    RunWarning,
)


_ARTIFACT_PATHS = {
    "summary": "summary.json",
    "metrics": "metrics.json",
    "action_log": "action_log.json",
    "launch_config": "launch_config.json",
    "task_graph": "task_graph_snapshot.json",
    "runtime_graph": "runtime_dual_dag_snapshot.json",
    "analysis_graph": "dual_dag_artifact.json",
    "decision_support": "decision_support.json",
    "runtime_checkpoint": ".runtime/runtime_result.json",
    "events": "events.jsonl",
}
_CANDIDATE_FILES = {
    "attempt.json",
    "summary.json",
    "metrics.json",
    "action_log.json",
    "launch_config.json",
    "task_graph_snapshot.json",
    "runtime_dual_dag_snapshot.json",
    "dual_dag_artifact.json",
    "decision_support.json",
    "events.jsonl",
}


class RunRepository:
    def __init__(self, root: str | Path, *, artifacts: ArtifactRepository | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.artifacts = artifacts or ArtifactRepository(self.root)

    def list_runs(self) -> tuple[RunManifest, ...]:
        manifests = [
            manifest
            for run_dir in self._candidate_directories()
            if (manifest := self._build_manifest(run_dir)) is not None
        ]
        manifests.sort(key=lambda manifest: manifest.run_id)
        manifests.sort(key=lambda manifest: manifest.started_at or "", reverse=True)
        return tuple(manifests)

    def get_run(self, run_id: str) -> RunManifest | None:
        run_dir = self._resolve_run_id(run_id)
        if run_dir is None or not self._is_candidate(run_dir):
            return None
        return self._build_manifest(run_dir)

    def _candidate_directories(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        candidates: list[Path] = []
        for directory, dirnames, filenames in os.walk(self.root, followlinks=False):
            current = Path(directory)
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name != ".runtime" and not (current / name).is_symlink()
            )
            if self._is_candidate(current, filenames=set(filenames)):
                candidates.append(current)
        return candidates

    def _is_candidate(self, run_dir: Path, *, filenames: set[str] | None = None) -> bool:
        if run_dir == self.root or run_dir.is_symlink():
            return False
        try:
            resolved = run_dir.resolve()
        except (OSError, ValueError):
            return False
        if not resolved.is_relative_to(self.root):
            return False
        names = filenames if filenames is not None else _safe_file_names(run_dir)
        has_checkpoint = _is_regular_file(run_dir / ".runtime" / "runtime_result.json")
        has_direct_artifact = bool(names & _CANDIDATE_FILES)
        if not has_checkpoint and not has_direct_artifact:
            return False
        if "matrix_summary.json" in names and not has_checkpoint and not (names & (_CANDIDATE_FILES - {"attempt.json"})):
            return False
        return True

    def _build_manifest(self, run_dir: Path) -> RunManifest | None:
        try:
            run_id = run_dir.resolve().relative_to(self.root).as_posix()
        except (OSError, ValueError):
            return None

        availability = {
            name: _is_regular_file(run_dir / relative_path)
            for name, relative_path in _ARTIFACT_PATHS.items()
        }
        warnings: list[RunWarning] = []
        invalid = False

        summary, summary_invalid = self._load_mapping(run_id, "summary.json", "summary", warnings)
        attempt, attempt_invalid = self._load_mapping(run_id, "attempt.json", "attempt", warnings)
        artifact_manifest, manifest_invalid = self._load_mapping(
            run_id,
            "artifact_manifest.json",
            "artifact_manifest",
            warnings,
        )
        checkpoint, checkpoint_invalid = self._load_mapping(
            run_id,
            ".runtime/runtime_result.json",
            "runtime_checkpoint",
            warnings,
        )
        launch_config, launch_invalid = self._load_mapping(
            run_id,
            "launch_config.json",
            "launch_config",
            warnings,
        )
        invalid = any((summary_invalid, attempt_invalid, manifest_invalid, checkpoint_invalid, launch_invalid))

        completion_id, completion_invalid = _read_completion_marker(run_dir / "_COMPLETED")
        if completion_invalid:
            invalid = True
            warnings.append(RunWarning(
                code="invalid_completion_marker",
                message="Completion marker could not be read safely.",
                artifact="completion_marker",
            ))

        attempt_id = _text(attempt.get("attempt_id"))
        manifest_attempt_id = _text(artifact_manifest.get("attempt_id"))
        summary_attempt_id = _text(summary.get("attempt_id"))
        identifiers = [value for value in (attempt_id, manifest_attempt_id, summary_attempt_id, completion_id) if value]
        if identifiers and any(value != identifiers[0] for value in identifiers[1:]):
            invalid = True
            warnings.append(RunWarning(
                code="attempt_mismatch",
                message="Run metadata refers to different attempt IDs.",
            ))

        state = _run_state(
            summary=summary,
            attempt=attempt,
            artifact_manifest=artifact_manifest,
            has_completion_marker=completion_id is not None,
            has_checkpoint=availability["runtime_checkpoint"] and not checkpoint_invalid,
            invalid=invalid,
        )
        runtime_snapshot = checkpoint.get("runtime_task_dag_snapshot")
        if not isinstance(runtime_snapshot, dict):
            runtime_snapshot = {}

        task = RunTaskMetadata(
            name=_text(summary.get("task_name") or launch_config.get("task_name")),
            task_type=_text(summary.get("task_type") or launch_config.get("task_type")),
            index=_json_scalar(summary.get("task_idx", launch_config.get("task_idx"))),
        )
        source = RunSourceMetadata(
            producer=_text(attempt.get("producer") or artifact_manifest.get("producer")),
            task_state=_text(summary.get("task_state_source") or runtime_snapshot.get("task_state_source")),
            snapshot=_text(summary.get("snapshot_source") or runtime_snapshot.get("snapshot_source")),
            source_of_truth=_text(summary.get("source_of_truth") or runtime_snapshot.get("source_of_truth")),
        )
        error_value = summary.get("error")
        return RunManifest(
            run_id=run_id,
            name=_text(summary.get("run_name")) or run_dir.name,
            state=state,
            started_at=_optional_text(summary.get("started_at")),
            mode=_text(summary.get("mode")),
            task=task,
            policy=_text(summary.get("runtime_selection_policy") or summary.get("task_selection_policy") or launch_config.get("task_selection_policy")),
            source=source,
            progress=_json_scalar(summary.get("progress")),
            error=_optional_text(error_value) if not isinstance(error_value, (dict, list)) else "Run failed; see artifacts.",
            artifacts=availability,
            warnings=tuple(warnings),
        )

    def _load_mapping(
        self,
        run_id: str,
        relative_path: str,
        artifact_name: str,
        warnings: list[RunWarning],
    ) -> tuple[dict[str, JSONValue], bool]:
        result = self.artifacts.load_json(Path(run_id) / relative_path)
        if result.error is not None:
            if result.error.code is ArtifactErrorCode.MISSING:
                return {}, False
            warnings.append(RunWarning(
                code=result.error.code.value,
                message=result.error.message,
                artifact=artifact_name,
            ))
            return {}, True
        if result.artifact is None or not isinstance(result.artifact.data, dict):
            warnings.append(RunWarning(
                code="invalid_shape",
                message="Artifact must contain a JSON object.",
                artifact=artifact_name,
            ))
            return {}, True
        warnings.extend(
            RunWarning(
                code=warning.code.value,
                message=warning.message,
                artifact=artifact_name,
            )
            for warning in result.artifact.warnings
        )
        return result.artifact.data, False

    def _resolve_run_id(self, run_id: str) -> Path | None:
        candidate = Path(run_id)
        if not run_id or candidate.is_absolute() or ".." in candidate.parts:
            return None
        path = self.root / candidate
        try:
            resolved = path.resolve()
        except (OSError, ValueError):
            return None
        if not resolved.is_relative_to(self.root) or _contains_symlink(self.root, path):
            return None
        return resolved


def _run_state(
    *,
    summary: dict[str, JSONValue],
    attempt: dict[str, JSONValue],
    artifact_manifest: dict[str, JSONValue],
    has_completion_marker: bool,
    has_checkpoint: bool,
    invalid: bool,
) -> RunState:
    if invalid:
        return RunState.INVALID
    summary_status = _text(summary.get("status")).lower()
    runtime = summary.get("runtime")
    runtime_status = _text(runtime.get("status")).lower() if isinstance(runtime, dict) else ""
    error_type = _text(summary.get("error_type")).lower()
    if summary.get("timed_out") is True or summary_status in {"timeout", "timed_out"} or error_type in {"timeout", "timed_out"}:
        return RunState.TIMED_OUT
    attempt_status = _text(attempt.get("status")).lower()
    manifest_status = _text(artifact_manifest.get("status")).lower()
    if summary.get("error") or summary_status == "failed" or runtime_status == "failed" or attempt_status == "failed" or manifest_status == "failed":
        return RunState.FAILED
    if has_completion_marker or attempt_status == "completed" or manifest_status == "completed":
        return RunState.COMPLETED
    if summary:
        return RunState.COMPLETED
    if has_checkpoint:
        return RunState.LIVE
    return RunState.PARTIAL


def _read_completion_marker(path: Path) -> tuple[str | None, bool]:
    if not path.exists() and not path.is_symlink():
        return None, False
    if path.is_symlink() or not path.is_file():
        return None, True
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError, ValueError):
        return None, True
    return (value or None), not bool(value)


def _safe_file_names(directory: Path) -> set[str]:
    try:
        return {path.name for path in directory.iterdir() if path.is_file() or path.is_symlink()}
    except OSError:
        return set()


def _is_regular_file(path: Path) -> bool:
    return not path.is_symlink() and path.is_file()


def _contains_symlink(root: Path, path: Path) -> bool:
    current = path
    while current != root:
        if current.is_symlink():
            return True
        if current.parent == current:
            return True
        current = current.parent
    return False


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    text = _text(value)
    return text or None


def _json_scalar(value: object) -> JSONScalar:
    return value if value is None or isinstance(value, (bool, int, float, str)) else None
