import json
import multiprocessing
import os
import time

import pytest

from benchmarks.minecraft.run_lock import (
    MinecraftTargetLock,
    MinecraftTargetLockError,
    MinecraftTargetLockMetadataError,
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
    first = _lock(tmp_path, "attempt-a").acquire()
    try:
        with pytest.raises(MinecraftTargetLockError, match="attempt attempt-a"):
            _lock(tmp_path, "attempt-b").acquire()
    finally:
        first.release()


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
    }), encoding="utf-8")

    with lock:
        assert lock.stale_owner_detected is True
        metadata = json.loads(lock.path.read_text(encoding="utf-8"))
        assert metadata["pid"] == os.getpid()


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
