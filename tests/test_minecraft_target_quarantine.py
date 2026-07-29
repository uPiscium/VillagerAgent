import json

from benchmarks.minecraft.run_lock import MinecraftTargetLock
from benchmarks.minecraft.target_quarantine import main


def test_status_reports_absent_and_persistent_quarantine(tmp_path, capsys):
    args = _base_args(tmp_path)
    assert main(["status", *args]) == 0
    assert json.loads(capsys.readouterr().out)["quarantined"] is False

    lock = _lock(tmp_path).acquire()
    lock.quarantine(
        run_name="run-a",
        reasons=["bridge_cleanup_incomplete"],
        diagnostics={},
    )
    lock.release()

    assert main(["status", *args]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["quarantined"] is True
    assert payload["metadata"]["attempt_id"] == "attempt-a"


def test_clear_requires_acknowledgement(tmp_path, capsys):
    _quarantine(tmp_path)

    assert main([
        "clear",
        *_base_args(tmp_path),
        "--reason",
        "Verified cleanup",
    ]) == 1
    assert "acknowledge_target_safe" in capsys.readouterr().out


def test_clear_rejects_empty_reason(tmp_path, capsys):
    _quarantine(tmp_path)

    assert main([
        "clear",
        *_base_args(tmp_path),
        "--reason",
        " ",
        "--acknowledge-target-safe",
    ]) == 1
    assert "non-empty reason" in capsys.readouterr().out


def test_clear_rejects_active_owner(tmp_path, capsys):
    lock = _lock(tmp_path).acquire()
    try:
        assert main([
            "clear",
            *_base_args(tmp_path),
            "--reason",
            "Verified cleanup",
            "--acknowledge-target-safe",
        ]) == 1
        assert "actively locked" in capsys.readouterr().out
    finally:
        lock.release()


def test_clear_records_reason_and_allows_new_owner(tmp_path, capsys):
    _quarantine(tmp_path)

    assert main([
        "clear",
        *_base_args(tmp_path),
        "--reason",
        "Verified cleanup",
        "--acknowledge-target-safe",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "cleared"
    assert payload["clear_reason"] == "Verified cleanup"
    replacement = MinecraftTargetLock(
        lock_root=tmp_path / "locks",
        host="127.0.0.1",
        port=25565,
        world_id="world-a",
        attempt_id="attempt-b",
    ).acquire()
    replacement.release()


def test_clear_corrupt_metadata_requires_force_corrupt(tmp_path, capsys):
    lock = _lock(tmp_path)
    lock.path.parent.mkdir(parents=True)
    lock.path.write_text("{", encoding="utf-8")
    clear_args = [
        "clear",
        *_base_args(tmp_path),
        "--reason",
        "Verified cleanup",
        "--acknowledge-target-safe",
    ]

    assert main(clear_args) == 1
    assert "force_corrupt" in capsys.readouterr().out
    assert main([*clear_args, "--force-corrupt"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "cleared"


def test_status_accepts_legacy_schema_v1_metadata(tmp_path, capsys):
    _write_schema_v1_released_metadata(tmp_path)

    assert main(["status", *_base_args(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["quarantined"] is False
    assert payload["metadata"]["schema_version"] == 1
    assert payload["metadata"]["status"] == "released"


def test_clear_reports_legacy_schema_as_not_quarantined(tmp_path, capsys):
    _write_schema_v1_released_metadata(tmp_path)

    assert main([
        "clear",
        *_base_args(tmp_path),
        "--reason",
        "Verified cleanup",
        "--acknowledge-target-safe",
    ]) == 1
    output = capsys.readouterr().out
    assert "not quarantined" in output
    assert "force_corrupt" not in output


def _base_args(tmp_path):
    return [
        "--host",
        "127.0.0.1",
        "--port",
        "25565",
        "--lock-root",
        str(tmp_path / "locks"),
    ]


def _lock(tmp_path):
    return MinecraftTargetLock(
        lock_root=tmp_path / "locks",
        host="127.0.0.1",
        port=25565,
        world_id="world-a",
        attempt_id="attempt-a",
    )


def _quarantine(tmp_path):
    lock = _lock(tmp_path).acquire()
    lock.quarantine(
        run_name="run-a",
        reasons=["bridge_cleanup_incomplete"],
        diagnostics={},
    )
    lock.release()


def _write_schema_v1_released_metadata(tmp_path):
    lock = _lock(tmp_path)
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    lock.path.write_text(json.dumps({
        "schema_version": 1,
        "status": "released",
        "attempt_id": "attempt-legacy",
        "pid": 99999999,
        "host": "127.0.0.1",
        "port": 25565,
        "world_id": "world-a",
        "lock_key": lock.key,
    }), encoding="utf-8")
