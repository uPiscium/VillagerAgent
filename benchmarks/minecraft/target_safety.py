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
    runtime_process: object,
    bridge_cleanup: object,
) -> MinecraftTargetSafetyAssessment:
    if not runtime_started:
        return MinecraftTargetSafetyAssessment(
            safe=True,
            reasons=(),
            diagnostics={"runtime_started": False},
        )

    reasons = []
    runtime_process_metadata_valid = isinstance(runtime_process, dict)
    process_alive = None
    process_group_alive = None
    if runtime_process_metadata_valid:
        process_alive, process_alive_valid = _strict_required_bool_field(
            runtime_process,
            "process_alive_after_kill",
        )
        process_group_alive, process_group_alive_valid = _strict_required_bool_field(
            runtime_process,
            "process_group_alive_after_kill",
        )
        runtime_process_metadata_valid = process_alive_valid and process_group_alive_valid
    if not runtime_process_metadata_valid:
        reasons.append("runtime_process_metadata_invalid")
    if process_alive:
        reasons.append("runtime_process_alive_after_kill")
    if process_group_alive:
        reasons.append("runtime_process_group_alive_after_kill")

    bridge_processes_alive = []
    bridge_cleanup_metadata_valid = True
    bridge_cleanup_complete = None
    if bridge_cleanup is None or bridge_cleanup == {}:
        reasons.append("bridge_cleanup_missing")
        bridge_cleanup_metadata_valid = False
    elif not isinstance(bridge_cleanup, dict):
        reasons.append("bridge_cleanup_metadata_invalid")
        bridge_cleanup_metadata_valid = False
    else:
        bridge_cleanup_complete_value = bridge_cleanup.get("cleanup_complete")
        if not isinstance(bridge_cleanup_complete_value, bool):
            reasons.append("bridge_cleanup_metadata_invalid")
            bridge_cleanup_metadata_valid = False
        else:
            bridge_cleanup_complete = bridge_cleanup_complete_value
        if bridge_cleanup_complete is False:
            reasons.append("bridge_cleanup_incomplete")
        processes = bridge_cleanup.get("processes")
        if not isinstance(processes, dict):
            reasons.append("bridge_process_metadata_invalid")
            bridge_cleanup_metadata_valid = False
        else:
            for agent_name, process in processes.items():
                if not isinstance(agent_name, str) or not agent_name:
                    reasons.append("bridge_process_metadata_invalid")
                    bridge_cleanup_metadata_valid = False
                    continue
                if (
                    not isinstance(process, dict)
                    or not isinstance(process.get("alive_after_kill"), bool)
                ):
                    reasons.append(f"bridge_process_metadata_invalid:{agent_name}")
                    bridge_cleanup_metadata_valid = False
                    continue
                if process["alive_after_kill"]:
                    bridge_processes_alive.append(agent_name)
                    reasons.append(f"bridge_process_alive_after_kill:{agent_name}")

    normalized_reasons = tuple(dict.fromkeys(reasons))
    return MinecraftTargetSafetyAssessment(
        safe=not normalized_reasons,
        reasons=normalized_reasons,
        diagnostics={
            "runtime_started": True,
            "runtime_process_metadata_valid": runtime_process_metadata_valid,
            "runtime_process_alive_after_kill": process_alive,
            "runtime_process_group_alive_after_kill": process_group_alive,
            "bridge_cleanup_metadata_valid": bridge_cleanup_metadata_valid,
            "bridge_cleanup_complete": bridge_cleanup_complete,
            "bridge_processes_alive_after_kill": bridge_processes_alive,
        },
    )


def _strict_required_bool_field(mapping: dict, field: str) -> tuple[bool | None, bool]:
    if field not in mapping:
        return None, False
    value = mapping[field]
    if not isinstance(value, bool):
        return None, False
    return value, True
