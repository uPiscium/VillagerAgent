import time


def entity_position_available(bot) -> bool:
    try:
        position = bot.entity.position
        return all(getattr(position, axis, None) is not None for axis in ("x", "y", "z"))
    except (AttributeError, TypeError):
        return False


def wait_for_spawn(bot, spawn_event, *, timeout: float, poll_interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if spawn_event.is_set() or entity_position_available(bot):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        spawn_event.wait(min(poll_interval, remaining))
