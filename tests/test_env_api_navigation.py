from types import SimpleNamespace

from env.movement_diagnostics import STRICT_PER_AXIS


class Position:
    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z

    def offset(self, x, y, z):
        return Position(self.x + x, self.y + y, self.z + z)


class FakePathfinder:
    def __init__(self):
        self.goals = []
        self.movements = []

    def setGoal(self, goal):
        self.goals.append(goal)

    def setMovements(self, movements):
        self.movements.append(movements)


class FakeBot:
    def __init__(self, position):
        self.entity = SimpleNamespace(position=position)
        self.pathfinder = FakePathfinder()

    def blockAt(self, _position):
        return {"name": "air"}


class FakePathfinderModule:
    class Movements:
        def __init__(self, _bot):
            pass

    class goals:
        @staticmethod
        def GoalNear(x, y, z, radius):
            return (x, y, z, radius)


def test_move_to_waits_for_asynchronous_goal_completion(monkeypatch):
    from env.env_api import move_to

    bot = FakeBot(Position(14, -59, 5))
    target = Position(5, -60, 5)
    sleeps = []

    def advance_pathfinder(interval):
        sleeps.append(interval)
        if len(sleeps) == 3:
            bot.entity.position = Position(5.5, -60.0, 5.5)

    monkeypatch.setattr("env.env_api.time.sleep", advance_pathfinder)

    passed, _ = move_to(
        FakePathfinderModule,
        bot,
        Position,
        1.0,
        target,
        completion_policy=STRICT_PER_AXIS,
        position_convention="entity_feet",
        navigation_timeout_seconds=1.0,
        poll_interval_seconds=0.1,
    )

    assert passed is True
    assert sleeps == [0.1, 0.1, 0.1]
    assert bot.pathfinder.goals == [(5, -60, 5, 0)]


def test_move_to_stops_pathfinder_goal_on_timeout(monkeypatch):
    from env.env_api import move_to

    bot = FakeBot(Position(7.5, -60, 5.25))
    target = Position(5, -60, 5)
    clock = [0.0]

    monkeypatch.setattr("env.env_api.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(
        "env.env_api.time.sleep",
        lambda interval: clock.__setitem__(0, clock[0] + interval),
    )

    passed, message = move_to(
        FakePathfinderModule,
        bot,
        Position,
        1.0,
        target,
        completion_policy=STRICT_PER_AXIS,
        position_convention="entity_feet",
        navigation_timeout_seconds=0.3,
        poll_interval_seconds=0.1,
    )

    assert passed is False
    assert "can not reach position" in message
    assert "navigation timeout=0.3s" in message
    assert bot.pathfinder.goals == [(5, -60, 5, 0), None]
