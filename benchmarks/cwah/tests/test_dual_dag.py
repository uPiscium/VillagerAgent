from benchmarks.common.actions import ActionSpec, InformationActionSpec
from benchmarks.cwah.adapter import CWAHConfig, CWAHSymbolicAdapter
from benchmarks.cwah.dual_dag import CWAHDualDAGRuntime
from benchmarks.cwah.mock_env import mock_cwah_env_factory


def test_dual_dag_keeps_private_nodes_local_and_projects_candidate_states():
    adapter = CWAHSymbolicAdapter(config=CWAHConfig(), env_factory=mock_cwah_env_factory)
    adapter.reset(episode_id="mock-cwah", seed=7)

    agent_0_context = adapter.decision_context("agent_0")
    adapter.decision_context("agent_1")
    agent_0_context_after_agent_1 = adapter.decision_context("agent_0")

    assert "plate" in repr(agent_0_context.visible_epistemic_nodes)
    assert "cupcake" not in repr(agent_0_context_after_agent_1.visible_epistemic_nodes)
    grab = next(
        candidate
        for candidate in agent_0_context.visible_candidates
        if candidate["candidate_id"] == "grab:agent_0:20"
    )
    message = next(
        candidate
        for candidate in agent_0_context.visible_candidates
        if candidate["candidate_id"] == "send_message:agent_0"
    )
    assert grab["state"] == "setup_required"
    assert grab["setup_action_id"] == "walktowards:agent_0:20"
    assert message["state"] == "information_action"
    assert {
        "source_id": "walktowards:agent_0:20",
        "target_id": "grab:agent_0:20",
        "edge_type": "enables",
        "metadata": {"reason": "needs_walktowards_object"},
    } in adapter.dual_dag_snapshot()["action_candidate_dag"]["edges"]


def test_dual_dag_projects_public_messages_for_other_agents():
    adapter = CWAHSymbolicAdapter(config=CWAHConfig(), env_factory=mock_cwah_env_factory)
    adapter.reset(episode_id="mock-cwah", seed=7)

    adapter.execute_information_action(
        "agent_0",
        InformationActionSpec(
            action_id="send_message:agent_0",
            action_type="send_message",
            parameters={"message": "The plate is in the kitchen."},
            information_subtype="send_message",
        ),
    )
    agent_1_context = adapter.decision_context("agent_1")

    public_messages = [
        node
        for node in agent_1_context.visible_epistemic_nodes
        if node["source_kind"] in {"agent_message", "public_message_sent"}
    ]
    assert public_messages
    assert all(node["visibility"]["public"] is True for node in public_messages)
    assert "plate" in repr(public_messages)


def test_dual_dag_sanitizes_forbidden_observation_fields():
    def env_factory(config):
        env = mock_cwah_env_factory(config)
        env._observations[0]["nodes"][0]["hidden_state"] = {"secret": True}
        env._observations[0]["nodes"][0]["simulator_debug"] = "internal"
        return env

    adapter = CWAHSymbolicAdapter(config=CWAHConfig(), env_factory=env_factory)
    adapter.reset(episode_id="mock-cwah", seed=7)

    context = adapter.decision_context("agent_0")
    serialized = repr(context.visible_epistemic_nodes)

    context.validate_agent_facing()
    assert "hidden_state" not in serialized
    assert "simulator_debug" not in serialized
    assert "secret" not in serialized


def test_dual_dag_records_failed_action_without_raw_step_info():
    def env_factory(config):
        env = mock_cwah_env_factory(config)

        def failed_step(_action_dict):
            env.steps += 1
            return env.get_observations(), 0.0, False, {
                "failed_exec": True,
                "debug": {"private": "do not expose"},
                "details": {"0": {"message": "Object is not close enough"}},
            }, [None, None]

        env.step = failed_step
        return env

    adapter = CWAHSymbolicAdapter(config=CWAHConfig(), env_factory=env_factory)
    adapter.reset(episode_id="mock-cwah", seed=7)
    context = adapter.decision_context("agent_0")
    action = next(action for action in context.legal_actions if action.action_id == "grab:agent_0:20")

    result = adapter.execute_action("agent_0", action)
    candidate = next(
        node
        for node in adapter.dual_dag_snapshot()["action_candidate_dag"]["nodes"]
        if node["candidate_id"] == action.action_id
    )

    assert result.succeeded is False
    assert candidate["state"] == "setup_required"
    assert candidate["last_outcome"] == {
        "step": 1,
        "succeeded": False,
        "error": "Object is not close enough",
    }
    assert "debug" not in repr(candidate)
    assert "private" not in repr(candidate)


def test_dual_dag_replaces_stale_setup_edges_when_preconditions_change():
    runtime = CWAHDualDAGRuntime(episode_id="mock-cwah")
    runtime.update_action_candidates(
        agent_id="agent_0",
        actions=(ActionSpec(
            action_id="grab:agent_0:20",
            action_type="grab",
            parameters={
                "precondition_status": "setup_required",
                "precondition_reason": "needs_walktowards_object",
                "setup_action_id": "walktowards:agent_0:20",
            },
        ),),
    )

    runtime.update_action_candidates(
        agent_id="agent_0",
        actions=(ActionSpec(
            action_id="grab:agent_0:20",
            action_type="grab",
            parameters={
                "precondition_status": "executable_now",
                "precondition_reason": "actor_close_to_object",
            },
        ),),
    )

    snapshot = runtime.snapshot()
    candidate = snapshot["action_candidate_dag"]["nodes"][0]
    assert candidate["state"] == "executable_now"
    assert snapshot["action_candidate_dag"]["edges"] == []
