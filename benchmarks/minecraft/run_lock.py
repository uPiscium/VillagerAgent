from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import time
from pathlib import Path


LOCK_METADATA_SCHEMA_VERSION = 2
LOCK_METADATA_STATUSES = frozenset({"acquired", "released", "quarantined", "cleared"})


class MinecraftTargetLockError(RuntimeError):
    pass


class MinecraftTargetQuarantinedError(MinecraftTargetLockError):
    def __init__(self, message: str, *, quarantine: dict):
        super().__init__(message)
        self.quarantine = quarantine


class MinecraftTargetLockMetadataError(MinecraftTargetLockError):
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
        self.quarantined = False
        self.quarantine_record = None
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

        try:
            previous = self._read_metadata()
            if previous.get("status") == "quarantined":
                raise MinecraftTargetQuarantinedError(
                    f"Minecraft target {self.host}:{self.port} is quarantined",
                    quarantine=previous,
                )
            previous_pid = previous.get("pid")
            self.stale_owner_detected = (
                previous.get("status") == "acquired"
                and isinstance(previous_pid, int)
                and previous_pid != os.getpid()
                and not _pid_exists(previous_pid)
            )
            acquired_metadata = {
                "schema_version": LOCK_METADATA_SCHEMA_VERSION,
                "status": "acquired",
                "attempt_id": self.attempt_id,
                "pid": os.getpid(),
                "host": self.host,
                "port": self.port,
                "world_id": self.world_id,
                "lock_key": self.key,
                "acquired_at": time.time(),
                "stale_owner_detected": self.stale_owner_detected,
            }
            if previous.get("schema_version") == 1:
                acquired_metadata.update({
                    "migrated_from_schema_version": 1,
                    "previous_status": previous["status"],
                })
            self._write_metadata(acquired_metadata)
        except BaseException:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
            self._stream.close()
            self._stream = None
            raise
        self.acquired = True
        return self

    def quarantine(
        self,
        *,
        run_name: str,
        reasons: tuple[str, ...] | list[str],
        diagnostics: dict,
    ) -> dict:
        if not self.acquired or self._stream is None:
            raise MinecraftTargetLockError("Minecraft target must be acquired before quarantine")
        normalized_reasons = tuple(dict.fromkeys(
            reason for reason in reasons if isinstance(reason, str) and reason
        ))
        if not normalized_reasons:
            raise ValueError("quarantine reasons must contain at least one non-empty string")
        if not isinstance(diagnostics, dict):
            raise ValueError("quarantine diagnostics must be an object")
        acquired = self._read_metadata()
        if acquired.get("status") != "acquired" or acquired.get("attempt_id") != self.attempt_id:
            raise MinecraftTargetLockMetadataError(
                "Minecraft target acquired metadata does not match the current owner"
            )
        record = {
            **acquired,
            "status": "quarantined",
            "run_name": run_name,
            "quarantined_at": time.time(),
            "reasons": list(normalized_reasons),
            "diagnostics": diagnostics,
        }
        self._write_metadata(record)
        self.quarantined = True
        self.quarantine_record = record
        return dict(record)

    def release(self) -> None:
        if self._stream is None:
            return
        if self.acquired:
            if not self.quarantined:
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
        except json.JSONDecodeError as exc:
            raise MinecraftTargetLockMetadataError(
                f"Minecraft target lock metadata is invalid JSON: {exc}"
            ) from exc
        return _parse_lock_metadata(
            payload,
            expected_key=self.key,
            expected_host=self.host,
            expected_port=self.port,
        )

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


def read_minecraft_target_lock_metadata(
    *,
    lock_root: str | Path,
    host: str,
    port: int,
) -> dict:
    key = minecraft_target_lock_key(host=host, port=port)
    path = Path(lock_root) / f"{key}.lock"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as stream:
        content = stream.read()
    if not content.strip():
        return {}
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise MinecraftTargetLockMetadataError(
            f"Minecraft target lock metadata is invalid JSON: {exc}"
        ) from exc
    return _parse_lock_metadata(
        payload,
        expected_key=key,
        expected_host=host,
        expected_port=int(port),
    )


def clear_minecraft_target_quarantine(
    *,
    lock_root: str | Path,
    host: str,
    port: int,
    reason: str,
    acknowledge_target_safe: bool,
    force_corrupt: bool = False,
) -> dict:
    if not acknowledge_target_safe:
        raise ValueError("clearing quarantine requires acknowledge_target_safe")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("clearing quarantine requires a non-empty reason")
    key = minecraft_target_lock_key(host=host, port=port)
    root = Path(lock_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{key}.lock"
    stream = path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MinecraftTargetLockError(
                f"Minecraft target {host}:{int(port)} is actively locked"
            ) from exc
        stream.seek(0)
        content = stream.read()
        previous = {}
        if content.strip():
            try:
                previous = _parse_lock_metadata(
                    json.loads(content),
                    expected_key=key,
                    expected_host=host,
                    expected_port=int(port),
                )
            except (json.JSONDecodeError, MinecraftTargetLockMetadataError) as exc:
                if not force_corrupt:
                    raise MinecraftTargetLockMetadataError(
                        "corrupt Minecraft target metadata requires force_corrupt"
                    ) from exc
        if previous and previous.get("status") != "quarantined" and not force_corrupt:
            raise MinecraftTargetLockError("Minecraft target is not quarantined")
        last_quarantine = _public_quarantine_history(previous)
        cleared = {
            "schema_version": LOCK_METADATA_SCHEMA_VERSION,
            "status": "cleared",
            "lock_key": key,
            "host": host,
            "port": int(port),
            "cleared_at": time.time(),
            "cleared_by_pid": os.getpid(),
            "clear_reason": reason.strip(),
            "last_quarantine": last_quarantine,
        }
        _write_stream_metadata(stream, cleared)
        return cleared
    finally:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def _parse_lock_metadata(
    payload: object,
    *,
    expected_key: str,
    expected_host: str,
    expected_port: int,
) -> dict:
    if not isinstance(payload, dict):
        raise MinecraftTargetLockMetadataError("Minecraft target lock metadata must be an object")
    schema_version = payload.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version not in {1, LOCK_METADATA_SCHEMA_VERSION}
    ):
        raise MinecraftTargetLockMetadataError("Minecraft target lock metadata schema is unsupported")
    _validate_metadata_identity(
        payload,
        expected_key=expected_key,
        expected_host=expected_host,
        expected_port=expected_port,
    )
    if schema_version == 1:
        return _validate_schema_v1_metadata(payload)
    return _validate_schema_v2_metadata(payload)


def _validate_metadata_identity(
    payload: dict,
    *,
    expected_key: str,
    expected_host: str,
    expected_port: int,
) -> None:
    if (
        payload.get("lock_key") != expected_key
        or not isinstance(payload.get("host"), str)
        or payload["host"].casefold() != expected_host.casefold()
        or payload.get("port") != int(expected_port)
    ):
        raise MinecraftTargetLockMetadataError("Minecraft target lock metadata identity mismatch")


def _validate_schema_v1_metadata(payload: dict) -> dict:
    if payload.get("status") not in {"acquired", "released"}:
        raise MinecraftTargetLockMetadataError("Minecraft target lock metadata status is invalid")
    attempt_id = payload.get("attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id:
        raise MinecraftTargetLockMetadataError("Minecraft target legacy metadata is invalid")
    if payload["status"] == "acquired":
        pid = payload.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise MinecraftTargetLockMetadataError("Minecraft target legacy metadata is invalid")
    return dict(payload)


def _validate_schema_v2_metadata(payload: dict) -> dict:
    if payload.get("status") not in LOCK_METADATA_STATUSES:
        raise MinecraftTargetLockMetadataError("Minecraft target lock metadata status is invalid")
    if payload["status"] == "quarantined":
        if (
            not isinstance(payload.get("attempt_id"), str)
            or not isinstance(payload.get("run_name"), str)
            or not isinstance(payload.get("reasons"), list)
            or not payload["reasons"]
            or not all(isinstance(reason, str) and reason for reason in payload["reasons"])
            or not isinstance(payload.get("diagnostics"), dict)
        ):
            raise MinecraftTargetLockMetadataError("Minecraft target quarantine metadata is invalid")
    return dict(payload)


def _write_stream_metadata(stream, payload: dict) -> None:
    stream.seek(0)
    stream.truncate()
    json.dump(payload, stream, indent=2)
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())


def _public_quarantine_history(metadata: dict) -> dict:
    if metadata.get("status") != "quarantined":
        return {}
    return {
        "attempt_id": metadata.get("attempt_id"),
        "run_name": metadata.get("run_name"),
        "quarantined_at": metadata.get("quarantined_at"),
        "reasons": list(metadata.get("reasons", [])),
    }
