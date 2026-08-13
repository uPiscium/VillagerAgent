import json
import os

import pytest

from benchmarks.minecraft import gate_a_v4_owned_lifecycle as lifecycle


def _binding():
    return {
        "experiment_id": "minecraft-judged-production-v4",
        "gate": "A",
        "run_id": "diagonal-s17-baseline_open",
        "lease_id": "a" * 64,
        "execution_revision": "25113661a6b09761ab47a05bd70bd8f0386e2b67",
        "premanifest_canonical": "bffaedbe296d18b1c4d0cd7bc0d073d011566e3415133d464e65492bb9c13f3a",
    }


def _to_postflight(handle):
    state = "prepared"
    for next_state in (
        "restore_started", "restore_finished", "executor_started", "executor_finished",
        "validation_started", "validation_finished", "cleanup_started", "postflight_verified",
    ):
        lifecycle.transition_run(handle, state, next_state)
        state = next_state


def test_initial_records_are_exclusive_bounded_and_empty(tmp_path):
    directory = tmp_path / "owned"; directory.mkdir(mode=0o700)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        lifecycle._initialize_records(descriptor, _binding())
        with pytest.raises(lifecycle.OwnedLifecycleError):
            lifecycle._initialize_records(descriptor, _binding())
    finally:
        os.close(descriptor)
    assert set(path.name for path in directory.iterdir()) == {"lease.json", "run-state.json", "children.json"}
    children = json.loads((directory / "children.json").read_text())
    assert children["children"] == []
    assert children["generation"] == 0
    assert children["registered_total"] == children["reaped_total"] == 0
    assert all(path.stat().st_size <= lifecycle.MAX_RECORD_BYTES for path in directory.iterdir())


def test_invalid_or_arbitrary_binding_is_rejected(tmp_path):
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for change in (
            {"run_id": "other"}, {"lease_id": "bad"}, {"path": "/secret"},
        ):
            value = _binding(); value.update(change)
            with pytest.raises(lifecycle.OwnedLifecycleError):
                lifecycle._initialize_records(descriptor, value)
    finally:
        os.close(descriptor)


def test_namespace_reservation_and_child_lifecycle_are_exclusive(tmp_path):
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    namespace_fd = lifecycle._reserve_namespace(parent_fd, _binding())
    identity = {"pid": 10, "start_ticks": 20, "pgid": 10, "session_id": 10}
    try:
        lifecycle._register_process_group(namespace_fd, _binding(), identity)
        lifecycle._mark_process_group_reaped(namespace_fd, _binding(), identity)
        children = json.loads((tmp_path / lifecycle.NAMESPACE_NAME / "children.json").read_text())
        assert children["generation"] == 2
        assert children["children"] == []
        assert children["registered_total"] == children["reaped_total"] == 1
        with pytest.raises(lifecycle.OwnedLifecycleError):
            lifecycle._register_process_group(namespace_fd, _binding(), identity)
    finally:
        os.close(namespace_fd)
        os.close(parent_fd)
    second_parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(lifecycle.OwnedLifecycleError):
            lifecycle._reserve_namespace(second_parent, _binding())
    finally:
        os.close(second_parent)


@pytest.mark.parametrize("identity", [
    {}, {"pid": 10},
    {"pid": 10, "start_ticks": 20, "pgid": 10, "session_id": 10, "extra": 1},
    {"pid": 11, "start_ticks": 20, "pgid": 10, "session_id": 10},
])
def test_reap_requires_exact_registered_identity(tmp_path, identity):
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    namespace_fd = lifecycle._reserve_namespace(parent_fd, _binding())
    registered = {"pid": 10, "start_ticks": 20, "pgid": 10, "session_id": 10}
    try:
        lifecycle._register_process_group(namespace_fd, _binding(), registered)
        with pytest.raises(lifecycle.OwnedLifecycleError):
            lifecycle._mark_process_group_reaped(namespace_fd, _binding(), identity)
    finally:
        os.close(namespace_fd)
        os.close(parent_fd)


@pytest.mark.parametrize("change", [
    {"role": "other"}, {"extra": "field"}, {"state": "reaped"},
])
def test_reap_rejects_malformed_stored_child(tmp_path, change):
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    namespace_fd = lifecycle._reserve_namespace(parent_fd, _binding())
    identity = {"pid": 10, "start_ticks": 20, "pgid": 10, "session_id": 10}
    try:
        lifecycle._register_process_group(namespace_fd, _binding(), identity)
        path = tmp_path / lifecycle.NAMESPACE_NAME / "children.json"
        record = json.loads(path.read_text())
        record["children"][0].update(change)
        path.write_text(json.dumps(record))
        with pytest.raises(lifecycle.OwnedLifecycleError):
            lifecycle._mark_process_group_reaped(namespace_fd, _binding(), identity)
    finally:
        os.close(namespace_fd)
        os.close(parent_fd)


def test_owned_handle_acquires_lock_tracks_multiple_children_and_releases_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "FIXED_PRIVATE_PARENT", tmp_path)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    handle = lifecycle.acquire_owned_run(parent_fd, _binding())
    first = {"pid": 10, "start_ticks": 20, "pgid": 10, "session_id": 10}
    second = {"pid": 11, "start_ticks": 21, "pgid": 11, "session_id": 11}
    try:
        assert (tmp_path / lifecycle.NAMESPACE_NAME / "lock").exists()
        lifecycle.register_owned_child(handle, first)
        lifecycle.register_owned_child(handle, second)
        assert lifecycle.owned_child_count(handle) == 2
        lifecycle.mark_owned_child_reaped(handle, first)
        lifecycle.mark_owned_child_reaped(handle, second)
        assert lifecycle.owned_child_count(handle) == 0
        _to_postflight(handle)
        lifecycle.release_owned_run(handle, {
            "managed_containers": 0, "run_owned_children": 0,
            "runtime_result_reusable": False,
        }, "success")
        assert not (tmp_path / lifecycle.NAMESPACE_NAME / "lock").exists()
        lease = json.loads((tmp_path / lifecycle.NAMESPACE_NAME / "lease.json").read_text())
        assert lease["state"] == "released"
    finally:
        handle.close()
        os.close(parent_fd)


def test_identity_drift_and_live_child_block_clean_release(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "FIXED_PRIVATE_PARENT", tmp_path)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    handle = lifecycle.acquire_owned_run(parent_fd, _binding())
    identity = {"pid": 10, "start_ticks": 20, "pgid": 10, "session_id": 10}
    try:
        lifecycle.register_owned_child(handle, identity)
        _to_postflight(handle)
        with pytest.raises(lifecycle.OwnedLifecycleError):
            lifecycle.release_owned_run(handle, {
                "managed_containers": 0, "run_owned_children": 0,
                "runtime_result_reusable": False,
            }, "success")
        with pytest.raises(TypeError):
            handle.binding["lease_id"] = "b" * 64
        os.rename(
            lifecycle.NAMESPACE_NAME, lifecycle.NAMESPACE_NAME + ".replaced",
            src_dir_fd=handle.parent_fd, dst_dir_fd=handle.parent_fd,
        )
        with pytest.raises(lifecycle.OwnedLifecycleError):
            lifecycle.validate_owned_run(handle)
    finally:
        handle.close()
        os.close(parent_fd)


def test_lifecycle_rejects_skipped_resume_or_backward_transition(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "FIXED_PRIVATE_PARENT", tmp_path)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    handle = lifecycle.acquire_owned_run(parent_fd, _binding())
    try:
        for expected, next_state in (
            ("prepared", "executor_started"),
            ("prepared", "postflight_verified"),
            ("prepared", "prepared"),
        ):
            with pytest.raises(lifecycle.OwnedLifecycleError):
                lifecycle.transition_run(handle, expected, next_state)
    finally:
        handle.close()
        os.close(parent_fd)


@pytest.mark.parametrize("record_name", ["lock", "lease.json", "run-state.json", "children.json"])
def test_owned_handle_rejects_persisted_ownership_drift(tmp_path, record_name, monkeypatch):
    monkeypatch.setattr(lifecycle, "FIXED_PRIVATE_PARENT", tmp_path)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    handle = lifecycle.acquire_owned_run(parent_fd, _binding())
    try:
        path = tmp_path / lifecycle.NAMESPACE_NAME / record_name
        record = json.loads(path.read_text())
        record["generation" if "generation" in record else "state"] = "drift"
        path.write_text(json.dumps(record))
        with pytest.raises(lifecycle.OwnedLifecycleError, match="identity drift|registry rejected"):
            lifecycle.validate_owned_run(handle)
    finally:
        handle.close()
        os.close(parent_fd)


def test_partial_namespace_initialization_marks_available_lease_blocked(tmp_path, monkeypatch):
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    real_write = lifecycle._write_exclusive

    def fail_run_state(directory_fd, name, value):
        if name == "run-state.json":
            raise lifecycle.OwnedLifecycleError("synthetic partial failure")
        return real_write(directory_fd, name, value)

    monkeypatch.setattr(lifecycle, "_write_exclusive", fail_run_state)
    try:
        with pytest.raises(lifecycle.OwnedLifecycleError):
            lifecycle._reserve_namespace(parent_fd, _binding())
        lease = json.loads((tmp_path / lifecycle.NAMESPACE_NAME / "lease.json").read_text())
        assert lease["state"] == "blocked"
    finally:
        os.close(parent_fd)


def test_lock_removal_failure_never_leaves_released_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "FIXED_PRIVATE_PARENT", tmp_path)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    handle = lifecycle.acquire_owned_run(parent_fd, _binding())
    _to_postflight(handle)
    real_unlink = os.unlink

    def reject_lock(name, *args, **kwargs):
        if name == "lock":
            raise OSError
        return real_unlink(name, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", reject_lock)
    try:
        with pytest.raises(lifecycle.OwnedLifecycleError):
            lifecycle.release_owned_run(handle, {
                "managed_containers": 0, "run_owned_children": 0,
                "runtime_result_reusable": False,
            }, "success")
        lifecycle.block_owned_run(handle)
        lease = json.loads((tmp_path / lifecycle.NAMESPACE_NAME / "lease.json").read_text())
        assert lease["state"] == "blocked"
    finally:
        handle.close()
        os.close(parent_fd)


def test_acquire_rejects_arbitrary_parent(tmp_path):
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(lifecycle.OwnedLifecycleError, match="parent rejected"):
            lifecycle.acquire_owned_run(parent_fd, _binding())
        assert not (tmp_path / lifecycle.NAMESPACE_NAME).exists()
    finally:
        os.close(parent_fd)
