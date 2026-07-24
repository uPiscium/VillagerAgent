from __future__ import annotations

from typing import Any


def _empty_hand() -> dict[str, Any]:
    return {"id": None, "type": None, "name": None, "contained": [None, None, None]}


class MockTDWMATEnvironment:
    """Dependency-free fixture using the official TDW-MAT observation/action schema."""

    def __init__(self):
        self.steps = 0
        self.frames = 0
        self.transported = 0
        self.messages = [None, None]
        self.held = {"0": [_empty_hand(), _empty_hand()], "1": [_empty_hand(), _empty_hand()]}

    def reset(self, *, seed: int, options: dict[str, str]):
        self.seed = seed
        self.options = dict(options)
        self.steps = 0
        self.frames = 0
        self.transported = 0
        self.messages = [None, None]
        self.held = {"0": [_empty_hand(), _empty_hand()], "1": [_empty_hand(), _empty_hand()]}
        return self._observations(), {
            "goal_description": {"bread": 1},
            "rooms_name": ["<Kitchen> (1000)", "<Bedroom> (2000)"],
            "agent_colors": {},
        }, [{}, {}]

    def step(self, actions: dict[str, dict[str, Any]]):
        self.steps += 1
        self.frames += 5
        self.messages = [None, None]
        valid = {"0": True, "1": True}
        for agent_id, action in actions.items():
            action_type = action.get("type")
            if action_type == 6:
                self.messages[int(agent_id)] = str(action.get("message", ""))
            elif action_type == 3:
                if action.get("object") != 10 or self.held[agent_id][0]["id"] is not None:
                    valid[agent_id] = False
                else:
                    self.held[agent_id][0] = {
                        "id": 10, "type": 0, "name": "bread", "contained": [None, None, None]
                    }
            elif action_type == 5:
                arm_index = 0 if action.get("arm") == "left" else 1
                if self.held[agent_id][arm_index]["id"] is None:
                    valid[agent_id] = False
                else:
                    self.held[agent_id][arm_index] = _empty_hand()
                    self.transported = 1
        observations = self._observations(valid=valid)
        done = self.transported == 1
        return observations, 0.0, done, {
            "done": done,
            "num_frames_for_step": 5,
            "num_step": self.steps,
        }

    def check_goal(self):
        return self.transported, 1, self.transported == 1

    def _observations(self, *, valid: dict[str, bool] | None = None):
        valid = valid or {"0": True, "1": True}
        visible = {
            "0": [{"id": 10, "type": 0, "name": "bread", "seg_color": [1, 2, 3]}],
            "1": [{"id": 20, "type": 1, "name": "teatray", "seg_color": [4, 5, 6]}],
        }
        return {
            agent_id: {
                "visible_objects": visible[agent_id],
                "held_objects": [dict(row) for row in self.held[agent_id]],
                "oppo_held_objects": [dict(row) for row in self.held[str(1 - int(agent_id))]],
                "messages": list(self.messages),
                "valid": valid[agent_id],
                "current_frames": self.frames,
                "rgb": "omitted fixture image",
                "depth": "omitted fixture depth",
                "seg_mask": "omitted fixture mask",
            }
            for agent_id in ("0", "1")
        }


def mock_tdw_mat_env_factory(_config):
    return MockTDWMATEnvironment()
