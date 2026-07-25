from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


PARTNR_SOURCE_COMMIT = "ddfff19f4b6c098a31edea4d19e7b75db72433c2"
DEFAULT_SOURCE_ROOT = Path("external/partnr-planner")
DEFAULT_DATASET_PATH = DEFAULT_SOURCE_ROOT / "data/datasets/partnr_episodes/v0_0/val_mini.json.gz"
DEFAULT_SCENE_ROOT = DEFAULT_SOURCE_ROOT / "data/hssd-hab"


@dataclass(frozen=True)
class PARTNRRuntimeConfig:
    source_root: Path = DEFAULT_SOURCE_ROOT
    dataset_path: Path = DEFAULT_DATASET_PATH
    scene_root: Path = DEFAULT_SCENE_ROOT
    output_dir: Path = Path("result/partnr/issue_378_real")
    python_executable: Path = Path(sys.executable)
    episode_limit: int = 4
    wall_timeout_seconds: int = 1800

    def __post_init__(self) -> None:
        if not 1 <= self.episode_limit <= 4:
            raise ValueError("PARTNR bounded smoke episode_limit must be between 1 and 4.")
        if self.wall_timeout_seconds <= 0 or self.wall_timeout_seconds > 1800:
            raise ValueError("PARTNR bounded smoke timeout must be between 1 and 1800 seconds.")


def inspect_real_preflight(
    runtime: PARTNRRuntimeConfig,
    *,
    module_available: Callable[[str], bool] | None = None,
    python_version: tuple[int, int] | None = None,
    source_commit: str | None = None,
    headless_context_ready: bool | None = None,
    scene_assets_ready: bool | None = None,
) -> dict[str, Any]:
    source = runtime.source_root.resolve()
    dataset = runtime.dataset_path.resolve()
    scenes = runtime.scene_root.resolve()
    output = runtime.output_dir.resolve()
    runtime_python_audit = (
        {"attempted": False}
        if module_available is not None and python_version is not None
        else _probe_runtime_python(runtime)
    )
    available = module_available or (
        lambda name: bool(runtime_python_audit.get("modules", {}).get(name))
    )
    probed_version = runtime_python_audit.get("version", [0, 0])
    version = python_version or (int(probed_version[0]), int(probed_version[1]))
    required_source = {
        "readme": source / "README.md",
        "installation": source / "INSTALLATION.md",
        "environment_interface": source / "habitat_llm/agent/env/environment_interface.py",
        "measures": source / "habitat_llm/agent/env/measures.py",
        "verify_episodes": source / "habitat_llm/examples/verify_episodes.py",
        "planner_demo": source / "habitat_llm/examples/planner_demo.py",
        "heuristic_config": source / "habitat_llm/conf/baselines/heuristic_full_obs.yaml",
    }
    submodules = {
        "habitat_lab_submodule": source / "third_party/habitat-lab/pyproject.toml",
        "semantic_exploration_submodule": source / "third_party/semantic_exploration/README.md",
        "transformers_cfg_submodule": source / "third_party/transformers-CFG/setup.py",
    }
    metadata_files = {
        "object_categories": scenes / "metadata/object_categories_filtered.csv",
        "furniture_categories": scenes / "metadata/fpmodels-with-decomposed.csv",
        "room_objects": scenes / "metadata/room_objects.json",
        "affordance_objects": scenes / "metadata/affordance_objects.csv",
    }
    dataset_audit = _inspect_dataset(dataset, display_path=_display_path(runtime.dataset_path))
    headless_context_audit = (
        {"attempted": False, "valid": headless_context_ready}
        if headless_context_ready is not None
        else _probe_headless_context(runtime)
    )
    scene_asset_audit = (
        {"inspected": False, "valid": scene_assets_ready}
        if scene_assets_ready is not None
        else _inspect_scene_assets(dataset, scenes, episode_limit=runtime.episode_limit)
    )
    actual_commit = source_commit if source_commit is not None else _git_commit(source)
    output_parent = _nearest_existing_parent(output.parent)
    output_is_separate = not _is_within(output, source) and not _is_within(output, dataset.parent)
    checks = {
        "source_tree": all(path.is_file() for path in required_source.values()),
        "source_commit": actual_commit == PARTNR_SOURCE_COMMIT,
        "python_3_9": version == (3, 9),
        "python_habitat_package": available("habitat"),
        "python_habitat_sim_package": available("habitat_sim"),
        "python_torch_package": available("torch"),
        "python_hydra_package": available("hydra"),
        **{name: path.is_file() for name, path in submodules.items()},
        "val_mini_dataset": bool(dataset_audit.get("valid")),
        "hssd_scene_root": scenes.is_dir(),
        "hssd_metadata": all(path.is_file() for path in metadata_files.values()),
        "headless_context": bool(headless_context_audit.get("valid")),
        "bounded_scene_assets": bool(scene_asset_audit.get("valid")),
        "output_identity": bool(output_parent and os.access(output_parent, os.W_OK) and output_is_separate),
    }
    return {
        "schema_version": 1,
        "benchmark": "partnr",
        "preflight_type": "official_val_mini_headless",
        "performance_claim": False,
        "source_repository": "https://github.com/facebookresearch/partnr-planner",
        "expected_source_commit": PARTNR_SOURCE_COMMIT,
        "actual_source_commit": actual_commit,
        "configured_paths": {
            "source_root": _display_path(runtime.source_root),
            "dataset_path": _display_path(runtime.dataset_path),
            "scene_root": _display_path(runtime.scene_root),
            "output_dir": _display_path(runtime.output_dir),
            "python_executable": _display_path(runtime.python_executable),
        },
        "python_version": f"{version[0]}.{version[1]}",
        "headless_required": True,
        "checks": checks,
        "ready": all(checks.values()),
        "missing": [name for name, passed in checks.items() if not passed],
        "dataset_audit": dataset_audit,
        "runtime_python_audit": runtime_python_audit,
        "headless_context_audit": headless_context_audit,
        "scene_asset_audit": scene_asset_audit,
        "source_files": {
            name: _display_path(runtime.source_root / path.relative_to(source))
            for name, path in required_source.items()
        },
        "metadata_files": {
            name: _display_path(runtime.scene_root / path.relative_to(scenes))
            for name, path in metadata_files.items()
        },
    }


def _probe_runtime_python(runtime: PARTNRRuntimeConfig) -> dict[str, Any]:
    modules = ("habitat", "habitat_sim", "torch", "hydra")
    probe = (
        "import importlib.util, json, sys; "
        f"names = {modules!r}; "
        "print(json.dumps({'version': [sys.version_info.major, sys.version_info.minor], "
        "'modules': {name: importlib.util.find_spec(name) is not None for name in names}}))"
    )
    try:
        completed = subprocess.run(
            [str(runtime.python_executable), "-c", probe],
            cwd=runtime.source_root,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        payload = json.loads(completed.stdout) if completed.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"attempted": True, "valid": False, "error_type": type(exc).__name__}
    return {
        "attempted": True,
        "valid": True,
        "returncode": completed.returncode,
        "version": payload.get("version", [0, 0]),
        "modules": payload.get("modules", {}),
    }


def _probe_headless_context(runtime: PARTNRRuntimeConfig) -> dict[str, Any]:
    probe = (
        "import habitat_sim; "
        "sim_config = habitat_sim.SimulatorConfiguration(); "
        "sim_config.scene_id = 'NONE'; "
        "agent = habitat_sim.agent.AgentConfiguration(); "
        "sim = habitat_sim.Simulator(habitat_sim.Configuration(sim_config, [agent])); "
        "print('PARTNR_HEADLESS_CONTEXT_OK'); "
        "sim.close()"
    )
    try:
        completed = subprocess.run(
            [str(runtime.python_executable), "-c", probe],
            cwd=runtime.source_root,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"attempted": True, "valid": False, "error_type": type(exc).__name__}
    return {
        "attempted": True,
        "valid": completed.returncode == 0
        and "PARTNR_HEADLESS_CONTEXT_OK" in completed.stdout,
        "returncode": completed.returncode,
    }


def _inspect_scene_assets(dataset: Path, scenes: Path, *, episode_limit: int) -> dict[str, Any]:
    try:
        episodes = _read_gzip_json(dataset).get("episodes", [])[:episode_limit]
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {"inspected": True, "valid": False, "error_type": type(exc).__name__}
    scene_ids = sorted(
        {
            _scene_id(str(episode.get("scene_id", "")))
            for episode in episodes
            if episode.get("scene_id")
        }
    )
    required: set[Path] = set()
    missing: set[str] = set()
    for scene_id in scene_ids:
        scene_instance = scenes / "scenes-partnr-filtered" / f"{scene_id}.scene_instance.json"
        required.update(
            {
                scene_instance,
                scenes / "stages" / f"{scene_id}.glb",
                scenes / "stages" / f"{scene_id}.stage_config.json",
                scenes / "semantics/scenes" / f"{scene_id}.semantic_config.json",
            }
        )
        if not scene_instance.is_file():
            continue
        try:
            payload = json.loads(scene_instance.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            missing.add(f"scene:{scene_id}:invalid_instance")
            continue
        for instance in payload.get("object_instances", []):
            template = str(instance.get("template_name", ""))
            config = _object_config_path(scenes, template)
            if config is None:
                missing.add(f"scene:{scene_id}:object_config:{template}")
                continue
            required.add(config)
            try:
                attributes = json.loads(config.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                missing.add(f"scene:{scene_id}:invalid_object_config:{template}")
                continue
            for field in ("render_asset", "collision_asset"):
                asset = attributes.get(field)
                if asset:
                    required.add(config.parent / str(asset))
        for instance in payload.get("articulated_object_instances", []):
            template = str(
                instance.get("template_name", instance.get("template_handle", ""))
            )
            root = scenes / "urdf" / template
            if not root.is_dir():
                missing.add(f"scene:{scene_id}:articulated:{template}")
                continue
            required.update(path for path in root.rglob("*") if path.is_file())
    unmaterialized = [path for path in required if not path.is_file() or _is_lfs_pointer(path)]
    missing.update(
        f"scene_asset:{path.name}" for path in sorted(unmaterialized, key=str)
    )
    return {
        "inspected": True,
        "valid": bool(scene_ids) and not missing,
        "scene_ids": scene_ids,
        "required_file_count": len(required),
        "missing": sorted(missing),
    }


def _scene_id(value: str) -> str:
    name = Path(value).name
    for suffix in (".scene_instance.json", ".glb", ".json"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _object_config_path(scenes: Path, template: str) -> Path | None:
    base = template.split("_part_", 1)[0]
    candidates = (
        scenes / "objects" / template[:1] / f"{template}.object_config.json",
        scenes / "objects/decomposed" / base / f"{template}.object_config.json",
        scenes / "objects/openings" / f"{template}.object_config.json",
    )
    return next((path for path in candidates if path.is_file()), None)


def _is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(42).startswith(b"version https://git-lfs.github.com/spec/")
    except OSError:
        return True


def write_bounded_dataset(source: Path, output: Path, *, episode_limit: int) -> dict[str, Any]:
    if not 1 <= episode_limit <= 4:
        raise ValueError("PARTNR dataset subset must contain between 1 and 4 episodes.")
    payload = _read_gzip_json(source)
    episodes = payload.get("episodes")
    if not isinstance(episodes, list) or len(episodes) < episode_limit:
        raise ValueError(f"PARTNR dataset has fewer than {episode_limit} episodes.")
    bounded = dict(payload)
    bounded["episodes"] = episodes[:episode_limit]
    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output, "wt", encoding="utf-8") as handle:
        json.dump(bounded, handle)
    return {
        "source": str(source),
        "output": str(output),
        "episode_count": episode_limit,
        "episode_ids": [str(episode.get("episode_id", "")) for episode in bounded["episodes"]],
    }


def build_step_zero_command(runtime: PARTNRRuntimeConfig, dataset_path: Path) -> list[str]:
    return [
        str(runtime.python_executable),
        "-m",
        "habitat_llm.examples.verify_episodes",
        "--config-name",
        "examples/planner_multi_agent_demo_config.yaml",
        "hydra.run.dir=.",
        "evaluation=centralized_evaluation_runner_multi_agent",
        f"habitat.dataset.data_path={dataset_path}",
        "mode=data",
        "world_model.partial_obs=False",
        "evaluation.type=centralized",
        f"evaluation.output_dir={runtime.output_dir.resolve() / 'step_zero'}",
        "num_proc=1",
    ]


def build_bounded_smoke_command(runtime: PARTNRRuntimeConfig, dataset_path: Path) -> list[str]:
    output = runtime.output_dir.resolve() / "bounded_heuristic"
    return [
        str(runtime.python_executable),
        "-m",
        "habitat_llm.examples.planner_demo",
        "--config-name",
        "baselines/heuristic_full_obs.yaml",
        f"habitat.dataset.data_path={dataset_path}",
        f"paths.results_dir={output}",
        f"evaluation.output_dir={output}",
        "num_proc=1",
    ]


def _inspect_dataset(path: Path, *, display_path: str | None = None) -> dict[str, Any]:
    reported_path = display_path or str(path)
    if not path.is_file():
        return {"path": reported_path, "present": False, "valid": False, "episode_count": 0}
    try:
        payload = _read_gzip_json(path)
        episodes = payload.get("episodes")
        if not isinstance(episodes, list) or not episodes:
            raise ValueError("missing non-empty episodes list")
        required = {"episode_id", "instruction", "scene_id", "evaluation_propositions"}
        missing_fields = sorted(required - set(episodes[0]))
        return {
            "path": reported_path,
            "present": True,
            "valid": not missing_fields,
            "episode_count": len(episodes),
            "first_episode_id": str(episodes[0].get("episode_id", "")),
            "first_episode_missing_fields": missing_fields,
        }
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {
            "path": reported_path,
            "present": True,
            "valid": False,
            "episode_count": 0,
            "error": str(exc),
        }


def _read_gzip_json(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("PARTNR dataset root must be an object.")
    return payload


def _git_commit(path: Path) -> str | None:
    if not path.is_dir():
        return None
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _nearest_existing_parent(path: Path) -> Path | None:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current if current.is_dir() else None


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _display_path(path: Path) -> str:
    if not path.is_absolute():
        return str(path)
    return f"<external>/{path.name}"
