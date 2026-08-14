from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from env.eac_preflight import evaluate_eac_preflight, register_eac_preflight_route


class Bot:
    def __init__(self):
        self.inventory = SimpleNamespace(items=lambda: [SimpleNamespace(name="stone", count=3)])
        self.heldItem = SimpleNamespace(name="iron_pickaxe")
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
        bot.blockAt = lambda pos: SimpleNamespace(name="air" if tuple(pos) == (1, 2, 3) else "stone")
    assert evaluate_eac_preflight(action, arguments, bot, lambda *values: values) is True


@pytest.mark.parametrize(("action", "arguments"), [
    ("talkTo", {"entity_name": "", "message": "hello"}),
    ("scanNearbyEntities", {"item_name": "cow", "radius": 0, "item_num": 1}),
    ("waitForFeedback", {"entity_name": "Bob", "seconds": 31}),
])
def test_evidence_action_preflight_rejects_invalid_declared_envpre(action, arguments):
    assert evaluate_eac_preflight(action, arguments, Bot(), lambda *values: values) is False


@pytest.mark.parametrize("facing", ["x", "y", "z"])
def test_place_preflight_rejects_support_on_wrong_axis(facing):
    bot = Bot()
    allowed = {"x": (0, 1, 0), "y": (1, 0, 0), "z": (1, 0, 0)}[facing]
    bot.blockAt = lambda pos: SimpleNamespace(
        name="air" if tuple(pos) != (1 + allowed[0], 2 + allowed[1], 3 + allowed[2]) else "stone")
    assert evaluate_eac_preflight(
        "placeBlock", {"item_name": "stone", "x": 1, "y": 2, "z": 3, "facing": facing},
        bot, lambda *values: values,
    ) is False


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
def test_fastapi_preflight_route_accepts_every_classified_action_without_effect(action, arguments):
    bot = Bot()
    bot.effect_calls = []
    bot.dig = lambda *unused: bot.effect_calls.append("dig")
    bot.placeBlock = lambda *unused: bot.effect_calls.append("place")
    bot.attack = lambda *unused: bot.effect_calls.append("attack")
    bot.chat = lambda *unused: bot.effect_calls.append("chat")
    if action == "placeBlock":
        bot.blockAt = lambda pos: SimpleNamespace(
            name="air" if tuple(pos) == (1, 2, 3) else "stone")
    app = FastAPI()
    register_eac_preflight_route(
        app, bot_provider=lambda: bot, vec3_provider=lambda: (lambda *values: values))

    response = TestClient(app).post(
        "/post_eac_preflight", json={"action": action, "arguments": arguments})

    assert response.status_code == 200
    assert response.json() == {"status": True, "action": action}
    assert bot.effect_calls == []


def test_fastapi_preflight_route_rejects_invalid_request_without_effect():
    bot = Bot()
    bot.effect_calls = []
    bot.chat = lambda *unused: bot.effect_calls.append("chat")
    app = FastAPI()
    register_eac_preflight_route(
        app, bot_provider=lambda: bot, vec3_provider=lambda: (lambda *values: values))

    response = TestClient(app).post(
        "/post_eac_preflight",
        json={"action": "talkTo", "arguments": {"entity_name": "", "message": "hello"}},
    )

    assert response.status_code == 200
    assert response.json() == {"status": False, "action": "talkTo"}
    assert bot.effect_calls == []
