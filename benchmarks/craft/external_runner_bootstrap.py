from __future__ import annotations

import random
import runpy
import sys
from pathlib import Path


def run_seeded(seed: int, script: str, script_args: list[str]) -> None:
    random.seed(seed)
    try:
        import numpy

        numpy.random.seed(seed % (2**32))
    except ImportError:
        pass
    script_path = str(Path(script).resolve())
    original_argv = sys.argv
    original_path = list(sys.path)
    try:
        sys.argv = [script_path, *script_args]
        sys.path.insert(0, str(Path(script_path).parent))
        runpy.run_path(script_path, run_name="__main__")
    finally:
        sys.argv = original_argv
        sys.path[:] = original_path


def main(argv: list[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) < 2:
        raise SystemExit("usage: external_runner_bootstrap.py SEED SCRIPT [ARGS...]")
    run_seeded(int(arguments[0]), arguments[1], arguments[2:])


if __name__ == "__main__":
    main()
