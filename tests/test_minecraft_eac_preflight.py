from types import SimpleNamespace

import pytest

from env.eac_preflight import evaluate_eac_preflight


class Bot:
    def __init__(self):
        self.inventory = SimpleNamespace(items=lambda: [SimpleNamespace(name="stone", count=3)])
        self.entities = {1: SimpleNamespace(name="cow", username=None),
                         2: SimpleNamespace(name="player", username="Bob")}

    def blockAt(self, unused):
        return SimpleNamespace(name="stone")


@pytest.mark.parametrize(("action", "arguments"), [
    ("MineBlock", {"x": 1, "y": 2, "z": 3}),
    ("placeBlock", {"item_name": "stone", "x": 1, "y": 2, "z": 3, "facing": "north"}),
    ("navigateTo", {"x": 1, "y": 2, "z": 3}),
    ("attackTarget", {"target_name": "cow"}),
    ("handoverBlock", {"target_player_name": "Bob", "item_name": "stone", "item_count": 2}),
    ("talkTo", {"entity_name": "Bob", "message": "hello"}),
    ("scanNearbyEntities", {"item_name": "cow", "radius": 5, "item_num": 1}),
    ("waitForFeedback", {"entity_name": "Bob", "seconds": 10}),
])
def test_classified_native_preflight_accepts_matching_legal_request(action, arguments):
    bot = Bot()
    if action == "placeBlock":
        bot.blockAt = lambda unused: SimpleNamespace(name="air")
    assert evaluate_eac_preflight(action, arguments, bot, lambda *values: values) is True


@pytest.mark.parametrize(("action", "arguments"), [
    ("talkTo", {"entity_name": "", "message": "hello"}),
    ("scanNearbyEntities", {"item_name": "cow", "radius": 0, "item_num": 1}),
    ("waitForFeedback", {"entity_name": "Bob", "seconds": 31}),
])
def test_evidence_action_preflight_rejects_invalid_declared_envpre(action, arguments):
    assert evaluate_eac_preflight(action, arguments, Bot(), lambda *values: values) is False
