from __future__ import annotations

import threading
from pathlib import Path

try:
    from env.runtime_paths import RuntimePaths, atomic_write_json
except ImportError:
    from runtime_paths import RuntimePaths, atomic_write_json


class ScoreOwnershipError(ValueError):
    pass


def validate_score_identity(
    score: dict,
    *,
    expected_attempt_id: str,
    expected_task_name: str,
) -> None:
    if not isinstance(score, dict):
        raise ScoreOwnershipError("score payload must be an object")
    attempt_id = score.get("attempt_id")
    task_name = score.get("task_name")
    if not isinstance(attempt_id, str) or not attempt_id:
        raise ScoreOwnershipError("score ownership identity is missing: attempt_id")
    if not isinstance(task_name, str) or not task_name:
        raise ScoreOwnershipError("score ownership identity is missing: task_name")
    if attempt_id != expected_attempt_id:
        raise ScoreOwnershipError(
            f"score attempt mismatch: expected {expected_attempt_id!r}, got {attempt_id!r}"
        )
    if task_name != expected_task_name:
        raise ScoreOwnershipError(
            f"score task mismatch: expected {expected_task_name!r}, got {task_name!r}"
        )
    if score.get("status") not in ("success", "failure"):
        raise ScoreOwnershipError("score status must be success or failure")


class TerminalArtifactWriter:
    def __init__(self, runtime_paths: RuntimePaths, run_result_dir: Path):
        self.runtime_paths = runtime_paths
        self.run_result_dir = Path(run_result_dir)
        self.status = None
        self._lock = threading.Lock()

    def write(self, payload: dict, config: dict) -> bool:
        with self._lock:
            if self.status is not None:
                return False
            validate_score_identity(
                payload,
                expected_attempt_id=config.get("attempt_id"),
                expected_task_name=config.get("task_name"),
            )
            atomic_write_json(self.run_result_dir / "score.json", payload)
            atomic_write_json(self.runtime_paths.score, payload)
            atomic_write_json(self.run_result_dir / "config.json", config)
            atomic_write_json(self.runtime_paths.load_status, {"status": "end"})
            self.status = payload.get("status")
            return True
