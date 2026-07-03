from benchmarks.common.actions import ActionSpec, InformationActionSpec
from benchmarks.cwah.llm_smoke import action_from_decision


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
