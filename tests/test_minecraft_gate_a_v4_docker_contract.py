from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import benchmarks.minecraft.gate_a_v4_docker_contract as contract


def test_environment_is_exact_and_preserves_model_credentials():
    expected = contract.endpoint()
    bound = contract.bind_environment({"MODEL_API_KEY": "keep", "DOCKER_HOST": expected})
    assert bound["MODEL_API_KEY"] == "keep"
    assert {key for key in bound if key.startswith("DOCKER_")} == {"DOCKER_HOST", "DOCKER_CONFIG"}
    assert bound["DOCKER_HOST"] == expected and bound["DOCKER_CONFIG"] == contract.DOCKER_CONFIG


@pytest.mark.parametrize("key,value", [
    ("DOCKER_HOST", "unix:///wrong"), ("DOCKER_CONTEXT", "desktop"),
    ("DOCKER_TLS", "1"), ("DOCKER_TLS_VERIFY", "1"),
    ("DOCKER_CERT_PATH", "/secret/certs"), ("DOCKER_CONFIG", "/secret/config"),
    ("DOCKER_API_VERSION", "1.1"), ("DOCKER_CUSTOM_HEADERS", "Authorization=secret"),
])
def test_unexpected_caller_docker_configuration_is_rejected(key, value):
    with pytest.raises(contract.DockerContractError, match="docker_environment_rejected"):
        contract.bind_environment({key: value})


def _fake_contract(monkeypatch, tmp_path, *, daemon="daemon-for-test", context="default",
                   ps="", socket_uid=4242, socket_mode=0o600, executable_bytes=b"trusted"):
    executable = tmp_path / "docker"
    executable.write_bytes(executable_bytes)
    monkeypatch.setattr(contract, "TRUSTED_STORE", str(tmp_path) + os.sep)
    monkeypatch.setattr(contract, "DOCKER_EXECUTABLE_SHA256", hashlib.sha256(b"trusted").hexdigest())
    monkeypatch.setattr(contract, "DAEMON_ID_SHA256", hashlib.sha256(b"daemon-for-test").hexdigest())
    monkeypatch.setattr(os, "geteuid", lambda: 4242)
    identities = {"socket_inode": 22, "executable_inode": 11}
    real_stat, real_lstat = os.stat, os.lstat

    def fake_stat(path, *args, **kwargs):
        if path == executable:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o500, st_uid=0, st_dev=1,
                                   st_ino=identities["executable_inode"])
        if str(path) == "/run/user/4242/docker.sock":
            return SimpleNamespace(st_mode=stat.S_IFSOCK | socket_mode, st_uid=socket_uid, st_dev=2,
                                   st_ino=identities["socket_inode"])
        return real_stat(path, *args, **kwargs)

    def fake_lstat(path, *args, **kwargs):
        if path == executable:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o500, st_uid=0, st_dev=1,
                                   st_ino=identities["executable_inode"])
        if str(path) == "/run/user/4242/docker.sock":
            return SimpleNamespace(st_mode=stat.S_IFSOCK | socket_mode, st_uid=socket_uid, st_dev=2,
                                   st_ino=identities["socket_inode"])
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", fake_stat)
    monkeypatch.setattr(os, "lstat", fake_lstat)
    outputs = [f"29.6.2|29.6.2|{context}|linux|amd64\n",
               f'{daemon}|["name=rootless"]\n', ps]
    monkeypatch.setattr(contract, "_run", lambda *args, **kwargs: outputs.pop(0))
    return executable, identities


def test_contract_report_is_sanitized_and_repeated_identity_matches(monkeypatch, tmp_path):
    executable, _ = _fake_contract(monkeypatch, tmp_path)
    first = contract.inspect_docker_contract(executable, {})
    executable, _ = _fake_contract(monkeypatch, tmp_path)
    second = contract.inspect_docker_contract(executable, {}, previous=first.identity)
    assert second.report == {
        "connection_category": "current_uid_rootless_unix_socket",
        "authorization_category": "current_uid_owner_read_write_no_world",
        "daemon_identity_category": "pinned_rootless_daemon",
        "executable_identity_category": "pinned_trusted_executable",
        "version_category": "client_server_pinned", "filtered_ps": "clean",
        "managed_container_count": 0, "same_daemon_identity": "matched",
    }
    encoded = json.dumps(second.report)
    assert all(secret not in encoded for secret in
               (str(executable), "/run/user", "docker.sock", "daemon-for-test", "DOCKER_HOST", "DOCKER_CONFIG"))


@pytest.mark.parametrize("kwargs,reason", [
    ({"context": "wrong"}, "docker_version_rejected"),
    ({"daemon": "wrong"}, "docker_daemon_rejected"),
    ({"ps": "va-mc-" + "a" * 32 + "\n"}, "docker_managed_present"),
    ({"socket_uid": 7}, "docker_socket_rejected"),
    ({"socket_mode": 0o606}, "docker_socket_rejected"),
    ({"executable_bytes": b"fake"}, "docker_executable_rejected"),
])
def test_wrong_context_daemon_managed_socket_and_binary_fail_closed(monkeypatch, tmp_path, kwargs, reason):
    executable, _ = _fake_contract(monkeypatch, tmp_path, **kwargs)
    with pytest.raises(contract.DockerContractError, match=reason):
        contract.inspect_docker_contract(executable, {})


def test_socket_and_executable_identity_drift_fail_closed(monkeypatch, tmp_path):
    executable, identities = _fake_contract(monkeypatch, tmp_path)
    first = contract.inspect_docker_contract(executable, {})
    executable, identities = _fake_contract(monkeypatch, tmp_path)
    identities["socket_inode"] += 1
    with pytest.raises(contract.DockerContractError, match="docker_identity_drift"):
        contract.inspect_docker_contract(executable, {}, previous=first.identity)


@pytest.mark.parametrize("failure", [PermissionError("secret socket"),
                                      ConnectionError("daemon unavailable"),
                                      subprocess.TimeoutExpired("docker", 30),
                                      subprocess.CalledProcessError(1, "docker", stderr="private")])
def test_client_permission_daemon_timeout_and_nonzero_fail_closed(monkeypatch, tmp_path, failure):
    executable = tmp_path / "docker"
    executable.write_bytes(b"trusted")
    monkeypatch.setattr(contract, "_socket_identity", lambda *args: (1, 2))
    monkeypatch.setattr(contract, "_executable_identity", lambda *args: (contract.DOCKER_EXECUTABLE_SHA256, 3, 4))
    monkeypatch.setattr(contract, "_bounded_command", lambda *args, **kwargs: (_ for _ in ()).throw(failure))
    with pytest.raises(contract.DockerContractError, match="docker_client_failure") as caught:
        contract.inspect_docker_contract(executable, {})
    assert all(secret not in str(caught.value) for secret in ("secret socket", "daemon unavailable", "private"))


def test_executor_prerequisite_calls_loaded_runtime_without_mutation():
    calls = []

    class Server:
        def __init__(self, root, runner=None):
            calls.append((root, runner))

        def verify_image(self):
            return "sha256:image"

    runtime = SimpleNamespace(DockerServer=Server, PINNED_IMAGE_DIGEST="sha256:image")
    assert contract.verify_executor_prerequisite(runtime) == "pinned_runtime_image_matched"
    assert calls == [(Path("/nonexistent/va-minecraft-admission"), None)]


def test_bounded_command_rejects_output_before_unbounded_retention():
    with pytest.raises(contract.DockerContractError, match="docker_output_rejected"):
        contract._bounded_command(
            [sys.executable, "-c", f"import sys;sys.stdout.buffer.write(b'x'*{contract.OUTPUT_LIMIT + 1})"],
            os.environ, timeout=5)


def test_bound_executor_runner_rechecks_identity_and_uses_fixed_client(monkeypatch, tmp_path):
    executable = tmp_path / "docker"
    identity = contract.DockerIdentity("d", "e", "c", "s", "default", "linux/amd64", 1, 2, 3, 4)
    observed, executed = [], []
    monkeypatch.setattr(contract, "inspect_docker_contract",
                        lambda exe, env, previous, require_clean: observed.append((exe, previous, require_clean)))
    monkeypatch.setattr(contract, "_bounded_command",
                        lambda argv, env, **kwargs: executed.append((argv, dict(env), kwargs))
                        or subprocess.CompletedProcess(argv, 0, "", ""))
    env = {"DOCKER_HOST": contract.endpoint(), "DOCKER_CONFIG": contract.DOCKER_CONFIG}
    runner = contract.make_bound_runner(executable, identity, env)
    runner(["docker", "image", "inspect", "fixed"], capture_output=True, text=True, timeout=7, check=False)
    runner(["docker", "logs", "fixed"], capture_output=True, text=False, timeout=7, check=False)
    assert observed == [(executable, identity, False), (executable, identity, False)]
    assert executed[0][0] == [str(executable), "image", "inspect", "fixed"]
    assert executed[1][2]["text"] is False
    assert {key for key in executed[0][1] if key.startswith("DOCKER_")} == {"DOCKER_HOST", "DOCKER_CONFIG"}
    with pytest.raises(contract.DockerContractError, match="docker_executor_command_rejected"):
        runner(["evil", "ps"])
