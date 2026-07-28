from __future__ import annotations

import threading
from pathlib import Path

try:
    from env.runtime_paths import RuntimePaths, atomic_write_json
except ImportError:
    from runtime_paths import RuntimePaths, atomic_write_json


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
            atomic_write_json(self.run_result_dir / "score.json", payload)
            atomic_write_json(self.runtime_paths.score, payload)
            atomic_write_json(self.run_result_dir / "config.json", config)
            atomic_write_json(self.runtime_paths.load_status, {"status": "end"})
            self.status = payload.get("status")
            return True
