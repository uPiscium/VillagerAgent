from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from benchmarks.tdw_mat.adapter import TDWMATConfig


DEFAULT_COELA_ROOT = Path("external/CoELA/tdw_mat")


@dataclass(frozen=True)
class CoELATDWRuntimeConfig:
    coela_root: Path = DEFAULT_COELA_ROOT
    output_dir: Path = Path("result/tdw_mat/real_smoke")
    port: int = 1071
    screen_size: int = 128
    launch_build: bool = True
    save_images: bool = False


class CoELATDWMATEnvironment:
    """Thin bridge around CoELA's TDW Gym without importing it at module load."""

    def __init__(self, *, scenario: TDWMATConfig, runtime: CoELATDWRuntimeConfig):
        self.scenario = scenario
        self.runtime = runtime
        self.root = runtime.coela_root.resolve()
        _require_source_tree(self.root)
        self.output_dir = runtime.output_dir.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        gym_dir = self.root / "tdw-gym"
        for import_path in (str(gym_dir), str(self.root)):
            if import_path not in sys.path:
                sys.path.insert(0, import_path)
        with _working_directory(self.output_dir):
            module = importlib.import_module("tdw_gym")
            self._env = module.TDW(
                port=runtime.port,
                number_of_agents=scenario.agent_count,
                save_dir=str(self.output_dir),
                max_frames=scenario.max_frames,
                launch_build=runtime.launch_build,
                screen_size=runtime.screen_size,
                data_prefix=str(self.root / "dataset" / "dataset_test") + os.sep,
                gt_mask=True,
            )

    def reset(self, *, seed: int, options: dict[str, str]):
        with _working_directory(self.root):
            return self._env.reset(seed=seed, options=options, output_dir=str(self.output_dir))

    def step(self, actions: dict[str, dict[str, Any]]):
        with _working_directory(self.root):
            return self._env.step(actions)

    def check_goal(self):
        return self._env.check_goal()

    def close(self) -> None:
        self._env.close()
        action_log = getattr(self._env, "f", None)
        if action_log is not None and not action_log.closed:
            action_log.close()


class CoELATDWMATEnvFactory:
    def __init__(self, runtime: CoELATDWRuntimeConfig):
        self.runtime = runtime

    def __call__(self, config: TDWMATConfig) -> CoELATDWMATEnvironment:
        return CoELATDWMATEnvironment(scenario=config, runtime=self.runtime)


def inspect_real_preflight(
    *,
    coela_root: str | Path = DEFAULT_COELA_ROOT,
    environ: dict[str, str] | None = None,
    module_available: Callable[[str], bool] | None = None,
    python_version: tuple[int, int] | None = None,
) -> dict[str, Any]:
    configured_root = Path(coela_root)
    root = configured_root.resolve()
    env = os.environ if environ is None else environ
    available = module_available or (lambda name: importlib.util.find_spec(name) is not None)
    version = python_version or (sys.version_info.major, sys.version_info.minor)
    source_files = {
        "tdw_gym": root / "tdw-gym" / "tdw_gym.py",
        "test_scenarios": root / "dataset" / "dataset_test" / "test_env.json",
        "name_map": root / "dataset" / "name_map.json",
        "room_types": root / "dataset" / "room_types.json",
        "license": root / "LICENSE",
    }
    checks = {
        "source_tree": all(path.is_file() for path in source_files.values()),
        "python_version_3_10_or_newer": version >= (3, 10),
        "python_gym_package": available("gym"),
        "python_tdw_package": available("tdw"),
        "display": bool(env.get("DISPLAY")),
    }
    asset_cache = root / "transport_challenge_asset_bundles"
    return {
        "schema_version": 1,
        "benchmark": "tdw_mat",
        "coela_root": str(configured_root),
        "python_version": f"{version[0]}.{version[1]}",
        "checks": checks,
        "ready": all(checks.values()),
        "missing": [name for name, passed in checks.items() if not passed],
        "source_files": {
            name: str(configured_root / path.relative_to(root))
            for name, path in source_files.items()
        },
        "asset_cache": {
            "path": str(configured_root / asset_cache.relative_to(root)),
            "present": asset_cache.is_dir(),
            "required_before_launch": False,
            "note": "Upstream normally downloads transport assets during first launch.",
        },
    }


def _require_source_tree(root: Path) -> None:
    required = (
        root / "tdw-gym" / "tdw_gym.py",
        root / "dataset" / "dataset_test" / "test_env.json",
        root / "dataset" / "name_map.json",
        root / "dataset" / "room_types.json",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Incomplete CoELA TDW-MAT source tree: " + ", ".join(missing))


@contextmanager
def _working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)
