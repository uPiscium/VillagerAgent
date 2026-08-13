"""Side-effect-free native legality checks for the classified EAC subset."""
from __future__ import annotations


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
                legal = block is not None and block.name != 'air'
            elif action_name == 'placeBlock':
                item_name = normalize(arguments.get('item_name'))
                facing = arguments.get('facing')
                aliases = {'default': 'A', 'up': 'y', 'down': 'y', 'north': 'z',
                           'south': 'z', 'west': 'x', 'east': 'x'}
                if isinstance(facing, str):
                    facing = aliases.get(facing.lower(), facing)
                block = native_bot.blockAt(target)
                legal = (facing in {'W', 'E', 'S', 'N', 'x', 'y', 'z', 'A', None}
                         and block is not None and block.name in {'air', 'water', 'dirt'}
                         and any(item.name == item_name and item.count > 0 for item in native_bot.inventory.items()))
            else:
                legal = native_bot.blockAt(target) is not None
        elif action_name == 'attackTarget':
            target_name = normalize(arguments.get('target_name'))
            legal = any(normalize(getattr(entity, 'name', None)) == target_name
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
