"""Small externally authenticated launcher for readiness diagnostics."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

# Filled after the launcher is finalized.  The bootstrap authenticates bytes,
# then executes precisely those bytes; it accepts no path or command arguments.
READINESS_LAUNCHER_SHA256 = "82a4ae2ec9ab3b039cf0ef8d874e8be8527b5ca7c351a656b79b188afb96ba32"


def main() -> int:
    if __name__ != "__main__":
        raise RuntimeError("bootstrap_must_run_as_main")
    if len(__import__("sys").argv) != 1:
        raise RuntimeError("arguments_rejected")
    launcher = Path(__file__).with_name("gate_a_v4_readiness_launcher.py")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    descriptor = os.open(launcher, flags)
    try:
        with os.fdopen(descriptor, "rb") as stream:
            source = stream.read()
    finally:
        descriptor = -1
    if hashlib.sha256(source).hexdigest() != READINESS_LAUNCHER_SHA256:
        raise RuntimeError("readiness_launcher_authentication_failed")
    namespace = {"__name__": "__main__", "__file__": str(launcher)}
    exec(compile(source, str(launcher), "exec"), namespace, namespace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
