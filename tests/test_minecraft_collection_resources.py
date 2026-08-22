import subprocess
from dataclasses import replace

import pytest

from benchmarks.minecraft.collection_resources import (
    CollectionResourceError,
    ObservedResourceFingerprint,
    admit_worktrees,
    docker_identity,
    model_identity,
    observed_lane_resources,
    path_fingerprint,
    resources_conflict,
)
from benchmarks.minecraft.approved_experiment import ApprovedExperiment, ArtifactReference
from benchmarks.minecraft.collection_spec import parse_collection_plan


def approval_record(experiment_id, revision):
    return ApprovedExperiment(1, experiment_id, revision, "premanifest", {},
                              "https://example.test", ArtifactReference("gist", "owner", "id", "r", "p", "0" * 64), {})


def admit(lane, **kwargs):
    def loader(experiment_id, _registry_dir):
        return approval_record(experiment_id, lane.execution_revision)

    return admit_worktrees(lane, approval_loader=loader, **kwargs)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    control, execution = tmp_path / "control", tmp_path / "execution"
    control.mkdir()
    git(control, "init")
    (control / "tracked").write_text("ok")
    git(control, "add", "tracked")
    git(control, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial")
    revision = git(control, "rev-parse", "HEAD").stdout.strip()
    git(control, "worktree", "add", str(execution), revision)
    lane = parse_collection_plan({
        "schema_version": 1, "session_id": "session", "max_parallel_lanes": 1,
        "lanes": [{"lane_id": "lane", "kind": "approved-production", "data_class": "research", "approved_experiment": "exp",
                   "control_plane_worktree": str(control), "control_plane_revision": revision,
                   "execution_worktree": str(execution), "execution_revision": revision,
                   "python_executable": "/usr/bin/python", "output_root": str(tmp_path / "output"),
                   "batch_count": 1, "batch_timeout_seconds": 1,
                   "environment": {"docker_host_env": "DOCKER_HOST", "docker_context_env": None,
                                   "model_api_base_env": "MODEL_URL", "model_api_key_env": "MODEL_KEY",
                   "lock_root": str(tmp_path / "locks")}, "resource_groups": ["docker:lane"]}]
    }).lanes[0]
    return control, execution, revision, lane


def test_clean_pair_and_control_untracked_are_admitted(repo):
    control, execution, revision, lane = repo
    (control / "notes").write_text("allowed")
    (control / ".gitignore").write_text("ignored-notes\n")
    git(control, "add", ".gitignore")
    git(control, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "ignore local notes")
    revision = git(control, "rev-parse", "HEAD").stdout.strip()
    git(execution, "switch", "--detach", revision)
    lane = replace(lane, control_plane_revision=revision, execution_revision=revision)
    (control / "ignored-notes").write_text("allowed")
    result = admit(lane)
    assert result.control.revision == revision
    assert result.control.untracked_count == 1
    assert result.control.ignored_count == 1
    assert result.execution.untracked_count == 0


def test_tracked_drift_and_execution_untracked_are_rejected(repo):
    control, execution, revision, lane = repo
    (control / "tracked").write_text("changed")
    with pytest.raises(CollectionResourceError):
        admit(lane)
    (control / "tracked").write_text("ok")
    (execution / "untracked").write_text("not allowed")
    with pytest.raises(CollectionResourceError):
        admit(lane)


def test_attached_execution_and_wrong_separate_revision_are_rejected(repo):
    control, execution, revision, lane = repo
    git(execution, "switch", "-c", "attached")
    with pytest.raises(CollectionResourceError):
        admit(lane)
    with pytest.raises(CollectionResourceError):
        admit(replace(lane, execution_revision="0" * 40))
    git(execution, "switch", "--detach", revision)
    changed = control / "changed"
    changed.write_text("revision")
    git(control, "add", "changed")
    git(control, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "drift")
    with pytest.raises(CollectionResourceError):
        admit(lane)


def test_subdirectory_symlink_and_unrelated_repository_rejected(repo, tmp_path):
    control, execution, revision, lane = repo
    sub = control / "sub"
    sub.mkdir()
    lane = replace(lane, control_plane_worktree=str(sub))
    with pytest.raises(CollectionResourceError):
        admit(lane)
    link = tmp_path / "link"
    link.symlink_to(control, target_is_directory=True)
    lane = replace(lane, control_plane_worktree=str(link))
    with pytest.raises(CollectionResourceError):
        admit(lane)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    git(unrelated, "init")
    (unrelated / "tracked").write_text("unrelated")
    git(unrelated, "add", "tracked")
    git(unrelated, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "unrelated")
    unrelated_revision = git(unrelated, "rev-parse", "HEAD").stdout.strip()
    unrelated_lane = replace(
        lane,
        control_plane_worktree=str(control),
        execution_worktree=str(unrelated),
        execution_revision=unrelated_revision,
    )
    with pytest.raises(CollectionResourceError):
        admit(unrelated_lane)


def test_docker_model_privacy_and_resource_conflicts():
    info = {"ID": "secret-daemon", "ServerVersion": "26", "OperatingSystem": "Linux",
            "Architecture": "amd64", "Rootless": True, "DockerRootDir": "/home/user/.docker"}
    one = docker_identity(info, "default")
    alias = docker_identity({**info, "ServerVersion": "other"}, "alias")
    assert one.fingerprint == alias.fingerprint
    assert all(value not in repr(one) for value in ("secret-daemon", "DockerRootDir", "/home/user"))
    model = model_identity("provider", "model", "sha256:abc", "HTTPS://Example.test:443/api")
    assert "secret" not in repr(model)
    with pytest.raises(CollectionResourceError):
        model_identity("p", "m", "d", "https://user:password@example.test/?token=secret")
    with pytest.raises(CollectionResourceError):
        model_identity("p", "m", "d", "https://example.test:bad")
    left = ObservedResourceFingerprint(frozenset({"a:b"}), one.fingerprint, model.endpoint_fingerprint, "a" * 64, "b" * 64)
    right = ObservedResourceFingerprint(frozenset({"b:c"}), one.fingerprint, model.endpoint_fingerprint, "c" * 64, "d" * 64)
    different = ObservedResourceFingerprint(frozenset({"c:d"}), "0" * 64, "1" * 64, "e" * 64, "f" * 64)
    assert resources_conflict(left, right)
    assert not resources_conflict(left, different)


def test_observed_resources_are_immutable_and_worktree_tokens_unified(repo, tmp_path):
    control, execution, revision, lane = repo
    execution_two = tmp_path / "execution-two"
    git(control, "worktree", "add", str(execution_two), revision)
    first = admit(lane)
    second = admit(replace(lane, execution_worktree=str(execution_two)))
    a = ObservedResourceFingerprint(frozenset({"a:b"}), execution_worktree_fingerprint=first.execution.fingerprint)
    b = ObservedResourceFingerprint(frozenset({"c:d"}), execution_worktree_fingerprint=second.execution.fingerprint)
    assert first.execution.fingerprint != second.execution.fingerprint
    assert not resources_conflict(a, b)
    reused = ObservedResourceFingerprint(frozenset({"c:d"}), control_worktree_fingerprint=first.execution.fingerprint)
    assert resources_conflict(a, reused)
    with pytest.raises(CollectionResourceError):
        ObservedResourceFingerprint({"mutable"})
    with pytest.raises(CollectionResourceError):
        ObservedResourceFingerprint(frozenset({""}))
    output = path_fingerprint(str(tmp_path / "out"))
    alias = path_fingerprint(str(tmp_path / "out"))
    assert resources_conflict(ObservedResourceFingerprint(frozenset({"x:y"}), output_root_fingerprint=output),
                              ObservedResourceFingerprint(frozenset({"z:w"}), output_root_fingerprint=alias))


def test_observed_lane_resources_combines_private_and_observed_resources(repo):
    _, _, _, lane = repo
    admitted = admit(lane)
    docker = docker_identity({"ID": "daemon", "ServerVersion": "1", "OperatingSystem": "Linux",
                              "Architecture": "amd64", "Rootless": False}, "ctx")
    model = model_identity("p", "m", "d", "https://example.test")
    observed = observed_lane_resources(lane, admitted, docker=docker, model=model)
    assert resources_conflict(observed, observed)
    assert str(lane.output_root) not in repr(observed)
    assert str(lane.environment.lock_root) not in repr(observed)
    assert resources_conflict(observed, ObservedResourceFingerprint(
        frozenset({"other:lane"}), docker.fingerprint, model.endpoint_fingerprint,
        admitted.execution.fingerprint, admitted.control.fingerprint,
        observed.output_root_fingerprint, observed.lock_root_fingerprint))


def test_path_fingerprint_rejects_symlinks_and_invalid_groups(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(CollectionResourceError):
        path_fingerprint(str(link))
    with pytest.raises(CollectionResourceError):
        ObservedResourceFingerprint(frozenset({"not-namespaced"}))
    with pytest.raises(CollectionResourceError):
        path_fingerprint("/")


def test_symbolic_ref_inspection_fails_closed_on_unexpected_runner_status(repo):
    _, _, _, lane = repo

    def runner(argv, **kwargs):
        result = subprocess.run(argv, **kwargs)
        if argv[-4:-1] == ["symbolic-ref", "--quiet", "--short"]:
            return subprocess.CompletedProcess(argv, 2, result.stdout, result.stderr)
        return result

    with pytest.raises(CollectionResourceError):
        admit(lane, runner=runner)


def test_approval_is_checked_before_git_and_lane_binding_is_exact(repo):
    _, _, _, lane = repo
    called = False

    def no_git(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("Git must not run for unknown approval")

    def unknown(_experiment, _registry):
        raise RuntimeError("unknown")

    with pytest.raises(CollectionResourceError):
        admit_worktrees(
            replace(lane, kind="externally-managed"),
            runner=no_git,
            approval_loader=unknown,
        )
    assert not called
    with pytest.raises(CollectionResourceError):
        admit_worktrees(lane, runner=no_git, approval_loader=unknown)
    assert not called
    with pytest.raises(CollectionResourceError):
        admit_worktrees(
            lane,
            runner=no_git,
            approval_loader=lambda experiment, _registry: approval_record(
                experiment, "0" * 40
            ),
        )
    assert not called
    admitted = admit(lane)
    with pytest.raises(CollectionResourceError):
        observed_lane_resources(replace(lane, lane_id="other-lane"), admitted)
