import asyncio
import importlib
import sys
import types


def test_enchanting_table_interaction_returns_structured_unsupported(monkeypatch):
    env_api = _import_env_api_with_fake_javascript(monkeypatch)

    message, status, detail = asyncio.run(
        env_api.interact_nearest(None, _Bot(), None, {}, None, 3, "enchanting_table", get_item_name="sword")
    )

    assert status is False
    assert "Unsupported Minecraft tool interaction" in message
    assert detail == {
        "error_type": "unsupported_tool",
        "tool": "enchanting_table",
        "supported": False,
    }


def test_anvil_interaction_returns_structured_unsupported(monkeypatch):
    env_api = _import_env_api_with_fake_javascript(monkeypatch)

    message, status, detail = asyncio.run(
        env_api.interact_nearest(None, _Bot(), None, {}, None, 3, "anvil", repair_item_name="sword")
    )

    assert status is False
    assert "Unsupported Minecraft tool interaction" in message
    assert detail == {
        "error_type": "unsupported_tool",
        "tool": "anvil",
        "supported": False,
    }


def _import_env_api_with_fake_javascript(monkeypatch):
    original_stdout = sys.stdout
    fake_javascript = types.ModuleType("javascript")
    fake_javascript.require = lambda *args, **kwargs: None
    fake_javascript.On = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "javascript", fake_javascript)
    sys.modules.pop("env.env_api", None)
    module = importlib.import_module("env.env_api")
    monkeypatch.setattr(sys, "stdout", original_stdout)
    return module


class _Bot:
    heldItem = None

    def findBlock(self, *args, **kwargs):
        raise AssertionError("unsupported tools should return before bot interaction")
