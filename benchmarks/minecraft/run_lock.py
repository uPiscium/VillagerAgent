from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import time
from pathlib import Path

class MinecraftTargetLockError(RuntimeError):
    pass


def minecraft_target_lock_key(*, host: str, port: int) -> str:
    identity = f"{host.casefold()}:{int(port)}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class MinecraftTargetLock:
    def __init__(
        self,
        *,
        lock_root: str | Path,
        host: str,
        port: int,
        world_id: str,
        attempt_id: str,
        timeout_seconds: float = 0.0,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise ValueError("timeout_seconds must be finite and non-negative")
        self.lock_root = Path(lock_root)
        self.key = minecraft_target_lock_key(host=host, port=port)
        self.path = self.lock_root / f"{self.key}.lock"
        self.host = host
        self.port = int(port)
        self.world_id = world_id
        self.attempt_id = attempt_id
        self.timeout_seconds = float(timeout_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.acquired = False
        self.stale_owner_detected = False
        self._stream = None

    def acquire(self) -> "MinecraftTargetLock":
        self.lock_root.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("a+", encoding="utf-8")
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    owner = self._read_metadata()
                    self._stream.close()
                    self._stream = None
                    raise MinecraftTargetLockError(
                        f"Minecraft target {self.host}:{self.port} is locked by "
                        f"attempt {owner.get('attempt_id', 'unknown')}"
                    ) from exc
                time.sleep(self.poll_interval_seconds)

        previous = self._read_metadata()
        previous_pid = previous.get("pid")
        self.stale_owner_detected = (
            isinstance(previous_pid, int)
            and previous_pid != os.getpid()
            and not _pid_exists(previous_pid)
        )
        self._write_metadata({
            "schema_version": 1,
            "status": "acquired",
            "attempt_id": self.attempt_id,
            "pid": os.getpid(),
            "host": self.host,
            "port": self.port,
            "world_id": self.world_id,
            "lock_key": self.key,
            "acquired_at": time.time(),
            "stale_owner_detected": self.stale_owner_detected,
        })
        self.acquired = True
        return self

    def release(self) -> None:
        if self._stream is None:
            return
        if self.acquired:
            metadata = self._read_metadata()
            metadata.update({"status": "released", "released_at": time.time()})
            self._write_metadata(metadata)
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        self._stream.close()
        self._stream = None
        self.acquired = False

    def _read_metadata(self) -> dict:
        if self._stream is None:
            return {}
        self._stream.seek(0)
        content = self._stream.read()
        if not content.strip():
            return {}
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_metadata(self, payload: dict) -> None:
        if self._stream is None:
            raise RuntimeError("lock stream is not open")
        self._stream.seek(0)
        self._stream.truncate()
        json.dump(payload, self._stream, indent=2)
        self._stream.write("\n")
        self._stream.flush()
        os.fsync(self._stream.fileno())

    def __enter__(self) -> "MinecraftTargetLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
