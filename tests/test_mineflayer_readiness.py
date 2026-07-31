import threading

from env.mineflayer_readiness import entity_position_available, wait_for_spawn


class Value:
    pass


def test_entity_position_is_independent_spawn_evidence():
    bot = Value()
    bot.entity = Value()
    bot.entity.position = Value()
    bot.entity.position.x = 5.5
    bot.entity.position.y = -59.0
    bot.entity.position.z = 5.5

    assert entity_position_available(bot) is True
    assert wait_for_spawn(bot, threading.Event(), timeout=0) is True


def test_spawn_event_remains_valid_readiness_evidence():
    event = threading.Event()
    event.set()

    assert wait_for_spawn(Value(), event, timeout=0) is True


def test_spawn_readiness_times_out_without_event_or_entity_position():
    bot = Value()
    bot.entity = None

    assert entity_position_available(bot) is False
    assert wait_for_spawn(bot, threading.Event(), timeout=0.01) is False
