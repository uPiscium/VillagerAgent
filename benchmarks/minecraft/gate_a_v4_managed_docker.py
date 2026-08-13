"""Future Gate A runner wrapper that binds Docker resources to owned state."""
from __future__ import annotations

import re
import subprocess
import os
import stat
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable


_LABELS = {
    "org.villageragent.minecraft.managed": "true",
    "org.villageragent.experiment": "minecraft-judged-production-v4",
    "org.villageragent.gate": "A",
    "org.villageragent.run": "diagonal-s17-baseline_open",
}
_NAME = re.compile(r"va-mc-[a-z0-9][a-z0-9_.-]{0,119}\Z")
_DANGEROUS = ("--privileged", "--network=host", "--pid=host", "--ipc=host",
              "--cap-add", "--device", "/var/run/docker.sock", "docker.sock")
_PROOF_AUTHORITY = object()
_PINNED_IMAGE = "docker.io/itzg/minecraft-server@sha256:5b3e96bcd7dace8ab7be89c245dc9ba0b1573fdef2f01d3e101ac40e7843fa70"
_NAMESPACE_NAME = ".villageragent.minecraft-judged-production-v4.gate-a.diagonal-s17-baseline_open"
_FIXED_ENV = frozenset({
    "EULA=TRUE", "TYPE=VANILLA", "VERSION=1.19.2", "ONLINE_MODE=FALSE",
    "MEMORY=2G", "UID=0", "GID=0", "SPAWN_PROTECTION=0", "ENABLE_RCON=true",
})


class ManagedDockerError(RuntimeError):
    pass


@dataclass(frozen=True)
class DockerCleanProof:
    managed_containers: int
    _authority: object


class ManagedDockerCapability:
    """Stateful lease-owned Docker boundary; raw runner is never exposed."""

    def __init__(self, runner: Callable, binding: dict, residue_probe: Callable[[], int], *, handle):
        required = {"experiment_id", "gate", "run_id", "lease_id"}
        if (
            not callable(runner) or not callable(residue_probe)
            or not isinstance(binding, dict) or not required.issubset(binding)
            or binding.get("experiment_id") != "minecraft-judged-production-v4"
            or binding.get("gate") != "A"
            or binding.get("run_id") != "diagonal-s17-baseline_open"
            or not isinstance(binding.get("lease_id"), str) or len(binding["lease_id"]) != 64
            or any(character not in "0123456789abcdef" for character in binding["lease_id"])
            or not hasattr(handle, "namespace_fd")
        ):
            raise ManagedDockerError("managed Docker capability rejected")
        try:
            root = PurePosixPath(os.readlink(f"/proc/self/fd/{handle.namespace_fd}"))
        except OSError:
            raise ManagedDockerError("managed Docker handle rejected") from None
        if not root.is_absolute() or root.name != _NAMESPACE_NAME:
            raise ManagedDockerError("managed Docker data root rejected")
        self.__runner = runner
        self.__probe = residue_probe
        self.__labels = {**_LABELS, "org.villageragent.lease": binding["lease_id"]}
        self.__owned = set()
        self.__owned_ids = {}
        self.__retired = set()
        self.__uncertain = set()
        self.__consumed_names = set()
        self.__data_root = root
        self.__handle = handle
        self.__mount_fd = None
        self.__mount_identity = None
        self.__runner_view = self.__run

    @property
    def executor_runner(self):
        return self.__runner_view

    @property
    def owned_count(self):
        return len(self.__owned)

    @staticmethod
    def _operation(argv):
        if not isinstance(argv, list) or len(argv) < 2 or argv[0] != "docker":
            raise ManagedDockerError("managed Docker command rejected")
        if argv[1] == "container":
            if len(argv) < 3:
                raise ManagedDockerError("managed Docker command rejected")
            return argv[2], 3
        return argv[1], 2

    @staticmethod
    def _creation_name(arguments):
        names = []
        for index, item in enumerate(arguments):
            if item == "--name" and index + 1 < len(arguments):
                names.append(arguments[index + 1])
            elif item.startswith("--name="):
                names.append(item.split("=", 1)[1])
        if len(names) != 1 or not _NAME.fullmatch(names[0]):
            raise ManagedDockerError("managed Docker name rejected")
        return names[0]

    def _validate_mount(self, value):
        fields = {}
        for item in value.split(","):
            if "=" not in item:
                raise ManagedDockerError("managed Docker mount rejected")
            key, field_value = item.split("=", 1)
            if key in fields:
                raise ManagedDockerError("managed Docker mount rejected")
            fields[key] = field_value
        if set(fields) != {"type", "src", "dst"} or fields["type"] != "bind" or fields["dst"] != "/data":
            raise ManagedDockerError("managed Docker mount rejected")
        source = PurePosixPath(fields["src"])
        root = self.__data_root
        try:
            expected = os.fstat(self.__handle.namespace_fd)
            observed = os.stat(root, follow_symlinks=False)
        except OSError:
            raise ManagedDockerError("managed Docker namespace drift") from None
        if (observed.st_dev, observed.st_ino, observed.st_uid) != (expected.st_dev, expected.st_ino, expected.st_uid):
            raise ManagedDockerError("managed Docker namespace drift")
        try:
            relative = source.relative_to(root)
            if relative != PurePosixPath("work"):
                raise ValueError
            descriptor = os.open(
                str(relative),
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=self.__handle.namespace_fd,
            )
            opened = os.fstat(descriptor)
            source_info = os.stat(source, follow_symlinks=False)
            identity = (opened.st_dev, opened.st_ino, opened.st_uid)
            if not stat.S_ISDIR(source_info.st_mode) or identity != (
                source_info.st_dev, source_info.st_ino, source_info.st_uid
            ):
                raise OSError
            if self.__mount_identity is None:
                self.__mount_fd = descriptor
                self.__mount_identity = identity
                descriptor = None
            elif identity != self.__mount_identity:
                raise OSError
        except ValueError:
            raise ManagedDockerError("managed Docker mount rejected")
        except OSError:
            raise ManagedDockerError("managed Docker mount rejected") from None
        finally:
            if "descriptor" in locals() and descriptor is not None:
                os.close(descriptor)
        return f"type=bind,src=/proc/{os.getpid()}/fd/{self.__mount_fd},dst=/data"

    def _verify_retained_mount(self):
        if self.__mount_fd is None:
            return
        try:
            retained = os.fstat(self.__mount_fd)
            current = os.stat(self.__data_root / "work", follow_symlinks=False)
        except OSError:
            raise ManagedDockerError("managed Docker mount rejected") from None
        identity = (current.st_dev, current.st_ino, current.st_uid)
        if (
            not stat.S_ISDIR(current.st_mode)
            or identity != self.__mount_identity
            or identity != (retained.st_dev, retained.st_ino, retained.st_uid)
        ):
            raise ManagedDockerError("managed Docker mount rejected")

    def _creation_shape(self, operation, arguments):
        allowed_values = {"--name", "--mount", "-p", "-e"}
        if operation == "run":
            allowed_values |= {"--user", "--entrypoint"}
        allowed_flags = {"--rm"} if operation == "run" else set()
        index = 0
        image_index = None
        mounts = 0
        environment = []
        ports = 0
        users = 0
        entrypoints = 0
        remove_flags = 0
        while index < len(arguments):
            item = arguments[index]
            if item == _PINNED_IMAGE:
                image_index = index
                break
            if item in allowed_flags:
                remove_flags += 1
                index += 1
                continue
            if item in allowed_values:
                if index + 1 >= len(arguments):
                    raise ManagedDockerError("managed Docker option rejected")
                value = str(arguments[index + 1])
                if item == "--mount":
                    arguments[index + 1] = self._validate_mount(value)
                    mounts += 1
                if item == "-e":
                    environment.append(value)
                if item == "-p" and value != "127.0.0.1::25565":
                    raise ManagedDockerError("managed Docker option rejected")
                if item == "-p":
                    ports += 1
                if item == "--user" and value != "0:0":
                    raise ManagedDockerError("managed Docker option rejected")
                if item == "--user":
                    users += 1
                if item == "--entrypoint" and value != "chown":
                    raise ManagedDockerError("managed Docker option rejected")
                if item == "--entrypoint":
                    entrypoints += 1
                index += 2
                continue
            if item.startswith("--name="):
                index += 1
                continue
            raise ManagedDockerError("managed Docker option rejected")
        if image_index is None:
            raise ManagedDockerError("managed Docker image rejected")
        if operation == "create" and (mounts != 1 or ports != 1 or frozenset(environment) != _FIXED_ENV
                                       or len(environment) != len(_FIXED_ENV)):
            raise ManagedDockerError("managed Docker creation shape rejected")
        if operation == "run" and (mounts != 1 or remove_flags != 1 or users != 1
                                    or entrypoints != 1 or ports or environment):
            raise ManagedDockerError("managed Docker helper shape rejected")
        command = arguments[image_index + 1:]
        if operation == "create" and command:
            raise ManagedDockerError("managed Docker command rejected")
        if operation == "run" and command != ["-R", "0:0", "/data"]:
            raise ManagedDockerError("managed Docker command rejected")
        return image_index

    @staticmethod
    def _targets(operation, arguments):
        option_values = {
            "stop": {"--time"}, "restart": {"--time"},
            "inspect": {"--format", "--type"}, "logs": {"--tail"},
        }.get(operation, set())
        flags = {"-f"} if operation == "rm" else set()
        positional = []
        index = 0
        while index < len(arguments):
            item = str(arguments[index])
            if item in option_values:
                index += 2
                continue
            if item in flags:
                index += 1
                continue
            if item.startswith("-"):
                raise ManagedDockerError("managed Docker option rejected")
            positional.append(item)
            index += 1
        if operation in {"exec", "port"}:
            return positional[:1]
        return positional

    def _validate_query(self, operation, arguments):
        value_options = {"--filter", "--format"}
        if operation == "events":
            value_options |= {"--since", "--until"}
        flags = {"-a"} if operation == "ps" else set()
        filters = []
        index = 0
        while index < len(arguments):
            item = str(arguments[index])
            if item in flags:
                index += 1
                continue
            if item in value_options:
                if index + 1 >= len(arguments):
                    raise ManagedDockerError("managed Docker query rejected")
                if item == "--filter":
                    filters.append(str(arguments[index + 1]))
                index += 2
                continue
            if item.startswith("--filter="):
                filters.append(item.split("=", 1)[1])
                index += 1
                continue
            raise ManagedDockerError("managed Docker query rejected")
        queryable = self.__owned | self.__retired
        exact = {
            *("container=" + name for name in queryable),
            *("name=^/" + name + "$" for name in queryable),
        }
        if not filters or not any(value in exact for value in filters):
            raise ManagedDockerError("managed Docker query rejected")
        if any(value not in exact and value != "type=container" for value in filters):
            raise ManagedDockerError("managed Docker query rejected")

    def __run(self, argv, **kwargs):
        operation, prefix = self._operation(argv)
        arguments = argv[prefix:]
        encoded = " ".join(str(item) for item in arguments)
        if any(value in encoded for value in _DANGEROUS):
            raise ManagedDockerError("managed Docker option rejected")
        if operation in {"create", "run"}:
            if any(
                item in {"--label", "-l", "--label-file"}
                or item.startswith(("--label=", "--label-file=", "-l="))
                or (item.startswith("-l") and len(item) > 2)
                for item in arguments
            ):
                raise ManagedDockerError("managed Docker labels rejected")
            image_index = self._creation_shape(operation, arguments)
            if operation == "run" and not any(
                item == "--name" or str(item).startswith("--name=") for item in arguments[:image_index]
            ):
                helper_name = "va-mc-helper-" + self.__labels["org.villageragent.lease"][:16]
                arguments = ["--name", helper_name, *arguments]
            name = self._creation_name(arguments)
            if (
                name in self.__owned or name in self.__retired
                or name in self.__uncertain or name in self.__consumed_names
            ):
                raise ManagedDockerError("managed Docker name rejected")
            injected = argv[:prefix]
            for key, value in sorted(self.__labels.items()):
                injected.extend(["--label", f"{key}={value}"])
            injected.extend(arguments)
            self.__owned.add(name)
            try:
                self._verify_retained_mount()
                result = self.__runner(injected, **kwargs)
            except subprocess.CalledProcessError:
                self.__owned.remove(name)
                raise
            except Exception:
                self.__uncertain.add(name)
                raise
            if operation == "create":
                container_id = str(result.stdout).strip() if hasattr(result, "stdout") else ""
                if not container_id:
                    if result is None:
                        container_id = name
                    else:
                        self.__uncertain.add(name)
                        raise ManagedDockerError("managed Docker ownership uncertain")
                self.__owned_ids[name] = container_id
            if hasattr(result, "returncode") and result.returncode != 0:
                self.__owned.remove(name)
            if operation == "run" and "--rm" in arguments:
                self.__owned.discard(name)
                if not hasattr(result, "returncode") or result.returncode == 0:
                    self.__consumed_names.add(name)
            return result
        if operation == "image":
            if len(argv) < 3 or argv[2] != "inspect":
                raise ManagedDockerError("managed Docker command rejected")
            self._verify_retained_mount()
            return self.__runner(argv, **kwargs)
        if operation in {"events", "ps"}:
            if not self.__owned:
                raise ManagedDockerError("managed Docker query rejected")
            self._validate_query(operation, arguments)
            self._verify_retained_mount()
            return self.__runner(argv, **kwargs)
        if operation not in {"start", "inspect", "logs", "stop", "rm", "port", "exec", "restart"}:
            raise ManagedDockerError("managed Docker command rejected")
        targets = self._targets(operation, arguments)
        queryable = self.__owned | (self.__retired if operation == "inspect" else set())
        if len(targets) != 1 or targets[0] not in queryable:
            raise ManagedDockerError("managed Docker target rejected")
        if operation == "exec":
            target_index = arguments.index(targets[0])
            command = arguments[target_index + 1:]
            if len(command) != 2 or command[0] != "rcon-cli" or not isinstance(command[1], str) or not command[1] or len(command[1]) > 512:
                raise ManagedDockerError("managed Docker exec rejected")
        if targets[0] in self.__owned and operation in {"start", "inspect", "logs", "restart", "exec", "stop", "rm", "port"}:
            expected = self.__owned_ids.get(targets[0])
            if not expected or not self._verify_identity(targets[0], expected):
                self.__uncertain.add(targets[0])
                raise ManagedDockerError("managed Docker ownership uncertain")
            argv = [*argv]
            argv[argv.index(targets[0], prefix)] = expected
        self._verify_retained_mount()
        result = self.__runner(argv, **kwargs)
        if operation == "rm" and (
            not hasattr(result, "returncode") or result.returncode == 0
        ):
            self.__owned.remove(targets[0])
            self.__owned_ids.pop(targets[0], None)
            self.__retired.add(targets[0])
            self.__consumed_names.add(targets[0])
        elif operation == "inspect" and targets[0] in self.__retired:
            if not hasattr(result, "returncode") or result.returncode == 0:
                raise ManagedDockerError("removed Docker target remained present")
            self.__retired.remove(targets[0])
        return result

    def _verify_identity(self, name, expected):
        try:
            result = self.__runner([
                "docker", "inspect", "--format",
                "{{json .Id}}|{{json .Config.Labels}}", name,
            ], check=True, capture_output=True, text=True)
            raw = str(getattr(result, "stdout", "")).strip()
            encoded_id, encoded_labels = raw.split("|", 1)
            import json
            observed_id = json.loads(encoded_id)
            observed_labels = json.loads(encoded_labels)
            return (
                observed_id == expected
                and isinstance(observed_labels, dict)
                and all(observed_labels.get(key) == value for key, value in self.__labels.items())
            )
        except Exception:
            return False

    def cleanup_owned(self) -> None:
        if self.__uncertain:
            raise ManagedDockerError("managed Docker ownership uncertain")
        failures = []
        for name in tuple(sorted(self.__owned)):
            expected = self.__owned_ids.get(name)
            if not expected or not self._verify_identity(name, expected):
                self.__uncertain.add(name)
                raise ManagedDockerError("managed Docker ownership uncertain")
            try:
                self.__runner(["docker", "stop", "--time", "30", expected], timeout=45, check=False)
            except Exception:
                failures.append(name)
            try:
                if not self._verify_identity(name, expected):
                    self.__uncertain.add(name)
                    raise ManagedDockerError("managed Docker ownership uncertain")
                self.__runner(["docker", "rm", "-f", expected], timeout=30, check=True)
            except Exception:
                failures.append(name)
            else:
                self.__owned.remove(name)
                self.__consumed_names.add(name)
        if failures:
            raise ManagedDockerError("managed Docker cleanup rejected")

    def prove_clean(self) -> DockerCleanProof:
        try:
            count = self.__probe()
        except Exception:
            raise ManagedDockerError("managed Docker postflight rejected") from None
        if type(count) is not int or count != 0 or self.__owned or self.__retired or self.__uncertain:
            raise ManagedDockerError("managed Docker residue present")
        return DockerCleanProof(managed_containers=0, _authority=_PROOF_AUTHORITY)


def bind_managed_docker(runner: Callable, binding: dict, residue_probe: Callable[[], int], *, handle):
    return ManagedDockerCapability(runner, binding, residue_probe, handle=handle)
