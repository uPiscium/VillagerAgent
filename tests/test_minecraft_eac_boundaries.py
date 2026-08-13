from copy import deepcopy

import pytest

from benchmarks.minecraft.eac_runtime import install_minecraft_eac
from benchmarks.minecraft.experiment import _dual_dag_config, _task_selection_policy
from env.env import VillagerBench, env_type
from env.minecraft_dual_dag import build_minecraft_dual_dag_artifact
from pipeline.dual_dag_task_store import RuntimeTaskDAGStore
from type_define.graph import Task


def test_runtime_task_dag_semantics_are_independent_of_eac(tmp_path):
    store = RuntimeTaskDAGStore()
    task = Task("mine target", {})
    store.upsert_task(task)
    before = deepcopy(store.snapshot())
    environment = VillagerBench(env_type.none, 0, False, _virtual_debug=True)
    install_minecraft_eac(environment, mode="dual_dag_authority", run_id="boundary")
    assert store.snapshot() == before


def test_minecraft_dual_dag_projection_cannot_mutate_eac_authority(tmp_path):
    environment = VillagerBench(env_type.none, 0, False, _virtual_debug=True)
    runtime = install_minecraft_eac(environment, mode="dual_dag_authority", run_id="boundary")
    epoch = runtime.authority.epoch
    snapshot = build_minecraft_dual_dag_artifact(action_log={}, tasks=[])
    snapshot["mutates_runtime"] = True
    assert runtime.authority.epoch == epoch
    assert environment.get_eac_audit_artifact()["read_only_projection"] is True


def test_eac_mode_is_immutable():
    environment = VillagerBench(env_type.none, 0, False, _virtual_debug=True)
    install_minecraft_eac(environment, mode="dual_dag_authority", run_id="boundary")
    with pytest.raises(RuntimeError, match="immutable"):
        install_minecraft_eac(environment, mode="dual_dag_advisory", run_id="boundary")


@pytest.mark.parametrize("mode", ["dual_dag_advisory", "dual_dag_authority"])
def test_eac_modes_keep_identical_dual_dag_task_scheduling(mode):
    assert _task_selection_policy(mode) == mode
    config = _dual_dag_config(mode)
    assert config["runtime_task_selection"] == {"enabled": True, "policy": "dual-dag"}
    assert config["eac_mode"] == mode
