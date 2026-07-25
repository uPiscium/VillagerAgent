from __future__ import annotations

from typing import Any

from benchmarks.partnr.adapter import PARTNRConfig


class FixturePARTNREnvironment:
    """Dependency-free fixture matching PARTNR episode, WorldGraph, and Tool shapes."""

    def __init__(self, config: PARTNRConfig):
        self.config = config
        self.steps = 0
        self.progress = 0.0
        self.held = False
        self.failed_once = False
        self.feedback: dict[str, list[dict[str, Any]]] = {agent: [] for agent in config.agent_ids}

    def reset(self, *, seed: int):
        self.seed = seed
        self.steps = 0
        self.progress = 0.0
        self.held = False
        self.failed_once = False
        self.feedback = {agent: [] for agent in self.config.agent_ids}
        return self._observations(), self._info(
            action_succeeded=True,
            response="reset",
            include_private_evaluator=True,
        )

    def step(self, *, agent_id: str, tool_name: str, arguments: dict[str, Any]):
        self.steps += 1
        succeeded = True
        response = "Successful execution!"
        if tool_name == "FindObjectTool":
            response = "apple_0 is on table_0 in kitchen_0"
        elif tool_name == "Navigate":
            response = f"Reached {arguments.get('target', 'target')}"
        elif tool_name == "Pick":
            if self.failed_once:
                self.held = True
                self.progress = max(self.progress, 0.5)
            else:
                self.failed_once = True
                succeeded = False
                response = "Unexpected failure! - object is not yet reachable"
        elif tool_name == "Place":
            if not self.held:
                succeeded = False
                response = "Unexpected failure! - agent is not holding the object"
            else:
                self.held = False
                self.progress = 1.0
        else:
            succeeded = False
            response = f"Unexpected failure! - unsupported tool {tool_name}"
        self.feedback[agent_id] = [{
            "tool": tool_name,
            "arguments": dict(arguments),
            "succeeded": succeeded,
            "response": response,
        }]
        done = self.progress == 1.0
        return self._observations(), done, self._info(
            action_succeeded=succeeded,
            response=response,
            include_private_evaluator=True,
        )

    def _observations(self):
        apple_states = {"is_held": self.held, "is_clean": True}
        return {
            "agent_0": {
                "entities": [
                    {
                        "name": "kitchen_0",
                        "entity_type": "room",
                        "translation": [0.0, 0.0, 0.0],
                    },
                    {
                        "name": "apple_0",
                        "entity_type": "object",
                        "relation": "held_by" if self.held else "on",
                        "target": "agent_0" if self.held else "table_0",
                        "states": apple_states,
                        "sim_handle": "apple_:0000",
                    },
                ],
                "action_candidates": self._agent_zero_candidates(),
                "action_feedback": self.feedback["agent_0"],
                "evaluation_propositions": [{"function_name": "is_on_top"}],
                "full_world_graph": {"private": "must not escape"},
            },
            "agent_1": {
                "entities": [
                    {"name": "living_room_0", "entity_type": "room"},
                    {
                        "name": "basket_0",
                        "entity_type": "receptacle",
                        "relation": "in",
                        "target": "living_room_0",
                        "sim_handle": "basket_:0000",
                    },
                ],
                "action_candidates": [{
                    "candidate_id": "navigate:agent_1:kitchen_0",
                    "tool": "Navigate",
                    "arguments": {"target": "kitchen_0"},
                    "precondition_status": "executable_now",
                }],
                "action_feedback": self.feedback["agent_1"],
            },
        }

    def _agent_zero_candidates(self):
        candidates = [
            {
                "candidate_id": "find:agent_0:apple_0",
                "tool": "FindObjectTool",
                "arguments": {"query": "apple_0"},
                "precondition_status": "executable_now",
            },
            {
                "candidate_id": "navigate:agent_0:kitchen_0",
                "tool": "Navigate",
                "arguments": {"target": "kitchen_0"},
                "precondition_status": "executable_now",
            },
            {
                "candidate_id": "pick:agent_0:apple_0",
                "tool": "Pick",
                "arguments": {"object": "apple_0"},
                "precondition_status": "uncertain",
                "precondition_reason": "world_graph_presence_does_not_guarantee_reachability",
                "requires": ["agent_0:entity:apple_0"],
            },
        ]
        if self.held:
            candidates.append({
                "candidate_id": "place:agent_0:apple_0:basket_0",
                "tool": "Place",
                "arguments": {"object": "apple_0", "receptacle": "basket_0"},
                "precondition_status": "uncertain",
                "precondition_reason": "basket_location_report_requires_navigation",
                "requires": ["agent_0:feedback:0:Pick"],
            })
        return candidates

    def _info(self, *, action_succeeded: bool, response: str, include_private_evaluator: bool):
        evaluator = {
            "task_percent_complete": self.progress,
            "task_state_success": float(self.progress == 1.0),
            "task_explanation": None if self.progress == 1.0 else "apple is not in basket",
            "evaluation_propositions": [
                {"function_name": "is_inside", "args": {"object_handles": ["apple_0"]}}
            ],
        }
        return {
            "tools": {
                "agent_0": [
                    "Navigate", "Pick", "Place", "FindObjectTool", "FindAgentActionTool"
                ],
                "agent_1": ["Navigate", "FindObjectTool", "FindAgentActionTool"],
            },
            "action_succeeded": action_succeeded,
            "response": response,
            "sim_step_count": self.steps,
            "evaluator": evaluator if include_private_evaluator else {},
        }


def fixture_partnr_env_factory(config: PARTNRConfig) -> FixturePARTNREnvironment:
    return FixturePARTNREnvironment(config)
