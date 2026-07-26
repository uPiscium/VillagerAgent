import json
import os

import pytest

from benchmarks.minecraft.run_lock import MinecraftTargetLock, MinecraftTargetLockError


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
    lock.path.write_text(json.dumps({"pid": 99999999, "attempt_id": "dead"}), encoding="utf-8")

    with lock:
        assert lock.stale_owner_detected is True
        metadata = json.loads(lock.path.read_text(encoding="utf-8"))
        assert metadata["pid"] == os.getpid()
