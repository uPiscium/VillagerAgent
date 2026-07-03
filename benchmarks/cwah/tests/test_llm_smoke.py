from benchmarks.common.actions import ActionSpec, InformationActionSpec
from benchmarks.cwah.llm_smoke import action_from_decision, summarize_observations


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
        InformationActionSpec(
            action_id="send_message:agent_0",
            action_type="send_message",
            parameters={"message": ""},
            information_subtype="send_message",
        ),
    )
    decision = {"action_id": "send_message:agent_0", "message": "I can see a plate."}

    action = action_from_decision("agent_0", decision, legal_actions, prefer_physical=True)

    assert action.action_id == "walktowards:agent_0:20"
    assert decision["policy_override"] == {
        "reason": "prefer_physical_after_steps",
        "action_id": "walktowards:agent_0:20",
    }


def test_summarize_observations_extracts_visible_objects_rooms_and_messages():
    summary = summarize_observations((
        {
            "source_kind": "environment_observation",
            "proposition": {"predicate": "object_visible", "subject": "20", "object": "plate"},
            "grounding": {"node": {"id": 20, "class_name": "plate", "category": "Objects"}},
        },
        {
            "source_kind": "environment_observation",
            "proposition": {"predicate": "object_visible", "subject": "10", "object": "kitchen"},
            "grounding": {"node": {"id": 10, "class_name": "kitchen", "category": "Rooms"}},
        },
        {
            "source_kind": "agent_message",
            "proposition": {"predicate": "reported_message", "subject": "agent_1", "object": "I found a cupcake."},
            "grounding": {"message": "I found a cupcake."},
        },
    ))

    assert summary["visible_objects"] == [{"id": 20, "class_name": "plate", "category": "Objects"}]
    assert summary["visible_rooms"] == [{"id": 10, "class_name": "kitchen", "category": "Rooms"}]
    assert summary["recent_messages"] == ["I found a cupcake."]
