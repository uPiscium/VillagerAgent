from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MinecraftTargetSafetyAssessment:
    safe: bool
    reasons: tuple[str, ...]
    diagnostics: dict


def assess_minecraft_target_safety(
    *,
    runtime_started: bool,
    runtime_process: dict,
    bridge_cleanup: dict,
) -> MinecraftTargetSafetyAssessment:
    if not runtime_started:
        return MinecraftTargetSafetyAssessment(
            safe=True,
            reasons=(),
            diagnostics={"runtime_started": False},
        )

    reasons = []
    process_alive = runtime_process.get("process_alive_after_kill") is True
    process_group_alive = runtime_process.get("process_group_alive_after_kill") is True
    if process_alive:
        reasons.append("runtime_process_alive_after_kill")
    if process_group_alive:
        reasons.append("runtime_process_group_alive_after_kill")

    bridge_processes_alive = []
    bridge_cleanup_complete = False
    if not isinstance(bridge_cleanup, dict) or not bridge_cleanup:
        reasons.append("bridge_cleanup_missing")
    else:
        bridge_cleanup_complete = bridge_cleanup.get("cleanup_complete") is True
        if not bridge_cleanup_complete:
            reasons.append("bridge_cleanup_incomplete")
        processes = bridge_cleanup.get("processes")
        if not isinstance(processes, dict):
            reasons.append("bridge_process_metadata_invalid")
        else:
            for agent_name, process in processes.items():
                if not isinstance(agent_name, str) or not isinstance(process, dict):
                    reasons.append("bridge_process_metadata_invalid")
                    continue
                if process.get("alive_after_kill") is True:
                    bridge_processes_alive.append(agent_name)
                    reasons.append(f"bridge_process_alive_after_kill:{agent_name}")

    normalized_reasons = tuple(dict.fromkeys(reasons))
    return MinecraftTargetSafetyAssessment(
        safe=not normalized_reasons,
        reasons=normalized_reasons,
        diagnostics={
            "runtime_started": True,
            "runtime_process_alive_after_kill": process_alive,
            "runtime_process_group_alive_after_kill": process_group_alive,
            "bridge_cleanup_complete": bridge_cleanup_complete,
            "bridge_processes_alive_after_kill": bridge_processes_alive,
        },
    )
