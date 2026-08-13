import json
import os
import pytest
import subprocess
from types import SimpleNamespace

from benchmarks.minecraft import gate_a_v4_managed_docker as managed


PINNED_IMAGE = "docker.io/itzg/minecraft-server@sha256:5b3e96bcd7dace8ab7be89c245dc9ba0b1573fdef2f01d3e101ac40e7843fa70"


def _binding():
    return {
        "experiment_id": "minecraft-judged-production-v4", "gate": "A",
        "run_id": "diagonal-s17-baseline_open", "lease_id": "a" * 64,
    }


def _bind(runner, probe=lambda: 0):
    root = "/tmp/opencode/" + managed._NAMESPACE_NAME
    os.makedirs(root, mode=0o700, exist_ok=True)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    handle = SimpleNamespace(namespace_fd=descriptor)
    capability = managed.bind_managed_docker(runner, _binding(), probe, handle=handle)
    capability._test_descriptor = descriptor
    return capability


def _identity_stdout(container_id="container-id"):
    labels = {
        "org.villageragent.minecraft.managed": "true",
        "org.villageragent.experiment": "minecraft-judged-production-v4",
        "org.villageragent.gate": "A",
        "org.villageragent.run": "diagonal-s17-baseline_open",
        "org.villageragent.lease": "a" * 64,
    }
    return json.dumps(container_id) + "|" + json.dumps(labels)


def _create(name="va-mc-owned"):
    root = "/tmp/opencode/" + managed._NAMESPACE_NAME
    os.makedirs(root + "/work", mode=0o700, exist_ok=True)
    return [
        "docker", "create", "--name", name,
        "--mount", f"type=bind,src={root}/work,dst=/data", "-p", "127.0.0.1::25565",
        *(item for value in sorted(managed._FIXED_ENV) for item in ("-e", value)), PINNED_IMAGE,
    ]


def test_stateful_capability_labels_tracks_and_cleans_only_owned_container():
    calls = []
    live = {"unrelated"}

    def raw(argv, **kwargs):
        calls.append((argv, kwargs))
        operation = argv[1]
        if operation == "create":
            live.add(argv[argv.index("--name") + 1])
            return subprocess.CompletedProcess(argv, 0, stdout="container-id\n")
        elif operation == "inspect" and "{{json .Id}}" in " ".join(argv):
            return subprocess.CompletedProcess(argv, 0, stdout=_identity_stdout())
        elif operation == "rm":
            live.discard("va-mc-owned")
        return subprocess.CompletedProcess(argv, 0, stdout="")

    capability = _bind(raw, lambda: len([name for name in live if name.startswith("va-mc-owned")]))
    capability.executor_runner(_create())
    capability.executor_runner(["docker", "start", "va-mc-owned"])
    assert capability.owned_count == 1
    encoded = " ".join(calls[0][0])
    assert "org.villageragent.lease=" + "a" * 64 in encoded
    capability.cleanup_owned()
    proof = capability.prove_clean()
    assert proof.managed_containers == 0
    assert live == {"unrelated"}


@pytest.mark.parametrize("command", [
    ["docker", "stop", "unrelated"],
    ["docker", "rm", "-f", "unrelated"],
    ["docker", "ps", "-a"],
    ["docker", "events"],
])
def test_stateful_capability_rejects_unowned_targets_and_broad_queries(command):
    capability = _bind(lambda argv, **kwargs: None)
    with pytest.raises(managed.ManagedDockerError):
        capability.executor_runner(command)


@pytest.mark.parametrize("arguments", [
    ["--privileged"], ["--network=host"], ["--device", "/dev/kvm"],
    ["--mount", "type=bind,src=/tmp/unowned,dst=/data"],
    ["--network", "host"], ["--pid", "host"], ["-v", "/:/host"],
    ["--volume", "/:/host"], ["--volumes-from", "unrelated"],
])
def test_stateful_capability_rejects_dangerous_creation_options(arguments):
    capability = _bind(lambda argv, **kwargs: None)
    with pytest.raises(managed.ManagedDockerError):
        capability.executor_runner([
            "docker", "create", "--name", "va-mc-owned", *arguments, PINNED_IMAGE,
        ])


def test_create_requires_fixed_mount_and_environment_shape():
    capability = _bind(lambda argv, **kwargs: None)
    with pytest.raises(managed.ManagedDockerError, match="creation shape rejected"):
        capability.executor_runner(["docker", "create", "--name", "va-mc-owned", PINNED_IMAGE])
    command = _create()
    command[command.index("EULA=TRUE")] = "EULA=FALSE"
    with pytest.raises(managed.ManagedDockerError, match="creation shape rejected"):
        capability.executor_runner(command)


def test_stateful_capability_residue_blocks_clean_proof():
    capability = _bind(lambda argv, **kwargs: None, lambda: 1)
    with pytest.raises(managed.ManagedDockerError):
        capability.prove_clean()


def test_stateful_capability_rejects_mixed_owned_and_unrelated_targets():
    capability = _bind(lambda argv, **kwargs: None)
    capability.executor_runner(_create())
    with pytest.raises(managed.ManagedDockerError):
        capability.executor_runner(["docker", "rm", "-f", "va-mc-owned", "unrelated"])


def test_uncertain_create_is_retained_but_never_deleted_without_ownership_proof():
    calls = []
    fail_create = True

    def raw(argv, **kwargs):
        nonlocal fail_create
        calls.append(argv)
        if argv[1] == "create" and fail_create:
            fail_create = False
            raise TimeoutError

    capability = _bind(raw)
    with pytest.raises(TimeoutError):
        capability.executor_runner(_create())
    assert capability.owned_count == 1
    with pytest.raises(managed.ManagedDockerError, match="ownership uncertain"):
        capability.cleanup_owned()
    assert not any(argv[1] in {"stop", "rm"} for argv in calls)
    assert capability.owned_count == 1


def test_fixed_runtime_helper_run_shape_gets_owned_name_and_labels():
    calls = []
    capability = _bind(lambda argv, **kwargs: calls.append(argv))
    root = "/tmp/opencode/" + managed._NAMESPACE_NAME
    os.makedirs(root + "/work", mode=0o700, exist_ok=True)
    capability.executor_runner([
        "docker", "run", "--rm", "--user", "0:0", "--entrypoint", "chown",
        "--mount", "type=bind,src=/tmp/opencode/" + managed._NAMESPACE_NAME + "/work,dst=/data",
        PINNED_IMAGE, "-R", "0:0", "/data",
    ])
    encoded = " ".join(calls[0])
    assert "--name va-mc-helper-" in encoded
    assert "org.villageragent.lease=" + "a" * 64 in encoded
    assert capability.owned_count == 0
    assert capability.prove_clean().managed_containers == 0
    with pytest.raises(managed.ManagedDockerError, match="name rejected"):
        capability.executor_runner([
            "docker", "run", "--rm", "--name", "va-mc-helper-" + "a" * 16,
            "--user", "0:0", "--entrypoint", "chown",
            "--mount", "type=bind,src=/tmp/opencode/" + managed._NAMESPACE_NAME + "/work,dst=/data",
            PINNED_IMAGE, "-R", "0:0", "/data",
        ])


def test_fixed_runtime_helper_rejects_symlinked_work_directory(tmp_path):
    capability = _bind(lambda argv, **kwargs: None)
    root = "/tmp/opencode/" + managed._NAMESPACE_NAME
    work = root + "/work"
    if os.path.lexists(work):
        os.rmdir(work)
    os.symlink(tmp_path, work)
    try:
        with pytest.raises(managed.ManagedDockerError, match="mount rejected"):
            capability.executor_runner([
                "docker", "run", "--rm", "--user", "0:0", "--entrypoint", "chown",
                "--mount", f"type=bind,src={work},dst=/data",
                PINNED_IMAGE, "-R", "0:0", "/data",
            ])
    finally:
        os.unlink(work)


def test_retained_work_identity_rejects_directory_replacement():
    calls = []
    capability = _bind(lambda argv, **kwargs: calls.append(argv))
    root = "/tmp/opencode/" + managed._NAMESPACE_NAME
    work = root + "/work"
    os.makedirs(work, mode=0o700, exist_ok=True)
    command = [
        "docker", "run", "--rm", "--user", "0:0", "--entrypoint", "chown",
        "--mount", f"type=bind,src={work},dst=/data",
        PINNED_IMAGE, "-R", "0:0", "/data",
    ]
    capability.executor_runner(command)
    os.rmdir(work)
    os.mkdir(work, mode=0o700)
    try:
        with pytest.raises(managed.ManagedDockerError, match="mount rejected"):
            capability.executor_runner(["docker", "image", "inspect", PINNED_IMAGE])
    finally:
        os.rmdir(work)


def test_runner_receives_descriptor_backed_mount_when_path_is_replaced():
    root = "/tmp/opencode/" + managed._NAMESPACE_NAME
    work = root + "/work"
    os.makedirs(work, mode=0o700, exist_ok=True)
    original = os.stat(work)

    def raw(argv, **kwargs):
        del kwargs
        mount = argv[argv.index("--mount") + 1]
        source = next(field.split("=", 1)[1] for field in mount.split(",") if field.startswith("src="))
        os.rmdir(work)
        os.mkdir(work, mode=0o700)
        authenticated = os.stat(source)
        assert (authenticated.st_dev, authenticated.st_ino) == (original.st_dev, original.st_ino)
        assert os.stat(work).st_ino != original.st_ino

    capability = _bind(raw)
    try:
        capability.executor_runner([
            "docker", "run", "--rm", "--user", "0:0", "--entrypoint", "chown",
            "--mount", f"type=bind,src={work},dst=/data",
            PINNED_IMAGE, "-R", "0:0", "/data",
        ])
    finally:
        os.rmdir(work)


def test_successful_executor_rm_retires_owned_name():
    def raw(argv, **kwargs):
        if argv[1] == "create":
            return subprocess.CompletedProcess(argv, 0, stdout="container-id\n")
        if argv[1] == "inspect" and "{{json .Id}}" in " ".join(argv):
            return subprocess.CompletedProcess(argv, 0, stdout=_identity_stdout())
        return subprocess.CompletedProcess(argv, 1 if argv[1] == "inspect" else 0, stdout="")

    capability = _bind(raw)
    capability.executor_runner(_create())
    capability.executor_runner(["docker", "rm", "-f", "va-mc-owned"])
    assert capability.owned_count == 0
    with pytest.raises(managed.ManagedDockerError, match="name rejected"):
        capability.executor_runner(_create())
    capability.executor_runner(["docker", "inspect", "va-mc-owned"], check=False)
    with pytest.raises(managed.ManagedDockerError, match="name rejected"):
        capability.executor_runner(_create())
    assert capability.prove_clean().managed_containers == 0


def test_same_name_replacement_is_never_deleted():
    calls = []

    def raw(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "create":
            return subprocess.CompletedProcess(argv, 0, stdout="owned-id\n")
        if argv[1] == "inspect":
            return subprocess.CompletedProcess(argv, 0, stdout=_identity_stdout("replacement-id"))
        raise AssertionError("destructive command must not run")

    capability = _bind(raw)
    capability.executor_runner(_create())
    with pytest.raises(managed.ManagedDockerError, match="ownership uncertain"):
        capability.cleanup_owned()
    assert not any(argv[1] in {"stop", "rm"} for argv in calls)


def test_known_create_conflict_never_becomes_cleanup_authority():
    calls = []

    def raw(argv, **kwargs):
        calls.append(argv)
        raise subprocess.CalledProcessError(1, argv)

    capability = _bind(raw)
    with pytest.raises(subprocess.CalledProcessError):
        capability.executor_runner(_create("va-mc-conflict"))
    capability.cleanup_owned()
    assert not any(argv[1] in {"stop", "rm"} for argv in calls)


def test_uncertain_create_blocks_without_deleting_target():
    calls = []

    def raw(argv, **kwargs):
        calls.append(argv)
        raise TimeoutError

    capability = _bind(raw)
    with pytest.raises(TimeoutError):
        capability.executor_runner(_create("va-mc-uncertain"))
    with pytest.raises(managed.ManagedDockerError, match="ownership uncertain"):
        capability.cleanup_owned()
    assert not any(argv[1] in {"stop", "rm"} for argv in calls)


def test_queries_require_complete_exact_owned_filters():
    calls = []
    capability = _bind(lambda argv, **kwargs: calls.append(argv))
    capability.executor_runner(_create())
    capability.executor_runner([
        "docker", "ps", "-a", "--filter", "name=^/va-mc-owned$", "--format", "{{.Names}}",
    ])
    with pytest.raises(managed.ManagedDockerError):
        capability.executor_runner([
            "docker", "ps", "-a", "--filter", "name=^/va-mc-owned$",
            "--filter", "name=^/unrelated$", "--format", "{{.Names}}",
        ])
