import json
import multiprocessing
import os
import threading
import time

import pytest

from benchmarks.minecraft.run_lock import (
    MinecraftTargetLock,
    MinecraftTargetLockBusyError,
    MinecraftTargetLockError,
    MinecraftTargetLockMetadataError,
    MinecraftTargetLockUnavailableError,
    MinecraftTargetQuarantinedError,
    clear_minecraft_target_quarantine,
    minecraft_target_lock_key,
    read_minecraft_target_lock_metadata,
)


def _lock(tmp_path, attempt_id, *, port=25565):
    return MinecraftTargetLock(
        lock_root=tmp_path / "locks",
        host="127.0.0.1",
        port=port,
        world_id="world-a",
        attempt_id=attempt_id,
    )


def test_same_minecraft_target_rejects_second_owner(tmp_path):
    first = _lock(tmp_path, "attempt-owner").acquire()
    contender = _lock(tmp_path, "attempt-contender")
    try:
        with pytest.raises(MinecraftTargetLockBusyError, match="attempt attempt-owner") as raised:
            contender.acquire()
        assert raised.value.reason == "busy"
        assert raised.value.owner == {
            "status": "acquired",
            "attempt_id": "attempt-owner",
        }
        assert contender.acquired is False
        assert contender._stream is None
    finally:
        first.release()


@pytest.mark.parametrize("content", ["", "{", "[]", '{"status": "acquired"}'])
def test_contention_owner_metadata_is_best_effort(tmp_path, content):
    owner = _lock(tmp_path, "attempt-owner").acquire()
    original_content = owner.path.read_text(encoding="utf-8")
    owner.path.write_text(content, encoding="utf-8")
    contender = _lock(tmp_path, "attempt-contender")
    try:
        with pytest.raises(MinecraftTargetLockBusyError):
            contender.acquire()
        assert contender._stream is None
        assert owner.path.read_text(encoding="utf-8") == content
    finally:
        owner.path.write_text(original_content, encoding="utf-8")
        owner.release()


def test_contender_acquires_after_owner_releases_within_timeout(tmp_path):
    owner = _lock(tmp_path, "attempt-owner").acquire()
    contender = MinecraftTargetLock(
        lock_root=tmp_path / "locks",
        host="127.0.0.1",
        port=25565,
        world_id="world-a",
        attempt_id="attempt-contender",
        timeout_seconds=1,
        poll_interval_seconds=0.01,
    )

    def release_owner():
        time.sleep(0.05)
        owner.release()

    release_thread = threading.Thread(target=release_owner)
    release_thread.start()
    try:
        contender.acquire()
        assert contender.acquired is True
    finally:
        contender.release()
        release_thread.join(timeout=1)


def test_non_contention_flock_error_is_unavailable(tmp_path, monkeypatch):
    lock = _lock(tmp_path, "attempt-a")
    monkeypatch.setattr(
        "benchmarks.minecraft.run_lock.fcntl.flock",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("flock failed")),
    )

    with pytest.raises(MinecraftTargetLockUnavailableError) as raised:
        lock.acquire()

    assert raised.value.reason == "io_error"
    assert str(lock.path) not in str(raised.value)
    assert lock.acquired is False
    assert lock._stream is None


def test_different_minecraft_targets_can_be_locked(tmp_path):
    first = _lock(tmp_path, "attempt-a", port=25565).acquire()
    second = _lock(tmp_path, "attempt-b", port=25566).acquire()
    try:
        assert first.acquired is True
        assert second.acquired is True
    finally:
        second.release()
        first.release()


def test_lock_is_released_after_context_failure(tmp_path):
    with pytest.raises(RuntimeError, match="child failed"):
        with _lock(tmp_path, "attempt-a"):
            raise RuntimeError("child failed")

    with _lock(tmp_path, "attempt-b") as replacement:
        assert replacement.acquired is True


def test_unlocked_dead_owner_metadata_is_detected_as_stale(tmp_path):
    lock = _lock(tmp_path, "attempt-a")
    lock.path.parent.mkdir(parents=True)
    lock.path.write_text(json.dumps({
        "schema_version": 2,
        "status": "acquired",
        "lock_key": lock.key,
        "host": "127.0.0.1",
        "port": 25565,
        "world_id": "world-a",
        "pid": 99999999,
        "attempt_id": "dead",
        "acquired_at": 1.0,
        "stale_owner_detected": False,
    }), encoding="utf-8")

    with lock:
        assert lock.stale_owner_detected is True
        metadata = json.loads(lock.path.read_text(encoding="utf-8"))
        assert metadata["pid"] == os.getpid()


def test_schema_v1_released_metadata_migrates_on_acquire(tmp_path):
    lock = _lock(tmp_path, "attempt-new")
    _write_schema_v1_metadata(lock, status="released", attempt_id="attempt-old")

    with lock:
        metadata = json.loads(lock.path.read_text(encoding="utf-8"))
        assert metadata["schema_version"] == 2
        assert metadata["status"] == "acquired"
        assert metadata["attempt_id"] == "attempt-new"
        assert metadata["lock_key"] == lock.key
        assert metadata["host"] == "127.0.0.1"
        assert metadata["port"] == 25565
        assert metadata["migrated_from_schema_version"] == 1
        assert metadata["previous_status"] == "released"


def test_schema_v1_dead_acquired_owner_migrates_as_stale(tmp_path):
    lock = _lock(tmp_path, "attempt-new")
    _write_schema_v1_metadata(
        lock,
        status="acquired",
        attempt_id="attempt-dead",
        pid=99999999,
    )

    with lock:
        metadata = json.loads(lock.path.read_text(encoding="utf-8"))
        assert lock.stale_owner_detected is True
        assert metadata["schema_version"] == 2
        assert metadata["stale_owner_detected"] is True
        assert metadata["previous_status"] == "acquired"


def test_schema_v1_identity_mismatch_fails_closed(tmp_path):
    lock = _lock(tmp_path, "attempt-new")
    _write_schema_v1_metadata(
        lock,
        status="released",
        attempt_id="attempt-old",
        host="other-host",
    )

    with pytest.raises(MinecraftTargetLockMetadataError, match="identity mismatch"):
        lock.acquire()


@pytest.mark.parametrize(
    ("status", "field", "replacement"),
    [
        ("acquired", "pid", None),
        ("acquired", "acquired_at", None),
        ("released", "attempt_id", None),
        ("released", "released_at", None),
        ("quarantined", "run_name", ""),
        ("quarantined", "quarantined_at", None),
        ("cleared", "clear_reason", None),
        ("cleared", "cleared_at", None),
        ("cleared", "last_quarantine", {"attempt_id": "attempt-a"}),
    ],
)
def test_invalid_schema_v2_state_is_rejected_without_modification(
    tmp_path,
    status,
    field,
    replacement,
):
    lock = _lock(tmp_path, "attempt-new")
    metadata = _schema_v2_metadata(lock, status=status)
    if replacement is None:
        del metadata[field]
    else:
        metadata[field] = replacement
    original_content = json.dumps(metadata, indent=2) + "\n"
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    lock.path.write_text(original_content, encoding="utf-8")

    with pytest.raises(MinecraftTargetLockMetadataError):
        lock.acquire()

    assert lock.path.read_text(encoding="utf-8") == original_content


def test_generated_schema_v2_states_validate(tmp_path):
    first = _lock(tmp_path, "attempt-a").acquire()
    assert _read_lock_metadata(tmp_path)["status"] == "acquired"
    first.release()
    assert _read_lock_metadata(tmp_path)["status"] == "released"

    second = _lock(tmp_path, "attempt-b").acquire()
    second.quarantine(
        run_name="run-b",
        reasons=["bridge_cleanup_incomplete"],
        diagnostics={},
    )
    second.release()
    assert _read_lock_metadata(tmp_path)["status"] == "quarantined"

    clear_minecraft_target_quarantine(
        lock_root=tmp_path / "locks",
        host="127.0.0.1",
        port=25565,
        reason="Verified cleanup",
        acknowledge_target_safe=True,
    )
    assert _read_lock_metadata(tmp_path)["status"] == "cleared"


def test_quarantine_persists_and_rejects_next_owner(tmp_path):
    first = _lock(tmp_path, "attempt-a").acquire()
    record = first.quarantine(
        run_name="run-a",
        reasons=["bridge_cleanup_incomplete"],
        diagnostics={"bridge_cleanup_complete": False},
    )
    first.release()

    persisted = json.loads(first.path.read_text(encoding="utf-8"))
    assert record["status"] == persisted["status"] == "quarantined"
    assert persisted["reasons"] == ["bridge_cleanup_incomplete"]
    with pytest.raises(MinecraftTargetQuarantinedError) as raised:
        _lock(tmp_path, "attempt-b").acquire()
    assert raised.value.quarantine["attempt_id"] == "attempt-a"
    assert json.loads(first.path.read_text(encoding="utf-8"))["status"] == "quarantined"


def test_quarantine_does_not_affect_different_target(tmp_path):
    first = _lock(tmp_path, "attempt-a", port=25565).acquire()
    first.quarantine(
        run_name="run-a",
        reasons=["runtime_process_alive_after_kill"],
        diagnostics={},
    )
    first.release()

    with _lock(tmp_path, "attempt-b", port=25566) as second:
        assert second.acquired is True


def test_clear_allows_new_owner_after_explicit_acknowledgement(tmp_path):
    first = _lock(tmp_path, "attempt-a").acquire()
    first.quarantine(
        run_name="run-a",
        reasons=["bridge_cleanup_incomplete"],
        diagnostics={},
    )
    first.release()

    cleared = clear_minecraft_target_quarantine(
        lock_root=tmp_path / "locks",
        host="127.0.0.1",
        port=25565,
        reason="Verified all processes stopped",
        acknowledge_target_safe=True,
    )

    assert cleared["status"] == "cleared"
    assert cleared["last_quarantine"]["attempt_id"] == "attempt-a"
    with _lock(tmp_path, "attempt-b") as replacement:
        assert replacement.acquired is True


def test_clear_rejects_active_owner(tmp_path):
    active = _lock(tmp_path, "attempt-a").acquire()
    try:
        with pytest.raises(MinecraftTargetLockError, match="actively locked"):
            clear_minecraft_target_quarantine(
                lock_root=tmp_path / "locks",
                host="127.0.0.1",
                port=25565,
                reason="Unsafe override",
                acknowledge_target_safe=True,
            )
    finally:
        active.release()


@pytest.mark.parametrize("content", ["{", "[]", '{"schema_version": 99}'])
def test_invalid_or_unsupported_metadata_fails_closed(tmp_path, content):
    lock = _lock(tmp_path, "attempt-a")
    lock.path.parent.mkdir(parents=True)
    lock.path.write_text(content, encoding="utf-8")

    with pytest.raises(MinecraftTargetLockMetadataError):
        lock.acquire()


def test_invalid_utf8_metadata_fails_closed_without_modification(tmp_path):
    lock = _lock(tmp_path, "attempt-a")
    original_content = b"\xff\xfeinvalid-lock-metadata"
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    lock.path.write_bytes(original_content)

    with pytest.raises(MinecraftTargetLockMetadataError, match="encoding is invalid"):
        lock.acquire()

    assert lock.acquired is False
    assert lock._stream is None
    assert lock.path.read_bytes() == original_content


def test_quarantine_requires_non_empty_reasons(tmp_path):
    lock = _lock(tmp_path, "attempt-a").acquire()
    try:
        with pytest.raises(ValueError, match="reasons"):
            lock.quarantine(run_name="run-a", reasons=[], diagnostics={})
    finally:
        lock.release()


def test_quarantine_is_observed_across_process_lock_handoff(tmp_path):
    context = multiprocessing.get_context("fork")
    ready = context.Event()
    release = context.Event()
    result = context.Queue()
    lock_root = str(tmp_path / "locks")
    owner = context.Process(
        target=_quarantine_owner,
        args=(lock_root, ready, release),
    )
    waiter = context.Process(
        target=_quarantine_waiter,
        args=(lock_root, result),
    )

    owner.start()
    assert ready.wait(2)
    waiter.start()
    time.sleep(0.1)
    release.set()
    owner.join(3)
    waiter.join(3)

    assert owner.exitcode == 0
    assert waiter.exitcode == 0
    assert result.get(timeout=1) == "quarantined"
    metadata = read_minecraft_target_lock_metadata(
        lock_root=lock_root,
        host="127.0.0.1",
        port=25565,
    )
    assert metadata["status"] == "quarantined"
    assert metadata["attempt_id"] == "attempt-owner"


def _quarantine_owner(lock_root, ready, release):
    lock = MinecraftTargetLock(
        lock_root=lock_root,
        host="127.0.0.1",
        port=25565,
        world_id="world-a",
        attempt_id="attempt-owner",
    ).acquire()
    lock.quarantine(
        run_name="owner-run",
        reasons=["bridge_cleanup_incomplete"],
        diagnostics={},
    )
    ready.set()
    release.wait(2)
    lock.release()


def _quarantine_waiter(lock_root, result):
    lock = MinecraftTargetLock(
        lock_root=lock_root,
        host="127.0.0.1",
        port=25565,
        world_id="world-a",
        attempt_id="attempt-waiter",
        timeout_seconds=2,
    )
    try:
        lock.acquire()
    except MinecraftTargetQuarantinedError:
        result.put("quarantined")
    else:
        result.put("acquired")
        lock.release()


def _write_schema_v1_metadata(
    lock,
    *,
    status,
    attempt_id,
    pid=None,
    host="127.0.0.1",
):
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": 1,
        "status": status,
        "attempt_id": attempt_id,
        "pid": os.getpid() if pid is None else pid,
        "host": host,
        "port": lock.port,
        "world_id": lock.world_id,
        "lock_key": lock.key,
    }
    lock.path.write_text(json.dumps(metadata), encoding="utf-8")


def _schema_v2_metadata(lock, *, status):
    metadata = {
        "schema_version": 2,
        "status": status,
        "lock_key": lock.key,
        "host": lock.host,
        "port": lock.port,
    }
    if status in {"acquired", "released", "quarantined"}:
        metadata.update({
            "attempt_id": "attempt-a",
            "pid": os.getpid(),
            "world_id": "world-a",
            "acquired_at": 1.0,
            "stale_owner_detected": False,
        })
    if status == "released":
        metadata["released_at"] = 2.0
    elif status == "quarantined":
        metadata.update({
            "run_name": "run-a",
            "quarantined_at": 2.0,
            "reasons": ["bridge_cleanup_incomplete"],
            "diagnostics": {},
        })
    elif status == "cleared":
        metadata.update({
            "cleared_at": 3.0,
            "cleared_by_pid": os.getpid(),
            "clear_reason": "Verified cleanup",
            "last_quarantine": {
                "attempt_id": "attempt-a",
                "run_name": "run-a",
                "quarantined_at": 2.0,
                "reasons": ["bridge_cleanup_incomplete"],
            },
        })
    return metadata


def _read_lock_metadata(tmp_path):
    return read_minecraft_target_lock_metadata(
        lock_root=tmp_path / "locks",
        host="127.0.0.1",
        port=25565,
    )
