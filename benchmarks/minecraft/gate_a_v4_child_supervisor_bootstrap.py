"""Private blocked-exec bootstrap for Gate A supervised Docker children."""
from __future__ import annotations

import json
import hashlib
import os
import sys
from pathlib import Path

DOCKER_EXECUTABLE = "/nix/store/3a2hvsxqwqz9zfi44jhpi58172gdgf8p-docker-29.6.2/bin/docker"
DOCKER_SHA256 = "f1dcc6a66f2b9d022d17ecb9e9e9939ef1e9062ae4f292e434ed90f6b43d431b"


def _identity() -> dict:
    fields = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="ascii").rsplit(") ", 1)[1].split()
    return {
        "pid": os.getpid(), "start_ticks": int(fields[19]), "pgid": int(fields[2]),
        "session_id": int(fields[3]),
    }


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] != "--" or sys.argv[2] != "docker":
        return 64
    identity_fd = int(os.environ.pop("VA_GATE_A_IDENTITY_FD"))
    release_fd = int(os.environ.pop("VA_GATE_A_RELEASE_FD"))
    docker_executable = DOCKER_EXECUTABLE
    if not Path(docker_executable).is_file():
        return 66
    try:
        digest = hashlib.sha256(Path(docker_executable).resolve(strict=True).read_bytes()).hexdigest()
    except OSError:
        return 66
    if digest != DOCKER_SHA256:
        return 66
    os.setsid()
    os.write(identity_fd, json.dumps(_identity(), sort_keys=True).encode("ascii"))
    os.close(identity_fd)
    if os.read(release_fd, 2) != b"G":
        return 65
    os.close(release_fd)
    os.execve(docker_executable, sys.argv[2:], os.environ)
    return 70


if __name__ == "__main__":
    raise SystemExit(main())
