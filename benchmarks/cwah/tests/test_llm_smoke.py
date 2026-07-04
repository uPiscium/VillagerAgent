from benchmarks.common.actions import ActionSpec, InformationActionSpec
from benchmarks.cwah.llm_smoke import action_from_decision, summarize_action_intents, summarize_observations


def test_action_from_decision_uses_selected_legal_action_id():
    legal_actions = (
        ActionSpec(action_id="wait:agent_0", action_type="wait", parameters={}),
        ActionSpec(action_id="walktowards:agent_0:20", action_type="walktowards", parameters={"object_id": 20}),
    )

    action = action_from_decision("agent_0", {"action_id": "walktowards:agent_0:20"}, legal_actions)

    assert action.action_type == "walktowards"
    assert action.parameters == {"object_id": 20}


def test_action_from_decision_adds_message_to_selected_send_message_action():
    legal_actions = (
        InformationActionSpec(
            action_id="send_message:agent_0",
            action_type="send_message",
            parameters={"message": ""},
            information_subtype="send_message",
        ),
    )

    action = action_from_decision(
        "agent_0",
        {"action_id": "send_message:agent_0", "message": "I found the plate."},
        legal_actions,
    )

    assert action.action_type == "send_message"
    assert action.parameters == {"message": "I found the plate."}


def test_action_from_decision_falls_back_for_legacy_action_type_decisions():
    action = action_from_decision("agent_0", {"action_type": "wait"}, ())

    assert action.action_type == "wait"


def test_action_from_decision_prefers_physical_action_after_warmup():
    legal_actions = (
        ActionSpec(action_id="wait:agent_0", action_type="wait", parameters={}),
        ActionSpec(action_id="walktowards:agent_0:20", action_type="walktowards", parameters={"object_id": 20}),
        ActionSpec(action_id="grab:agent_0:20", action_type="grab", parameters={"object_id": 20}),
        InformationActionSpec(
            action_id="send_message:agent_0",
            action_type="send_message",
            parameters={"message": ""},
            information_subtype="send_message",
        ),
    )
    decision = {"action_id": "send_message:agent_0", "message": "I can see a plate."}

    action = action_from_decision("agent_0", decision, legal_actions, prefer_physical=True)

    assert action.action_id == "grab:agent_0:20"
    assert decision["policy_override"] == {
        "reason": "prefer_physical_after_steps",
        "action_id": "grab:agent_0:20",
        "action_type": "grab",
    }


def test_action_from_decision_skips_blocked_failed_actions():
    legal_actions = (
        ActionSpec(action_id="wait:agent_0", action_type="wait", parameters={}),
        ActionSpec(action_id="grab:agent_0:20", action_type="grab", parameters={"object_id": 20}),
        ActionSpec(action_id="grab:agent_0:21", action_type="grab", parameters={"object_id": 21}),
    )
    decision = {"action_id": "grab:agent_0:20"}

    action = action_from_decision(
        "agent_0",
        decision,
        legal_actions,
        prefer_physical=True,
        blocked_action_ids={"grab:agent_0:20"},
    )

    assert action.action_id == "grab:agent_0:21"
    assert decision["policy_override"] == {
        "reason": "prefer_physical_after_steps",
        "action_id": "grab:agent_0:21",
        "action_type": "grab",
    }


def test_summarize_observations_extracts_visible_objects_rooms_and_messages():
    summary = summarize_observations((
        {
            "source_kind": "environment_observation",
            "proposition": {"predicate": "object_visible", "subject": "20", "object": "plate"},
            "grounding": {"node": {"id": 20, "class_name": "plate", "category": "Objects", "properties": ["GRABBABLE"], "states": []}},
        },
        {
            "source_kind": "environment_observation",
            "proposition": {"predicate": "object_visible", "subject": "10", "object": "kitchen"},
            "grounding": {"node": {"id": 10, "class_name": "kitchen", "category": "Rooms", "properties": [], "states": []}},
        },
        {
            "source_kind": "environment_observation",
            "proposition": {"predicate": "holds_rh", "subject": "1", "object": "20"},
            "grounding": {"edge": {"from_id": 1, "to_id": 20, "relation_type": "HOLDS_RH"}},
        },
        {
            "source_kind": "agent_message",
            "proposition": {"predicate": "reported_message", "subject": "agent_1", "object": "I found a cupcake."},
            "grounding": {"message": "I found a cupcake."},
        },
    ))

    assert summary["visible_objects"] == [{"id": 20, "class_name": "plate", "category": "Objects", "properties": ["GRABBABLE"], "states": []}]
    assert summary["visible_rooms"] == [{"id": 10, "class_name": "kitchen", "category": "Rooms", "properties": [], "states": []}]
    assert summary["held_objects"] == [{"from_id": 1, "from_name": None, "relation_type": "HOLDS_RH", "to_id": 20, "to_name": "plate"}]
    assert summary["recent_messages"] == ["I found a cupcake."]


def test_summarize_action_intents_describes_task_sequence_actions():
    intents = summarize_action_intents((
        ActionSpec(action_id="grab:agent_0:20", action_type="grab", parameters={"object_name": "plate"}),
        ActionSpec(action_id="putin:agent_0:20:30", action_type="putin", parameters={"object_name": "plate", "target_name": "dishwasher"}),
    ))

    assert intents == [
        {"action_id": "grab:agent_0:20", "action_type": "grab", "intent": "pick up plate"},
        {"action_id": "putin:agent_0:20:30", "action_type": "putin", "intent": "place plate at dishwasher"},
    ]
