import os
from pathlib import Path

import pytest

from benchmarks.minecraft.real_smoke import main


def _run_opt_in(variable: str, check: str, extra_args: list[str] | None = None) -> None:
    if os.environ.get(variable) != "1":
        pytest.skip(f"set {variable}=1 to run the {check} real smoke")
    output_dir = os.environ.get("VILLAGER_REAL_SMOKE_OUTPUT_DIR")
    if not output_dir:
        pytest.fail("missing prerequisite: set VILLAGER_REAL_SMOKE_OUTPUT_DIR")
    exit_code = main([check, "--output-dir", output_dir, *(extra_args or [])])
    assert exit_code == 0, f"{check} smoke failed; inspect {Path(output_dir) / _run_name(check)}"


def _run_name(check: str) -> str:
    return {
        "ollama": "ollama_preflight",
        "port": "minecraft_port",
        "bridge": "minecraft_bridge",
        "judged": "minecraft_judged_smoke",
    }[check]


@pytest.mark.real_smoke
def test_ollama_real_preflight():
    _run_opt_in("VILLAGER_OLLAMA_REAL_SMOKE", "ollama")


@pytest.mark.real_smoke
def test_minecraft_port_reachability():
    _run_opt_in("VILLAGER_MINECRAFT_PORT_SMOKE", "port")


@pytest.mark.real_smoke
def test_minecraft_env_type_none_bridge_action():
    _run_opt_in("VILLAGER_MINECRAFT_BRIDGE_SMOKE", "bridge")


@pytest.mark.real_smoke
def test_minecraft_judged_meta_smoke():
    _run_opt_in("VILLAGER_MINECRAFT_JUDGED_SMOKE", "judged")
