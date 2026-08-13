"""Small externally authenticated launcher for readiness diagnostics."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

# Filled after the launcher is finalized.  The bootstrap authenticates bytes,
# then executes precisely those bytes; it accepts no path or command arguments.
READINESS_LAUNCHER_SHA256 = "e9d2cef3f357a86edeb0a9b3bb2c60882717f0347afbfdc5e3aa2516777b3bf7"


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
