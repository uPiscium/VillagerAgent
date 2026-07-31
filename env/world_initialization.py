LEGACY_GENERATED_ARENA = "legacy_generated_arena"
PRESERVE_RESTORED_SNAPSHOT = "preserve_restored_snapshot"

_SUPPORTED_MODES = {LEGACY_GENERATED_ARENA, PRESERVE_RESTORED_SNAPSHOT}


def resolve_world_initialization(value: str | None) -> str:
    mode = value or LEGACY_GENERATED_ARENA
    if mode not in _SUPPORTED_MODES:
        raise ValueError(f"unsupported world initialization mode: {mode!r}")
    return mode
