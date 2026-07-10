from benchmarks.common.actions import ActionSpec, InformationActionSpec
from benchmarks.cwah.llm_smoke import (
    action_failure_signature,
    action_navigation_signature,
    action_from_decision,
    physical_action_rank,
    preferred_physical_action,
    summarize_action_intents,
    summarize_observations,
)


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


def test_action_from_decision_skips_blocked_failed_action_signatures():
    legal_actions = (
        ActionSpec(action_id="wait:agent_0", action_type="wait", parameters={}),
        ActionSpec(action_id="putback:agent_0:20:30", action_type="putback", parameters={"object_id": 20, "target_id": 30}),
        ActionSpec(action_id="walktowards:agent_0:31", action_type="walktowards", parameters={"object_id": 31}),
    )
    decision = {"action_id": "putback:agent_0:20:30"}

    action = action_from_decision(
        "agent_0",
        decision,
        legal_actions,
        prefer_physical=True,
        blocked_action_signatures={"putback:20:30"},
    )

    assert action.action_id == "walktowards:agent_0:31"
    assert decision["policy_override"] == {
        "reason": "prefer_physical_after_steps",
        "action_id": "walktowards:agent_0:31",
        "action_type": "walktowards",
    }


def test_preferred_physical_action_filters_failed_signatures():
    legal_actions = (
        ActionSpec(
            action_id="putin:agent_0:20:30",
            action_type="putin",
            parameters={
                "object_id": 20,
                "target_id": 30,
                "goal_object_match": True,
                "goal_target_match": True,
                "goal_relation_matches": ("inside",),
            },
        ),
        ActionSpec(action_id="grab:agent_0:21", action_type="grab", parameters={"object_id": 21}),
    )

    action = preferred_physical_action(legal_actions, blocked_action_signatures={"putin:20:30"})

    assert action is not None
    assert action.action_id == "grab:agent_0:21"


def test_preferred_physical_action_filters_suppressed_navigation_signatures():
    legal_actions = (
        ActionSpec(action_id="walktowards:agent_0:20", action_type="walktowards", parameters={"object_id": 20, "goal_object_match": True}),
        ActionSpec(action_id="walktowards:agent_0:21", action_type="walktowards", parameters={"object_id": 21}),
    )

    action = preferred_physical_action(legal_actions, blocked_action_signatures={"walktowards:20:"})

    assert action is not None
    assert action.action_id == "walktowards:agent_0:21"


def test_preferred_physical_action_filters_failed_open_targets():
    legal_actions = (
        ActionSpec(action_id="open:agent_0:30", action_type="open", parameters={"object_id": 30, "precondition_status": "executable_now"}),
        ActionSpec(action_id="walktowards:agent_0:20", action_type="walktowards", parameters={"object_id": 20, "goal_object_match": True}),
    )

    action = preferred_physical_action(legal_actions, blocked_open_target_ids={"30"})

    assert action is not None
    assert action.action_id == "walktowards:agent_0:20"


def test_action_from_decision_avoids_suppressed_navigation_selection():
    legal_actions = (
        ActionSpec(action_id="walktowards:agent_0:20", action_type="walktowards", parameters={"object_id": 20, "goal_object_match": True}),
        ActionSpec(action_id="grab:agent_0:21", action_type="grab", parameters={"object_id": 21}),
    )
    decision = {"action_id": "walktowards:agent_0:20"}

    action = action_from_decision(
        "agent_0",
        decision,
        legal_actions,
        prefer_physical=True,
        blocked_action_signatures={"walktowards:20:"},
    )

    assert action.action_id == "grab:agent_0:21"
    assert decision["policy_override"] == {
        "reason": "prefer_physical_after_steps",
        "action_id": "grab:agent_0:21",
        "action_type": "grab",
    }


def test_action_from_decision_avoids_repeated_failed_open_selection():
    legal_actions = (
        ActionSpec(action_id="open:agent_0:30", action_type="open", parameters={"object_id": 30, "precondition_status": "executable_now"}),
        ActionSpec(action_id="walktowards:agent_0:20", action_type="walktowards", parameters={"object_id": 20, "goal_object_match": True}),
    )
    decision = {"action_id": "open:agent_0:30"}

    action = action_from_decision(
        "agent_0",
        decision,
        legal_actions,
        prefer_physical=True,
        blocked_open_target_ids={"30"},
    )

    assert action.action_id == "walktowards:agent_0:20"
    assert decision["policy_override"] == {
        "reason": "prefer_physical_after_steps",
        "action_id": "walktowards:agent_0:20",
        "action_type": "walktowards",
    }


def test_action_failure_signature_uses_local_action_parameters():
    action = ActionSpec(action_id="putback:agent_0:20:30", action_type="putback", parameters={"object_id": 20, "target_id": 30})

    assert action_failure_signature(action) == "putback:20:30"


def test_action_navigation_signature_only_tracks_walktowards():
    walk = ActionSpec(action_id="walktowards:agent_0:20", action_type="walktowards", parameters={"object_id": 20})
    grab = ActionSpec(action_id="grab:agent_0:20", action_type="grab", parameters={"object_id": 20})

    assert action_navigation_signature(walk) == "walktowards:20:"
    assert action_navigation_signature(grab) == ""


def test_action_from_decision_overrides_setup_required_selection():
    legal_actions = (
        ActionSpec(
            action_id="walktowards:agent_0:20",
            action_type="walktowards",
            parameters={"object_id": 20, "goal_object_match": True, "precondition_status": "executable_now"},
        ),
        ActionSpec(
            action_id="grab:agent_0:20",
            action_type="grab",
            parameters={
                "object_id": 20,
                "goal_object_match": True,
                "precondition_status": "setup_required",
                "setup_action_id": "walktowards:agent_0:20",
            },
        ),
    )
    decision = {"action_id": "grab:agent_0:20"}

    action = action_from_decision("agent_0", decision, legal_actions, prefer_physical=True)

    assert action.action_id == "walktowards:agent_0:20"
    assert decision["policy_override"] == {
        "reason": "prefer_physical_after_steps",
        "action_id": "walktowards:agent_0:20",
        "action_type": "walktowards",
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
        {
            "source_kind": "task_goal",
            "proposition": {"predicate": "task_goal", "relation": "inside", "object_class": "plate", "target_id": 30, "target_class": "dishwasher", "count": 1},
            "grounding": {"task_goal_hint": {"relation": "inside", "object_class": "plate", "target_id": 30, "target_class": "dishwasher", "count": 1}},
        },
    ))

    assert summary["visible_objects"] == [{"id": 20, "class_name": "plate", "category": "Objects", "properties": ["GRABBABLE"], "states": []}]
    assert summary["visible_rooms"] == [{"id": 10, "class_name": "kitchen", "category": "Rooms", "properties": [], "states": []}]
    assert summary["task_goals"] == [{"relation": "inside", "object_class": "plate", "target_id": 30, "target_class": "dishwasher", "count": 1}]
    assert summary["held_objects"] == [{"from_id": 1, "from_name": None, "relation_type": "HOLDS_RH", "to_id": 20, "to_name": "plate"}]
    assert summary["recent_messages"] == ["I found a cupcake."]


def test_summarize_action_intents_describes_task_sequence_actions():
    intents = summarize_action_intents((
        ActionSpec(action_id="grab:agent_0:20", action_type="grab", parameters={"object_name": "plate", "goal_object_match": True}),
        ActionSpec(action_id="putin:agent_0:20:30", action_type="putin", parameters={"object_name": "plate", "target_name": "dishwasher", "goal_object_match": True, "goal_target_match": True, "goal_relation_matches": ("inside",)}),
    ))

    assert intents == [
        {
            "action_id": "grab:agent_0:20",
            "action_type": "grab",
            "intent": "pick up plate",
            "precondition_status": "unknown",
            "precondition_reason": "",
            "setup_action_id": "",
            "hand_state": "unknown",
            "held_object_id": None,
            "held_object_name": "",
            "placement_relation": "",
            "placement_relation_compatibility": "",
            "target_affordance": "",
            "placement_suitability": "",
            "container_suitability": "",
            "goal_object_match": True,
            "goal_target_match": False,
            "goal_relation_matches": [],
        },
        {
            "action_id": "putin:agent_0:20:30",
            "action_type": "putin",
            "intent": "place plate at dishwasher",
            "precondition_status": "unknown",
            "precondition_reason": "",
            "setup_action_id": "",
            "hand_state": "unknown",
            "held_object_id": None,
            "held_object_name": "",
            "placement_relation": "",
            "placement_relation_compatibility": "",
            "target_affordance": "",
            "placement_suitability": "",
            "container_suitability": "",
            "goal_object_match": True,
            "goal_target_match": True,
            "goal_relation_matches": ["inside"],
        },
    ]


def test_physical_action_rank_prioritizes_goal_sequence():
    actions = (
        ActionSpec(action_id="grab:agent_0:chair", action_type="grab", parameters={"object_name": "chair"}),
        ActionSpec(action_id="walktowards:agent_0:plate", action_type="walktowards", parameters={"object_name": "plate", "goal_object_match": True}),
        ActionSpec(action_id="grab:agent_0:plate", action_type="grab", parameters={"object_name": "plate", "goal_object_match": True}),
        ActionSpec(action_id="putin:agent_0:plate:dishwasher", action_type="putin", parameters={"object_name": "plate", "target_name": "dishwasher", "goal_object_match": True, "goal_target_match": True, "goal_relation_matches": ("inside",)}),
    )

    assert min(actions, key=physical_action_rank).action_id == "putin:agent_0:plate:dishwasher"


def test_physical_action_rank_prefers_setup_navigation_before_blocked_goal_action():
    actions = (
        ActionSpec(
            action_id="grab:agent_0:20",
            action_type="grab",
            parameters={
                "object_name": "plate",
                "goal_object_match": True,
                "precondition_status": "setup_required",
                "setup_action_id": "walktowards:agent_0:20",
            },
        ),
        ActionSpec(
            action_id="walktowards:agent_0:20",
            action_type="walktowards",
            parameters={"object_name": "plate", "goal_object_match": True, "precondition_status": "executable_now"},
        ),
    )

    assert min(actions, key=physical_action_rank).action_id == "walktowards:agent_0:20"


def test_physical_action_rank_deprioritizes_fallback_receptacle_placement():
    actions = (
        ActionSpec(
            action_id="putback:agent_0:plate:bowl",
            action_type="putback",
            parameters={"object_name": "plate", "target_name": "bowl", "placement_suitability": "fallback_receptacle", "precondition_status": "executable_now"},
        ),
        ActionSpec(action_id="walktowards:agent_0:table", action_type="walktowards", parameters={"object_name": "table", "precondition_status": "executable_now"}),
    )

    assert min(actions, key=physical_action_rank).action_id == "walktowards:agent_0:table"


def test_physical_action_rank_blocks_extra_grab_while_holding():
    actions = (
        ActionSpec(
            action_id="grab:agent_0:apple",
            action_type="grab",
            parameters={"object_name": "apple", "precondition_status": "blocked", "precondition_reason": "blocked_by_holding_object"},
        ),
        ActionSpec(
            action_id="putin:agent_0:plate:dishwasher",
            action_type="putin",
            parameters={
                "object_name": "plate",
                "target_name": "dishwasher",
                "goal_object_match": True,
                "goal_target_match": True,
                "goal_relation_matches": ("inside",),
                "precondition_status": "executable_now",
            },
        ),
    )

    assert min(actions, key=physical_action_rank).action_id == "putin:agent_0:plate:dishwasher"


def test_physical_action_rank_prefers_open_setup_for_closed_target():
    actions = (
        ActionSpec(
            action_id="putin:agent_0:plate:dishwasher",
            action_type="putin",
            parameters={
                "object_name": "plate",
                "target_name": "dishwasher",
                "goal_object_match": True,
                "goal_target_match": True,
                "goal_relation_matches": ("inside",),
                "precondition_status": "setup_required",
                "precondition_reason": "needs_open_target",
                "setup_action_id": "open:agent_0:dishwasher",
            },
        ),
        ActionSpec(
            action_id="open:agent_0:dishwasher",
            action_type="open",
            parameters={"object_name": "dishwasher", "goal_target_match": True, "precondition_status": "executable_now"},
        ),
    )

    assert min(actions, key=physical_action_rank).action_id == "open:agent_0:dishwasher"


def test_physical_action_rank_deprioritizes_putin_for_on_goal():
    actions = (
        ActionSpec(
            action_id="putin:agent_0:plate:dishwasher",
            action_type="putin",
            parameters={
                "object_name": "plate",
                "target_name": "dishwasher",
                "goal_object_match": True,
                "goal_target_match": True,
                "goal_relation_matches": ("on",),
                "container_suitability": "container_likely_unsuitable",
                "precondition_status": "executable_now",
            },
        ),
        ActionSpec(
            action_id="putback:agent_0:plate:table",
            action_type="putback",
            parameters={
                "object_name": "plate",
                "target_name": "table",
                "goal_object_match": True,
                "goal_target_match": False,
                "precondition_status": "executable_now",
            },
        ),
    )

    assert min(actions, key=physical_action_rank).action_id == "putback:agent_0:plate:table"


def test_physical_action_rank_prefers_matching_on_relation_placement():
    actions = (
        ActionSpec(
            action_id="putin:agent_0:plate:dishwasher",
            action_type="putin",
            parameters={
                "object_name": "plate",
                "target_name": "dishwasher",
                "goal_object_match": True,
                "goal_target_match": True,
                "placement_relation_compatibility": "goal_relation_mismatch",
                "precondition_status": "executable_now",
            },
        ),
        ActionSpec(
            action_id="putback:agent_0:plate:table",
            action_type="putback",
            parameters={
                "object_name": "plate",
                "target_name": "table",
                "goal_object_match": True,
                "goal_target_match": True,
                "placement_relation_compatibility": "goal_relation_match",
                "precondition_status": "executable_now",
            },
        ),
    )

    assert min(actions, key=physical_action_rank).action_id == "putback:agent_0:plate:table"


def test_physical_action_rank_prefers_matching_inside_relation_placement():
    actions = (
        ActionSpec(
            action_id="putback:agent_0:plate:table",
            action_type="putback",
            parameters={
                "object_name": "plate",
                "target_name": "table",
                "goal_object_match": True,
                "goal_target_match": True,
                "placement_relation_compatibility": "goal_relation_mismatch",
                "precondition_status": "executable_now",
            },
        ),
        ActionSpec(
            action_id="putin:agent_0:plate:dishwasher",
            action_type="putin",
            parameters={
                "object_name": "plate",
                "target_name": "dishwasher",
                "goal_object_match": True,
                "goal_target_match": True,
                "placement_relation_compatibility": "goal_relation_match",
                "precondition_status": "executable_now",
            },
        ),
    )

    assert min(actions, key=physical_action_rank).action_id == "putin:agent_0:plate:dishwasher"


def test_physical_action_rank_deprioritizes_unsuitable_putin_container():
    actions = (
        ActionSpec(
            action_id="putin:agent_0:plate:box",
            action_type="putin",
            parameters={
                "object_name": "plate",
                "target_name": "box",
                "container_suitability": "container_likely_unsuitable",
                "precondition_status": "executable_now",
            },
        ),
        ActionSpec(action_id="walktowards:agent_0:plate", action_type="walktowards", parameters={"object_name": "plate", "goal_object_match": True, "precondition_status": "executable_now"}),
    )

    assert min(actions, key=physical_action_rank).action_id == "walktowards:agent_0:plate"
