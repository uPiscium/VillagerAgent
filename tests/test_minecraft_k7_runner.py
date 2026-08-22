import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.minecraft import k6_protocol, k7_runner


ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 40


def _git_responses(monkeypatch, *, dirty="", head=REVISION):
    def fake_git(root, *args):
        if args == ("rev-parse", "--show-toplevel"):
            return str(Path(root).resolve()) + "\n"
        if args == ("worktree", "list", "--porcelain"):
            return f"worktree {Path(root).resolve()}\n"
        if args == ("rev-parse", "HEAD"):
            return head + "\n"
        if args == ("status", "--porcelain", "--untracked-files=all"):
            return dirty
        raise AssertionError(f"unexpected git request: {args}")

    monkeypatch.setattr(k7_runner, "_git", fake_git)


class _ProtocolFacade:
    CONDITIONS = k6_protocol.CONDITIONS

    def __init__(self, cells, *, bindings=None):
        self._cells = tuple(cells)
        self._protocol = k6_protocol.load_k6_protocol()
        if bindings is not None:
            self._protocol = copy.deepcopy(self._protocol)
            self._protocol["validated_protocol_digest"] = bindings

    def load_k6_protocol(self):
        return self._protocol

    def build_k6_cells(self):
        return self._cells

    def validate_k6_trace(self, trace, *, cell):
        expected = {name: getattr(cell, name) for name in (
            "cell_id", "scenario_family", "inventory_id", "condition",
            "affected_actor", "matrix",
        )}
        if trace["cell"] != expected:
            raise ValueError("synthetic cell identity mismatch")
        return trace

    def aggregate_k6_results(self, traces):
        return {
            "complete": len(traces) == 60,
            "observed_primary_cells": sum(t["cell"]["matrix"] == "primary" for t in traces),
            "observed_control_cells": sum(t["cell"]["matrix"] == "control" for t in traces),
            "synthetic": True,
        }


def _facade(**kwargs):
    return _ProtocolFacade(k6_protocol.build_k6_cells(), **kwargs)


def _trace(cell):
    identity = {name: getattr(cell, name) for name in (
        "cell_id", "scenario_family", "inventory_id", "condition",
        "affected_actor", "matrix",
    )}
    pair_key = "|".join(str(identity[name]) for name in (
        "scenario_family", "inventory_id", "affected_actor", "matrix"))
    return {
        "schema_version": "synthetic-k6-trace/1",
        "cell": identity,
        "pairing_digest": hashlib.sha256(pair_key.encode()).hexdigest(),
        "synthetic_trace": True,
    }


class _Fixture:
    def __init__(self, calls, fail_at=None):
        self.calls = calls
        self.fail_at = fail_at

    def construct_k6_trial(self, cell):
        self.calls["construct"] += 1
        ordinal = self.calls["construct"]
        if ordinal == self.fail_at:
            raise RuntimeError("synthetic construction failure")
        return _Trial(self.calls, cell)


class _Trial:
    def __init__(self, calls, cell):
        self.calls, self.cell = calls, cell

    def submit(self):
        self.calls["submit"] += 1
        return _trace(self.cell)


def _run(
    monkeypatch, tmp_path, *, run_id="census", fixture=None, protocol=None,
    fault_hook=None,
):
    _git_responses(monkeypatch)
    return k7_runner._run_with_dependencies(
        ROOT, run_id=run_id, expected_execution_revision=REVISION,
        output_dir=tmp_path, fixture_module=fixture or _Fixture({"construct": 0, "submit": 0}),
        protocol_module=protocol or _facade(), fault_hook=fault_hook,
    )


def _final_dir(tmp_path, run_id="census"):
    return tmp_path / run_id / "final"


def _assert_no_authoritative_final(tmp_path, run_id="census"):
    run_dir = tmp_path / run_id
    final = _final_dir(tmp_path, run_id)
    assert not (final / "final_manifest.json").exists()
    assert not (final / "aggregate.json").exists()
    assert not (run_dir / "aggregate.json").exists()


def test_initial_manifest_has_canonical_60_cell_order_and_statuses(monkeypatch, tmp_path):
    calls = {"construct": 0, "submit": 0}
    fixture = _Fixture(calls)
    protocol = _facade()
    saved = []
    original = k7_runner._durable_json

    def capture(path, value, **kwargs):
        if path.name == "run_manifest.json" and not kwargs.get("replace_existing"):
            saved.append(copy.deepcopy(value))
        return original(path, value, **kwargs)

    monkeypatch.setattr(k7_runner, "_durable_json", capture)
    _run(monkeypatch, tmp_path, fixture=fixture, protocol=protocol)
    manifest = saved[0]
    cells = protocol._cells
    assert [entry["ordinal"] for entry in manifest["cells"]] == list(range(1, 61))
    assert [entry["cell_id"] for entry in manifest["cells"]] == [c.cell_id for c in cells]
    assert [entry["status"] for entry in manifest["cells"]] == ["not_started"] * 60
    assert [entry["path"] for entry in manifest["cells"]] == [
        f"cells/{n:04d}_{cell.cell_id}.json" for n, cell in enumerate(cells, 1)
    ]
    assert manifest["schema_version"] == "minecraft-k7d-run/2"
    assert manifest["canonical_order_source"].endswith("build_k6_cells")
    assert manifest["run_status"] == "not_started"
    assert manifest["started"] is False and manifest["failure"] is None


def test_revision_mismatch_and_dirty_tree_fail_before_construction_or_submission(monkeypatch, tmp_path):
    for dirty, head, expected in (("", "b" * 40, REVISION), ("?? stray\n", REVISION, REVISION)):
        calls = {"construct": 0, "submit": 0}
        _git_responses(monkeypatch, dirty=dirty, head=head)
        with pytest.raises(k7_runner.K7RunnerError):
            k7_runner._run_with_dependencies(
                ROOT, run_id=f"gate-{len(dirty)}", expected_execution_revision=expected,
                output_dir=tmp_path, fixture_module=_Fixture(calls), protocol_module=_facade(),
            )
        assert calls == {"construct": 0, "submit": 0}


def test_existing_run_directory_is_rejected_before_construction(monkeypatch, tmp_path):
    (tmp_path / "already").mkdir()
    calls = {"construct": 0, "submit": 0}
    with pytest.raises(k7_runner.K7RunnerError, match="must not already exist"):
        _run(monkeypatch, tmp_path, run_id="already", fixture=_Fixture(calls))
    assert calls == {"construct": 0, "submit": 0}


def test_cell_failure_marks_prefix_failed_cell_and_suffix_not_started_without_aggregate(monkeypatch, tmp_path):
    calls = {"construct": 0, "submit": 0}
    with pytest.raises(k7_runner.K7RunnerError, match="K7 cell"):
        _run(monkeypatch, tmp_path, fixture=_Fixture(calls, fail_at=4))
    run_dir = tmp_path / "census"
    manifest = json.loads((run_dir / "run_manifest.json").read_text())
    assert [c["status"] for c in manifest["cells"][:6]] == ["completed"] * 3 + ["failed", "not_started", "not_started"]
    assert manifest["run_status"] == "failed" and manifest["failure"] is not None
    _assert_no_authoritative_final(tmp_path)
    assert calls == {"construct": 4, "submit": 3}
    with pytest.raises(k7_runner.K7RunnerError, match="must not already exist"):
        _run(monkeypatch, tmp_path, fixture=_Fixture({"construct": 0, "submit": 0}))


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "pair"])
def test_disk_completeness_and_pair_gates_reject_synthetic_trace_corruption(monkeypatch, tmp_path, mutation):
    original = k7_runner._durable_json
    written = 0

    def corrupt(path, value, **kwargs):
        nonlocal written
        if path.parent.name == "cells" and not kwargs.get("replace_existing"):
            written += 1
            if mutation == "missing" and written == 60:
                return None
            if mutation == "duplicate" and written == 2:
                value = copy.deepcopy(value)
                value["cell"] = copy.deepcopy(value["cell"])
                value["cell"]["cell_id"] = "duplicate-cell"
            if mutation == "pair" and written == 2:
                value = copy.deepcopy(value)
                value["pairing_digest"] = "f" * 64
        return original(path, value, **kwargs)

    monkeypatch.setattr(k7_runner, "_durable_json", corrupt)
    with pytest.raises((k7_runner.K7RunnerError, ValueError), match="(completeness|pair|identity|cell)"):
        _run(monkeypatch, tmp_path)
    assert (tmp_path / "census" / "run_manifest.json").exists()
    _assert_no_authoritative_final(tmp_path)


def test_complete_synthetic_census_publishes_authoritative_final_once(monkeypatch, tmp_path):
    original_publish = k7_runner._rename_directory_no_replace
    publications = []

    def observe_publication(source, destination):
        if Path(source).name == ".final.tmp" and Path(destination).name == "final":
            publications.append((Path(source), Path(destination)))
        return original_publish(source, destination)

    monkeypatch.setattr(k7_runner, "_rename_directory_no_replace", observe_publication)
    result = _run(monkeypatch, tmp_path)
    run_dir = tmp_path / "census"
    manifest = json.loads((run_dir / "run_manifest.json").read_text())
    final = run_dir / "final"
    final_manifest = json.loads((final / "final_manifest.json").read_text())
    final_aggregate_bytes = (final / "aggregate.json").read_bytes()
    assert result["schema_version"] == "minecraft-k7b-aggregate/1"
    assert result["raw_trace_count"] == 60 and result["pair_count"] == 30
    assert result["aggregate"]["synthetic"] is True
    assert final_manifest["completed"] is True
    assert final_manifest["aggregate_path"] == "aggregate.json"
    assert final_manifest["aggregate_sha256"] == hashlib.sha256(final_aggregate_bytes).hexdigest()
    assert manifest["completed"] is True and manifest["aggregate_generated"] is True
    assert manifest["run_status"] == "completed" and manifest["aggregate_path"] == "final/aggregate.json"
    assert not (run_dir / "aggregate.json").exists()
    assert not (run_dir / ".final.tmp").exists()
    assert len(publications) == 1
    assert all(c["status"] == "completed" for c in manifest["cells"])
    assert [p.name for p in sorted((run_dir / "cells").glob("*.json"))] == [
        f"{n:04d}_{cell.cell_id}.json" for n, cell in enumerate(k6_protocol.build_k6_cells(), 1)
    ]
    assert set(result) >= {"runner_contract_digest", "protocol_digest", "inventory_digest", "result_schema_digest"}


@pytest.mark.parametrize("target", ["aggregate.json", "final_manifest.json"])
def test_staging_write_failure_leaves_no_final_authority_and_failed_progress(monkeypatch, tmp_path, target):
    original = k7_runner._durable_json

    def fail_staged(path, value, **kwargs):
        if path.parent.name == ".final.tmp" and path.name == target:
            raise OSError(f"synthetic staging failure: {target}")
        return original(path, value, **kwargs)

    monkeypatch.setattr(k7_runner, "_durable_json", fail_staged)
    with pytest.raises(OSError, match="synthetic staging failure"):
        _run(monkeypatch, tmp_path)
    run_dir = tmp_path / "census"
    progress = json.loads((run_dir / "run_manifest.json").read_text())
    assert progress["run_status"] == "failed"
    assert progress["completed"] is False and progress["aggregate_generated"] is False
    _assert_no_authoritative_final(tmp_path)


def test_failure_before_atomic_publication_leaves_staging_unconsumable(monkeypatch, tmp_path):
    def fault(stage):
        if stage == "before_atomic_publication":
            raise RuntimeError("synthetic pre-publication failure")

    with pytest.raises(RuntimeError, match="synthetic pre-publication failure"):
        _run(monkeypatch, tmp_path, fault_hook=fault)
    run_dir = tmp_path / "census"
    assert (run_dir / ".final.tmp" / "aggregate.json").exists()
    _assert_no_authoritative_final(tmp_path)
    progress = json.loads((run_dir / "run_manifest.json").read_text())
    assert progress["run_status"] == "failed" and progress["failure"] is not None


@pytest.mark.parametrize(
    "stage",
    ["after_staging_mkdir", "after_aggregate_staged", "after_final_manifest_staged"],
)
def test_each_prepublication_hook_failure_has_no_final_authority(monkeypatch, tmp_path, stage):
    def fault(observed):
        if observed == stage:
            raise RuntimeError(f"synthetic {stage} failure")

    with pytest.raises(RuntimeError, match=stage):
        _run(monkeypatch, tmp_path, fault_hook=fault)
    _assert_no_authoritative_final(tmp_path)
    progress = json.loads((tmp_path / "census" / "run_manifest.json").read_text())
    assert progress["run_status"] == "failed"


def test_publication_rename_failure_leaves_no_final_or_consumer_aggregate(monkeypatch, tmp_path):
    def fail_publication(source, destination):
        raise OSError("synthetic publication rename failure")

    monkeypatch.setattr(k7_runner, "_rename_directory_no_replace", fail_publication)
    with pytest.raises(OSError, match="synthetic publication rename failure"):
        _run(monkeypatch, tmp_path)
    _assert_no_authoritative_final(tmp_path)
    run_dir = tmp_path / "census"
    assert not (run_dir / "final").exists()
    assert (run_dir / ".final.tmp" / "aggregate.json").exists()
    progress = json.loads((run_dir / "run_manifest.json").read_text())
    assert progress["run_status"] == "failed"


def test_keyboard_interrupt_after_publication_preserves_authority(monkeypatch, tmp_path):
    def fault(stage):
        if stage == "after_atomic_publication":
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _run(monkeypatch, tmp_path, fault_hook=fault)
    final = _final_dir(tmp_path)
    final_manifest = json.loads((final / "final_manifest.json").read_text())
    assert final_manifest["completed"] is True
    assert (final / "aggregate.json").exists()
    assert not (tmp_path / "census" / ".final.tmp").exists()
    progress = json.loads((tmp_path / "census" / "run_manifest.json").read_text())
    assert progress["run_status"] != "failed"


def test_interrupt_inside_publication_return_gap_preserves_authority(monkeypatch, tmp_path):
    original_publish = k7_runner._rename_directory_no_replace

    def publish_then_interrupt(source, destination):
        original_publish(source, destination)
        raise KeyboardInterrupt

    monkeypatch.setattr(k7_runner, "_rename_directory_no_replace", publish_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        _run(monkeypatch, tmp_path)
    final = _final_dir(tmp_path)
    assert (final / "final_manifest.json").exists()
    assert (final / "aggregate.json").exists()
    progress = json.loads((tmp_path / "census" / "run_manifest.json").read_text())
    assert progress["run_status"] != "failed"


def test_parent_fsync_failure_is_indeterminate_without_downgrade(monkeypatch, tmp_path):
    original_fsync = k7_runner._fsync_directory

    def fail_final_parent(path):
        if path.name == "census" and (path / "final").exists():
            raise OSError("synthetic final parent fsync failure")
        return original_fsync(path)

    monkeypatch.setattr(k7_runner, "_fsync_directory", fail_final_parent)
    with pytest.raises(k7_runner.K7FinalizationDurabilityError, match="parent fsync failed"):
        _run(monkeypatch, tmp_path)
    final = _final_dir(tmp_path)
    assert (final / "final_manifest.json").exists()
    assert (final / "aggregate.json").exists()
    progress = json.loads((tmp_path / "census" / "run_manifest.json").read_text())
    assert progress["run_status"] != "failed"


def test_post_parent_fsync_exception_preserves_authority(monkeypatch, tmp_path):
    def fault(stage):
        if stage == "after_parent_fsync":
            raise RuntimeError("synthetic post-fsync bookkeeping failure")

    with pytest.raises(RuntimeError, match="post-fsync bookkeeping failure"):
        _run(monkeypatch, tmp_path, fault_hook=fault)
    final = _final_dir(tmp_path)
    assert (final / "final_manifest.json").exists()
    assert (final / "aggregate.json").exists()
    progress = json.loads((tmp_path / "census" / "run_manifest.json").read_text())
    assert progress["run_status"] != "failed"


def test_post_publication_progress_write_failure_returns_successfully(monkeypatch, tmp_path):
    original = k7_runner._durable_json

    def fail_completed_progress(path, value, **kwargs):
        if path.name == "run_manifest.json" and value.get("completed") is True:
            raise OSError("synthetic progress refresh failure")
        return original(path, value, **kwargs)

    monkeypatch.setattr(k7_runner, "_durable_json", fail_completed_progress)
    result = _run(monkeypatch, tmp_path)
    assert result["aggregate"]["synthetic"] is True
    final = _final_dir(tmp_path)
    assert (final / "final_manifest.json").exists()
    assert (final / "aggregate.json").exists()
    progress = json.loads((tmp_path / "census" / "run_manifest.json").read_text())
    assert progress["run_status"] == "started"


def test_preexisting_final_at_publication_is_not_overwritten(monkeypatch, tmp_path):
    def create_sentinel(stage):
        if stage == "before_atomic_publication":
            final = _final_dir(tmp_path)
            final.mkdir()
            (final / "sentinel").write_text("preserve me", encoding="utf-8")

    with pytest.raises(k7_runner.K7RunnerError, match="appeared before publication"):
        _run(monkeypatch, tmp_path, fault_hook=create_sentinel)
    final = _final_dir(tmp_path)
    assert (final / "sentinel").read_text(encoding="utf-8") == "preserve me"
    _assert_no_authoritative_final(tmp_path)


def test_atomic_publication_race_does_not_replace_existing_final(monkeypatch, tmp_path):
    original_publish = k7_runner._rename_directory_no_replace

    def create_target_then_publish(source, destination):
        destination.mkdir()
        (destination / "sentinel").write_text("preserve me", encoding="utf-8")
        return original_publish(source, destination)

    monkeypatch.setattr(k7_runner, "_rename_directory_no_replace", create_target_then_publish)
    with pytest.raises(k7_runner.K7RunnerError, match="already exists"):
        _run(monkeypatch, tmp_path)
    final = _final_dir(tmp_path)
    assert (final / "sentinel").read_text(encoding="utf-8") == "preserve me"
    _assert_no_authoritative_final(tmp_path)


@pytest.mark.parametrize(
    "mutation", [
        "malformed", "wrong_shape", "unreadable", "hash", "binding", "cell_ids",
        "aggregate_schema", "aggregate_incomplete",
    ],
)
def test_authoritative_final_predicate_rejects_invalid_bundle(monkeypatch, tmp_path, mutation):
    _run(monkeypatch, tmp_path)
    final = _final_dir(tmp_path)
    manifest_path = final / "final_manifest.json"
    aggregate_path = final / "aggregate.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checks = {
        "execution_revision": manifest["execution_revision"],
        "runner_id": manifest["runner"]["identity"],
        "runner_version": manifest["runner"]["version"],
        "runner_contract_digest": manifest["runner"]["contract_digest"],
        "implementation_sha256": manifest["runner"]["implementation_sha256"],
        "protocol_digest": manifest["protocol_digest"],
        "inventory_digest": manifest["inventory_digest"],
        "result_schema_digest": manifest["result_schema_digest"],
        "cell_ids": list(manifest["canonical_cell_ids"]),
    }
    if mutation == "malformed":
        manifest_path.write_text("{", encoding="utf-8")
    elif mutation == "wrong_shape":
        manifest_path.write_text("[]", encoding="utf-8")
    elif mutation == "hash":
        aggregate_path.write_text("{}\n", encoding="utf-8")
    elif mutation == "binding":
        manifest["protocol_digest"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif mutation == "cell_ids":
        manifest["canonical_cell_ids"][0] = "forged-cell"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif mutation in {"aggregate_schema", "aggregate_incomplete"}:
        aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
        if mutation == "aggregate_schema":
            aggregate["schema_version"] = "forged-aggregate/1"
        else:
            aggregate["aggregate"]["complete"] = False
        payload = (json.dumps(aggregate, sort_keys=True) + "\n").encode("utf-8")
        aggregate_path.write_bytes(payload)
        manifest["aggregate_sha256"] = hashlib.sha256(payload).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        original_read_bytes = Path.read_bytes

        def fail_aggregate_read(path):
            if path == aggregate_path:
                raise OSError("synthetic unreadable aggregate")
            return original_read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", fail_aggregate_read)
    assert k7_runner._authoritative_final_valid(final, "census", checks) is False


def test_contract_k6_binding_drift_fails_preflight_before_construction(monkeypatch, tmp_path):
    calls = {"construct": 0, "submit": 0}
    with pytest.raises(k7_runner.K7RunnerError, match="binding mismatch"):
        _run(monkeypatch, tmp_path, fixture=_Fixture(calls), protocol=_facade(bindings="d" * 64))
    assert calls == {"construct": 0, "submit": 0}


def test_runner_contract_digest_drift_fails_preflight(monkeypatch, tmp_path):
    document = json.loads(k7_runner.CONTRACT_PATH.read_text(encoding="utf-8"))
    document["canonical_order_source"] = "forged.order"
    changed = tmp_path / "changed_contract.json"
    changed.write_text(json.dumps(document), encoding="utf-8")
    original_loader = k7_runner.load_k7_contract
    monkeypatch.setattr(k7_runner, "load_k7_contract", lambda: original_loader(changed))
    _git_responses(monkeypatch)
    with pytest.raises(k7_runner.K7RunnerError, match="digest mismatch"):
        k7_runner.preflight(ROOT, expected_execution_revision=REVISION)


def test_preflight_rejects_a_different_git_repository(monkeypatch, tmp_path):
    _git_responses(monkeypatch)
    with pytest.raises(k7_runner.K7RunnerError, match="checkout containing"):
        k7_runner.preflight(tmp_path, expected_execution_revision=REVISION)
