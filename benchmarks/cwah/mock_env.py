from __future__ import annotations


class MockCWAHEnvironment:
    def __init__(self):
        self.steps = 0
        self.messages = [None, None]
        self.finished = False
        self.task_goal = {0: {"inside_plate_30": 1}, 1: {"inside_cupcake_30": 1}}
        self.goal_spec = {0: {"inside_plate_<dishwasher> (30)": [1, True, 2]}, 1: {"inside_cupcake_<dishwasher> (30)": [1, True, 2]}}
        self._observations = {
            0: {
                "nodes": [
                    {"id": 1, "class_name": "character", "category": "Characters", "states": [], "properties": []},
                    {"id": 10, "class_name": "kitchen", "category": "Rooms", "states": [], "properties": []},
                    {"id": 20, "class_name": "plate", "category": "Objects", "states": [], "properties": ["GRABBABLE"]},
                ],
                "edges": [
                    {"from_id": 1, "to_id": 10, "relation_type": "INSIDE"},
                    {"from_id": 20, "to_id": 10, "relation_type": "INSIDE"},
                ],
                "messages": self.messages,
                "location": [0, 0, 0],
            },
            1: {
                "nodes": [
                    {"id": 2, "class_name": "character", "category": "Characters", "states": [], "properties": []},
                    {"id": 11, "class_name": "livingroom", "category": "Rooms", "states": [], "properties": []},
                    {"id": 21, "class_name": "cupcake", "category": "Objects", "states": [], "properties": ["GRABBABLE"]},
                ],
                "edges": [
                    {"from_id": 2, "to_id": 11, "relation_type": "INSIDE"},
                    {"from_id": 21, "to_id": 11, "relation_type": "INSIDE"},
                ],
                "messages": self.messages,
                "location": [1, 0, 0],
            },
        }

    def seed(self, seed: int) -> None:
        self.seed_value = seed

    def reset(self, task_id: int = 0):
        self.task_id = task_id
        self.steps = 0
        self.finished = False
        self.messages = [None, None]
        return self.get_observations()

    def get_observations(self):
        observations = {}
        for agent_id, obs in self._observations.items():
            copied = {key: value for key, value in obs.items()}
            copied["messages"] = list(self.messages)
            observations[agent_id] = copied
        return observations

    def get_action_space(self):
        return {0: [10, 20], 1: [11, 21]}

    def step(self, action_dict):
        self.steps += 1
        self.messages = [None, None]
        for agent_id, action in action_dict.items():
            if isinstance(action, str) and action.startswith("[send_message]"):
                self.messages[agent_id] = action.replace("[send_message]", "", 1).strip()
        self.finished = self.steps >= 2
        return self.get_observations(), 0.0, self.finished, {
            "finished": self.finished,
            "failed_exec": False,
            "progress": {
                "satisfied": {"inside_plate_dishwasher": ["inside_20_30"]} if self.finished else {},
                "unsatisfied": {"inside_plate_dishwasher": 0 if self.finished else 1},
            },
        }, list(self.messages)


def mock_cwah_env_factory(_config):
    return MockCWAHEnvironment()
