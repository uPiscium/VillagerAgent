"""Side-effect-free native legality checks for the classified EAC subset."""
from __future__ import annotations

import json
from pathlib import Path
from fastapi import Request
from fastapi.responses import JSONResponse

_DIG_DATA = json.loads((Path(__file__).resolve().parents[1] / "data/dig_item.json").read_text())


def register_eac_preflight_route(app, *, bot_provider, vec3_provider,
                                 timeout_decorator=lambda unused: (lambda function: function)):
    """Install the production read-only EAC preflight route on a FastAPI app."""
    @app.post('/post_eac_preflight')
    @timeout_decorator(10)
    async def eac_preflight(request: Request):
        data = await request.json()
        action_name, arguments = data.get('action'), data.get('arguments', {})
        return JSONResponse({
            'status': evaluate_eac_preflight(
                action_name, arguments, bot_provider(), vec3_provider()),
            'action': action_name,
        })

    return eac_preflight


def evaluate_eac_preflight(action_name, arguments, native_bot, Vec3):
    if not isinstance(arguments, dict):
        return False
    try:
        normalize = lambda value: value.lower().replace(' ', '_') if isinstance(value, str) else value
        if action_name in {'MineBlock', 'placeBlock', 'navigateTo'}:
            coordinates = tuple(arguments.get(key) for key in ('x', 'y', 'z'))
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in coordinates):
                return False
            target = Vec3(*coordinates)
            if action_name == 'MineBlock':
                block = native_bot.blockAt(target)
                legal = block is not None and block.name != 'air' and _diggable(native_bot, block.name)
            elif action_name == 'placeBlock':
                item_name = normalize(arguments.get('item_name'))
                facing = arguments.get('facing')
                aliases = {'default': 'A', 'up': 'y', 'down': 'y', 'north': 'z',
                           'south': 'z', 'west': 'x', 'east': 'x'}
                if isinstance(facing, str):
                    facing = aliases.get(facing.lower(), facing)
                block = native_bot.blockAt(target)
                legal = (facing in {'W', 'E', 'S', 'N', 'x', 'y', 'z', 'A', None}
                         and block is not None and block.name in {'air', 'flower_pot', item_name}
                         and any(item.name == item_name and item.count > 0 for item in native_bot.inventory.items())
                         and _placement_reference(native_bot, target, facing, Vec3))
            else:
                legal = native_bot.blockAt(target) is not None
        elif action_name == 'attackTarget':
            target_name = normalize(arguments.get('target_name'))
            legal = target_name in _ATTACKABLE and any(
                normalize(getattr(entity, 'name', None)) == target_name
                or normalize(getattr(entity, 'username', None)) == target_name
                for entity in native_bot.entities.values())
        elif action_name == 'handoverBlock':
            item_name, count = normalize(arguments.get('item_name')), arguments.get('item_count')
            target_name = normalize(arguments.get('target_player_name'))
            target_exists = any(normalize(getattr(entity, 'username', None)) == target_name
                                for entity in native_bot.entities.values())
            legal = (isinstance(count, int) and not isinstance(count, bool) and count > 0
                     and target_exists
                     and sum(item.count for item in native_bot.inventory.items() if item.name == item_name) >= count)
        elif action_name == 'scanNearbyEntities':
            item_name, radius, item_num = arguments.get('item_name'), arguments.get('radius'), arguments.get('item_num')
            legal = (isinstance(item_name, str) and bool(item_name.strip())
                     and isinstance(radius, (int, float)) and not isinstance(radius, bool) and radius > 0
                     and isinstance(item_num, int) and not isinstance(item_num, bool) and item_num > 0)
        elif action_name == 'talkTo':
            entity_name, message = arguments.get('entity_name'), arguments.get('message')
            legal = (isinstance(entity_name, str) and bool(entity_name.strip())
                     and isinstance(message, str) and bool(message.strip()))
        elif action_name == 'waitForFeedback':
            entity_name, seconds = arguments.get('entity_name'), arguments.get('seconds')
            legal = (isinstance(entity_name, str) and bool(entity_name.strip())
                     and isinstance(seconds, int) and not isinstance(seconds, bool) and 0 < seconds <= 30)
        else:
            legal = False
        return bool(legal)
    except Exception:
        return False


_ATTACKABLE = frozenset({
    'rabbit', 'bat', 'sheep', 'cat', 'chicken', 'wolf', 'cod', 'cow', 'fox', 'pig',
    'horse', 'turtle', 'parrot', 'panda', 'blaze', 'cave_spider', 'creeper', 'drowned',
    'elder_guardian', 'ender_dragon', 'enderman', 'endermite', 'evoker', 'ghast',
    'guardian', 'hoglin', 'husk', 'illusioner', 'magma_cube', 'phantom', 'piglin',
    'piglin_brute', 'pillager', 'ravager', 'shulker', 'silverfish', 'skeleton', 'slime',
    'spider', 'stray', 'vex', 'vindicator', 'witch', 'wither', 'wither_skeleton',
    'zoglin', 'zombie', 'zombie_villager', 'zombified_piglin',
})


def _diggable(bot, block_name):
    item = next((item for item in _DIG_DATA if item['name'] == block_name), None)
    if item is None or not item['diggable']:
        return False
    held = bot.heldItem
    return 'tools' not in item or (held is not None and held.name in item['tools'])


def _placement_reference(bot, target, facing, Vec3):
    coordinates = tuple(getattr(target, name) for name in ('x', 'y', 'z')) if hasattr(target, 'x') else tuple(target)
    offsets = {'N': (0, 0, -1), 'S': (0, 0, 1), 'W': (-1, 0, 0), 'E': (1, 0, 0)}
    required = offsets.get(facing)
    if required is not None:
        return bot.blockAt(Vec3(coordinates[0] + required[0], coordinates[1] + required[1], coordinates[2] + required[2])).name != 'air'
    axes = {
        'x': ((-1, 0, 0), (1, 0, 0)),
        'y': ((0, -1, 0), (0, 1, 0)),
        'z': ((0, 0, -1), (0, 0, 1)),
    }
    candidates = axes.get(facing, ((0, -1, 0), (0, 1, 0), (-1, 0, 0),
                                   (1, 0, 0), (0, 0, -1), (0, 0, 1)))
    return any(bot.blockAt(Vec3(coordinates[0] + dx, coordinates[1] + dy, coordinates[2] + dz)).name != 'air'
               for dx, dy, dz in candidates)
