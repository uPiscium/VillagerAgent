from __future__ import annotations

import pickle
import sys
from pathlib import Path

from benchmarks.cwah.adapter import CWAHConfig


def coela_cwah_env_factory(config: CWAHConfig):
    root = Path(config.metadata.get("repo_root", Path.cwd())).resolve()
    coela_cwah = Path(config.metadata.get("coela_cwah_path") or root / "external" / "CoELA" / "cwah").resolve()
    dataset_path = Path(config.metadata.get("dataset_path") or coela_cwah / "dataset" / "test_env_set_help.pik").resolve()
    executable_file = resolve_coela_executable(
        root=root,
        coela_cwah=coela_cwah,
        explicit_path=config.metadata.get("executable_file"),
    )
    base_port = int(config.metadata.get("base_port", 6314))

    if not coela_cwah.exists():
        raise FileNotFoundError(f"CoELA C-WAH path not found: {coela_cwah}")
    if not dataset_path.exists():
        raise FileNotFoundError(f"C-WAH dataset not found: {dataset_path}")
    if not executable_file.exists():
        raise FileNotFoundError(_missing_executable_message(root=root, coela_cwah=coela_cwah, explicit_path=config.metadata.get("executable_file")))

    sys.path.insert(0, str(coela_cwah))
    from envs.unity_environment import UnityEnvironment

    env_task_set = pickle.load(dataset_path.open("rb"))
    return UnityEnvironment(
        num_agents=config.agent_count,
        max_episode_length=config.max_steps,
        env_task_set=env_task_set,
        agent_goals=["LLM" for _ in range(config.agent_count)],
        observation_types=[config.observation_type for _ in range(config.agent_count)],
        use_editor=False,
        executable_args={"file_name": str(executable_file), "no_graphics": True},
        base_port=base_port,
        seed=config.seed,
    )


def resolve_coela_executable(*, root: Path, coela_cwah: Path, explicit_path: str | Path | None) -> Path:
    for candidate in _executable_candidates(root=root, coela_cwah=coela_cwah, explicit_path=explicit_path):
        if candidate.exists():
            return candidate
    return _executable_candidates(root=root, coela_cwah=coela_cwah, explicit_path=explicit_path)[0]


def _executable_candidates(*, root: Path, coela_cwah: Path, explicit_path: str | Path | None) -> list[Path]:
    candidates = []
    if explicit_path:
        candidates.append(Path(explicit_path).expanduser().resolve())
    candidates.extend([
        (coela_cwah.parent / "executable" / "linux_exec.v2.3.0.x86_64").resolve(),
        (root / "external" / "executable" / "linux_exec.v2.3.0.x86_64").resolve(),
    ])
    return candidates


def _missing_executable_message(*, root: Path, coela_cwah: Path, explicit_path: str | Path | None) -> str:
    checked = "\n".join(f"- {path}" for path in _executable_candidates(root=root, coela_cwah=coela_cwah, explicit_path=explicit_path))
    return (
        "C-WAH executable not found. Download the CoELA VirtualHome executable, unzip it under "
        f"{coela_cwah.parent / 'executable'}, and chmod +x linux_exec.v2.3.0.x86_64. Checked:\n{checked}"
    )
