"""Fake-only tests for Issue #497 experiment-owned admission."""
import ast
import json
import os
import sys
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from benchmarks.minecraft import gate_a_v4_owned_admission as admission


def _git(worktree, **changes):
    value = {
        "revision": admission.FIXED_REVISION,
        "detached": True,
        "clean": True,
        "worktrees": (str(worktree),),
    }
    value.update(changes)
    return value


def _runtime(**changes):
    value = {
        "child_manifest_sha256": admission.FIXED_CHILD_MANIFEST,
        "child_assets": admission.FIXED_CHILD_ASSETS,
        "runtime_digest": admission.FIXED_RUNTIME_DIGEST,
        "runtime_image": admission.FIXED_RUNTIME_IMAGE,
    }
    value.update(changes)
    return value


def _premanifest(**changes):
    value = {
        "byte_sha256": admission.FIXED_PREMANIFEST_BYTES,
        "canonical_identity": admission.FIXED_PREMANIFEST_CANONICAL,
        "mode": admission.FIXED_PREMANIFEST_MODE,
        "canary": admission.RUN_ID,
        "baseline_archive_sha256": admission.BASELINE_ARCHIVE_SHA256,
        "baseline_tree_sha256": admission.BASELINE_TREE_SHA256,
    }
    value.update(changes)
    return value


def _model(**changes):
    value = {
        "provider": "ollama", "name": "gemma4:12b",
        "digest": admission.FIXED_MODEL_DIGEST,
        "endpoint": admission.FIXED_MODEL_ENDPOINT, "matched_count": 1,
    }
    value.update(changes)
    return value


def _docker(**changes):
    value = {
        "contract_sha256": admission.FIXED_DOCKER_CONTRACT,
        "connection_category": "current_uid_rootless_unix_socket",
        "authorization_category": "current_uid_owner_read_write_no_world",
        "daemon_identity_category": "pinned_rootless_daemon",
        "executable_identity_category": "pinned_trusted_executable",
        "pinned_image": "matched", "managed_container_count": 0,
        "identity": ("daemon", "executable", "image"),
    }
    value.update(changes)
    return value


def _bindings(worktree, **overrides):
    values = {
        "git": _git(worktree), "runtime": _runtime(),
        "premanifest": _premanifest(), "model": _model(), "docker": _docker(),
    }
    values.update(overrides)
    return admission.AdmissionBindings(**{
        name: (lambda name=name: dict(values[name])) for name in values
    })


@pytest.fixture
def clean(tmp_path):
    parent = tmp_path / "private"; parent.mkdir(mode=0o700)
    worktree = tmp_path / "checkout"; worktree.mkdir()
    return admission.owned_paths(parent), _bindings(worktree), worktree


def _assert_failed(paths, bindings):
    with pytest.raises(admission.OwnedAdmissionError):
        admission.read_only_admission(paths, bindings)


def test_clean_owned_state_passes_with_exact_zero_effect_record(clean):
    paths, bindings, _ = clean
    result = admission.read_only_admission(paths, bindings)
    assert result["status"] == "admission_passed"
    assert result["managed_containers"] == 0
    assert result["run_owned_children"] == 0
    assert result["canary"] == "diagonal-s17-baseline_open"
    assert result["final_recheck"] == "passed"
    assert result["attempts"] == 0
    assert set(result["counters"].values()) == {0}
    assert set(result["execution_flags"].values()) == {False}


def test_managed_container_fails_but_unrelated_container_is_ignored(clean):
    paths, _, worktree = clean
    _assert_failed(paths, _bindings(worktree, docker=_docker(managed_container_count=1)))
    result = admission.read_only_admission(
        paths, _bindings(worktree, docker=_docker(unrelated_container_count=17)),
    )
    assert result["status"] == "admission_passed"
    _assert_failed(paths, _bindings(worktree, docker=_docker(managed_labeled_count=1)))


def test_docker_label_filters_are_scoped_to_fixed_experiment_or_run():
    assert admission.OWNED_DOCKER_LABEL_FILTERS == (
        "label=org.villageragent.minecraft.managed=true",
        "label=org.villageragent.experiment=minecraft-judged-production-v4",
        "label=org.villageragent.gate=A",
        "label=org.villageragent.run=diagonal-s17-baseline_open",
    )
    assert all("org.villageragent.lease" not in item for item in admission.OWNED_DOCKER_LABEL_FILTERS)


def test_host_docker_binding_classifies_managed_residue_without_clean_short_circuit(monkeypatch, clean):
    require_clean_values = []

    class Contract:
        @staticmethod
        def bind_environment():
            return {}

        @staticmethod
        def inspect_docker_contract(executable, environment, previous=None, require_clean=True):
            del executable, environment, previous
            require_clean_values.append(require_clean)
            return SimpleNamespace(identity=("fixed",), report={
                "connection_category": "current_uid_rootless_unix_socket",
                "authorization_category": "current_uid_owner_read_write_no_world",
                "daemon_identity_category": "pinned_rootless_daemon",
                "executable_identity_category": "pinned_trusted_executable",
                "managed_container_count": 0,
            })

        @staticmethod
        def make_bound_runner(executable, identity, environment):
            del executable, identity, environment

            def runner(argv, **kwargs):
                del kwargs
                if argv[1:3] == ["image", "inspect"]:
                    return SimpleNamespace(stdout=json.dumps({
                        "Id": "sha256:5b3e96bcd7dace8ab7be89c245dc9ba0b1573fdef2f01d3e101ac40e7843fa70",
                        "RepoDigests": ["image@sha256:5b3e96bcd7dace8ab7be89c245dc9ba0b1573fdef2f01d3e101ac40e7843fa70"],
                        "Config": {"Labels": {"org.opencontainers.image.revision": "162bd9b5f19a0de2870407a4406506aeb0fe5a99"}},
                    }))
                if argv[1] == "ps":
                    return SimpleNamespace(stdout="va-mc-owned\n")
                return SimpleNamespace(stdout="")

            return runner

    monkeypatch.setattr(admission, "_load_authenticated", lambda *args: Contract)
    host = admission._host_bindings(Path("/fixed"), Path("/fixed/premanifest"), Path("/fixed/contract"), Path("/fixed/docker"))
    paths, bindings, worktree = clean
    combined = admission.AdmissionBindings(
        git=bindings.git, runtime=bindings.runtime, premanifest=bindings.premanifest,
        model=bindings.model, docker=host.docker,
    )
    result = admission.diagnostic_admission(paths, combined)
    assert (result["phase_id"], result["reason_code"]) == (
        "managed_docker_residue", "managed_container_residue",
    )
    assert require_clean_values == [False, False]


def test_ownership_namespace_is_stable_and_not_issue_number_scoped(clean):
    paths, _, _ = clean
    assert paths.namespace.name == ".villageragent.minecraft-judged-production-v4.gate-a.diagonal-s17-baseline_open"
    assert "issue-497" not in str(paths.namespace)


@pytest.mark.parametrize("field", ["output", "lock", "work", "runtime_result", "runtime_result_tmp"])
def test_existing_destination_or_temp_result_fails_closed(clean, field):
    paths, bindings, _ = clean
    target = Path(getattr(paths, field)); target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("stale")
    _assert_failed(paths, bindings)


@pytest.mark.parametrize("field", ["lease", "run_state", "child_registry"])
def test_stale_or_malformed_owned_marker_fails_closed(clean, field):
    paths, bindings, _ = clean
    target = Path(getattr(paths, field)); target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("malformed-or-live")
    _assert_failed(paths, bindings)


def test_run_owned_child_marked_alive_fails_without_process_scan(clean):
    paths, bindings, _ = clean
    paths.child_registry.parent.mkdir(parents=True, exist_ok=True)
    paths.child_registry.write_text(json.dumps({
        "schema_version": "run-owned-children.v1",
        "children": [{"state": "alive"}],
    }))
    _assert_failed(paths, bindings)


def test_symlink_destination_and_worktree_destination_fail(clean):
    paths, bindings, worktree = clean
    target = paths.private_parent / "target"; target.write_text("x")
    paths.output.parent.mkdir(parents=True, exist_ok=True)
    paths.output.symlink_to(target)
    _assert_failed(paths, bindings)
    paths.output.unlink()
    inside = admission.owned_paths(worktree)
    _assert_failed(inside, _bindings(worktree))


@pytest.mark.parametrize("mount_kind", ["separate_tmp", "tmpfs", "stable_bind"])
def test_stable_external_mount_boundary_is_not_a_failure(clean, monkeypatch, mount_kind):
    paths, bindings, _ = clean
    devices = {"separate_tmp": "2:1", "tmpfs": "3:1", "stable_bind": "1:1"}
    roots = {
        "separate_tmp": PurePosixPath("/"), "tmpfs": PurePosixPath("/"),
        "stable_bind": PurePosixPath("/external/owned-parent"),
    }
    table = (
        ("1:1", PurePosixPath("/"), Path("/")),
        (devices[mount_kind], roots[mount_kind], paths.private_parent),
    )
    monkeypatch.setattr(admission, "_read_mount_table", lambda: table)
    assert admission.diagnostic_admission(paths, bindings)["status"] == "admission_passed"


def test_worktree_subdirectory_bind_alias_fails(clean, monkeypatch):
    paths, bindings, worktree = clean
    table = (
        ("1:1", PurePosixPath("/"), Path("/")),
        ("1:1", PurePosixPath(str(worktree / "owned-subdirectory")), paths.private_parent),
    )
    monkeypatch.setattr(admission, "_read_mount_table", lambda: table)
    result = admission.diagnostic_admission(paths, bindings)
    assert (result["phase_id"], result["reason_code"]) == (
        "destination_parent", "destination_symlink_or_alias",
    )


def test_nested_mount_then_bind_inside_worktree_alias_fails(clean, monkeypatch):
    paths, bindings, worktree = clean
    nested_mount = worktree / "external-mount"
    table = (
        ("1:1", PurePosixPath("/"), Path("/")),
        ("2:1", PurePosixPath("/"), nested_mount),
        ("2:1", PurePosixPath("/owned-subdirectory"), paths.private_parent),
    )
    monkeypatch.setattr(admission, "_read_mount_table", lambda: table)
    result = admission.diagnostic_admission(paths, bindings)
    assert (result["phase_id"], result["reason_code"]) == (
        "destination_parent", "destination_symlink_or_alias",
    )


@pytest.mark.parametrize(("binding", "changed"), [
    ("git", {"revision": "0" * 40}),
    ("runtime", {"runtime_digest": "sha256:" + "0" * 64}),
    ("runtime", {"child_manifest_sha256": "0" * 64}),
    ("runtime", {"child_assets": 122}),
    ("premanifest", {"byte_sha256": "0" * 64}),
    ("premanifest", {"canonical_identity": "0" * 64}),
    ("premanifest", {"mode": 0o600}),
    ("premanifest", {"canary": "other"}),
    ("premanifest", {"baseline_archive_sha256": "0" * 64}),
    ("premanifest", {"baseline_tree_sha256": "0" * 64}),
    ("model", {"digest": "0" * 64}),
    ("docker", {"daemon_identity_category": "other"}),
    ("docker", {"executable_identity_category": "other"}),
    ("docker", {"pinned_image": "mismatch"}),
])
def test_fixed_identity_mismatch_fails_closed(clean, binding, changed):
    paths, _, worktree = clean
    factories = {
        "git": _git(worktree), "runtime": _runtime(),
        "premanifest": _premanifest(), "model": _model(), "docker": _docker(),
    }
    factories[binding].update(changed)
    _assert_failed(paths, _bindings(worktree, **{binding: factories[binding]}))


def test_exact_canary_and_baseline_derivation_pass(clean):
    paths, bindings, _ = clean
    result = admission.read_only_admission(paths, bindings)
    assert result["canary"] == admission.RUN_ID
    assert result["baseline_identity"] == "match"


def test_final_recheck_identity_drift_fails(clean):
    paths, bindings, worktree = clean
    values = iter((_runtime(), _runtime(runtime_digest="sha256:" + "0" * 64)))
    drift = admission.AdmissionBindings(
        git=bindings.git, runtime=lambda: next(values), premanifest=bindings.premanifest,
        model=bindings.model, docker=bindings.docker,
    )
    _assert_failed(paths, drift)


def test_final_recheck_has_bounded_reason(clean):
    paths, bindings, worktree = clean
    docker_values = iter((_docker(), _docker(unrelated_container_count=1)))
    drift = admission.AdmissionBindings(
        git=bindings.git, runtime=bindings.runtime, premanifest=bindings.premanifest,
        model=bindings.model, docker=lambda: next(docker_values),
    )
    result = admission.diagnostic_admission(paths, drift)
    assert (result["phase_id"], result["reason_code"]) == ("final_recheck", "final_recheck_failed")


def test_git_worktree_alias_has_bounded_reason(clean):
    _, _, worktree = clean
    paths = admission.owned_paths(worktree)
    result = admission.diagnostic_admission(paths, _bindings(worktree))
    assert (result["phase_id"], result["reason_code"]) == (
        "destination_parent", "destination_symlink_or_alias",
    )


def test_parent_replacement_has_bounded_reason(clean, monkeypatch):
    paths, bindings, _ = clean
    real_open = admission._open_parent

    def changed_parent(value):
        descriptor, identity = real_open(value)
        return descriptor, (identity[0], identity[1] + 1, identity[2], identity[3])

    monkeypatch.setattr(admission, "_open_parent", changed_parent)
    result = admission.diagnostic_admission(paths, bindings)
    assert (result["phase_id"], result["reason_code"]) == (
        "destination_parent", "destination_parent_identity_changed",
    )


def test_unrelated_same_uid_process_and_unreadable_fields_are_not_inputs(clean, monkeypatch):
    paths, bindings, _ = clean
    touched = False

    def forbidden(*args, **kwargs):
        nonlocal touched; touched = True
        raise AssertionError("host process inspection forbidden")

    monkeypatch.setattr(os, "listdir", forbidden)
    assert admission.read_only_admission(paths, bindings)["status"] == "admission_passed"
    assert touched is False


def test_no_old_classifier_observer_or_effectful_gate_dependency():
    source = Path(admission.__file__).read_text()
    for forbidden in (
        "gate_a_v4_process_admission", "gate_a_v4_process_diagnostic",
        "gate_a_v4_fake_observer", "gate_a_v4_observer_protocol",
        "gate_a_v4_process_augmentation", "restore_world_snapshot",
        "DockerMatrixExecutor", "validate_matrix_run", "execute_gate_a_once",
        "gate_b", "gate_c",
    ):
        assert forbidden not in source
    assert 'Path("/proc/self/mountinfo")' in source
    assert 'Path("/proc")' not in source
    tree = ast.parse(source)
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree) if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert not calls.intersection({"mkdir", "makedirs", "write_text", "write_bytes", "replace", "rename"})


def test_git_environment_disables_replacement_objects_and_external_config():
    environment = admission._git_environment()
    assert environment["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert environment["GIT_OPTIONAL_LOCKS"] == "0"


def test_public_result_is_bounded_and_has_no_host_process_data(clean):
    result = admission.read_only_admission(clean[0], clean[1])
    encoded = json.dumps(result, sort_keys=True)
    assert len(encoded.encode()) < 4096
    for forbidden in ("pid", "ppid", "argv", "cwd", "exe path", "username", "/proc", "process table"):
        assert forbidden not in encoded.lower()


@pytest.mark.parametrize(("binding", "changed", "phase_id", "reason_code"), [
    ("git", {"revision": "0" * 40}, "revision_worktree", "revision_mismatch"),
    ("git", {"detached": False}, "revision_worktree", "worktree_dirty_or_not_detached"),
    ("runtime", {"runtime_digest": "sha256:" + "0" * 64}, "runtime_identity", "runtime_mismatch"),
    ("runtime", {"child_manifest_sha256": "0" * 64}, "runtime_identity", "child_manifest_mismatch"),
    ("premanifest", {"byte_sha256": "0" * 64}, "premanifest_identity", "premanifest_mismatch"),
    ("premanifest", {"canary": "other"}, "canary_derivation", "canary_mismatch"),
    ("premanifest", {"baseline_tree_sha256": "0" * 64}, "baseline_identity", "baseline_mismatch"),
    ("model", {"digest": "0" * 64}, "model_inventory", "model_inventory_mismatch"),
    ("docker", {"daemon_identity_category": "other"}, "docker_identity", "docker_identity_mismatch"),
    ("docker", {"managed_container_count": 1}, "managed_docker_residue", "managed_container_residue"),
    ("docker", {"managed_labeled_count": 1}, "managed_docker_residue", "managed_name_or_label_collision"),
])
def test_bounded_identity_diagnostic_mapping(clean, binding, changed, phase_id, reason_code):
    paths, _, worktree = clean
    values = {
        "git": _git(worktree), "runtime": _runtime(), "premanifest": _premanifest(),
        "model": _model(), "docker": _docker(),
    }
    values[binding].update(changed)
    result = admission.diagnostic_admission(paths, _bindings(worktree, **{binding: values[binding]}))
    assert (result["phase_id"], result["reason_code"]) == (phase_id, reason_code)
    assert result["attempts"] == 0


@pytest.mark.parametrize(("field", "content", "reason_code"), [
    ("output", "stale", "destination_exists"),
    ("lease", "stale", "ownership_state_present"),
    ("runtime_result", "partial", "runtime_result_state_present"),
    ("child_registry", "not-json", "ownership_state_malformed"),
    ("child_registry", json.dumps({
        "schema_version": "gate_a_v4_owned_children.v1",
        "experiment_id": "minecraft-judged-production-v4", "gate": "A",
        "run_id": "diagonal-s17-baseline_open", "lease_id": "a" * 64,
        "execution_revision": admission.FIXED_REVISION,
        "premanifest_canonical": admission.FIXED_PREMANIFEST_CANONICAL,
        "generation": 1, "registered_total": 1, "reaped_total": 0,
        "children": [{"pid": 10, "start_ticks": 20, "pgid": 10, "session_id": 10,
                      "role": "runtime_process_group", "state": "registered"}],
    }), "run_owned_child_present"),
])
def test_bounded_owned_state_diagnostic_mapping(clean, field, content, reason_code):
    paths, bindings, _ = clean
    target = Path(getattr(paths, field)); target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    result = admission.diagnostic_admission(paths, bindings)
    expected_phase = "destination_absence" if reason_code == "destination_exists" else "ownership_state"
    assert (result["phase_id"], result["reason_code"]) == (expected_phase, reason_code)


def test_unexpected_callback_failure_is_bounded(clean):
    paths, bindings, _ = clean
    broken = admission.AdmissionBindings(
        git=bindings.git, runtime=lambda: (_ for _ in ()).throw(ValueError("secret /absolute/path")),
        premanifest=bindings.premanifest, model=bindings.model, docker=bindings.docker,
    )
    result = admission.diagnostic_admission(paths, broken)
    encoded = json.dumps(result, sort_keys=True)
    assert (result["phase_id"], result["reason_code"]) == ("runtime_identity", "unexpected_failure")
    assert "secret" not in encoded and "/absolute/path" not in encoded
    assert len(encoded.encode()) < 1024


def test_phase_and_reason_allowlists_are_closed():
    assert admission.PHASE_IDS == frozenset({
        "source_authentication", "revision_worktree", "runtime_identity",
        "premanifest_identity", "model_inventory", "docker_identity",
        "managed_docker_residue", "destination_parent", "destination_absence",
        "ownership_state", "baseline_identity", "canary_derivation",
        "final_recheck", "admission_passed",
    })
    assert "unexpected_failure" in admission.REASON_CODES
    assert "none" in admission.REASON_CODES
    invalid = admission.OwnedAdmissionError("runtime_identity", "revision_mismatch")
    assert (invalid.phase_id, invalid.reason_code) == ("final_recheck", "unexpected_failure")


@pytest.mark.parametrize(("argument", "phase_id", "reason_code"), [
    ("execution-root", "revision_worktree", "worktree_dirty_or_not_detached"),
    ("premanifest", "premanifest_identity", "premanifest_mismatch"),
    ("private-parent", "destination_parent", "destination_symlink_or_alias"),
    ("docker-contract", "source_authentication", "source_hash_mismatch"),
    ("docker-executable", "docker_identity", "docker_identity_mismatch"),
])
def test_main_maps_argument_symlink_to_owning_phase(tmp_path, capsys, argument, phase_id, reason_code):
    root = tmp_path / "root"; root.mkdir()
    premanifest = tmp_path / "premanifest.json"; premanifest.write_text("{}")
    contract = tmp_path / "contract.py"; contract.write_text("pass\n")
    executable = tmp_path / "docker"; executable.write_text("binary")
    target = tmp_path / "target"; target.write_text("target")
    bad = tmp_path / "bad"; bad.symlink_to(target)
    values = {
        "execution-root": root, "premanifest": premanifest,
        "private-parent": Path("/tmp/opencode"), "docker-contract": contract,
        "docker-executable": executable,
    }
    values[argument] = bad
    argv = []
    for name, value in values.items():
        argv.extend([f"--{name}", str(value)])
    argv.append("--read-only-admission")
    assert admission.main(argv, _authority=admission._LAUNCH_AUTHORITY) == 3
    result = json.loads(capsys.readouterr().out)
    assert (result["phase_id"], result["reason_code"]) == (phase_id, reason_code)
    assert str(bad) not in json.dumps(result)


def test_admission_argument_errors_emit_only_bounded_json(capsys):
    assert admission.main(
        ["--unknown=/secret/absolute/path"], _authority=admission._LAUNCH_AUTHORITY,
    ) == 3
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.err == ""
    assert (result["phase_id"], result["reason_code"]) == (
        "source_authentication", "unexpected_failure",
    )
    assert "/secret/absolute/path" not in captured.out
