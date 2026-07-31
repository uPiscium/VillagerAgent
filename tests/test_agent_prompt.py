from pipeline.agent_prompt import (
    agent_cooper_prompt,
    agent_prompt_w_emoji,
    agent_prompt_wo_emoji,
)


def test_agent_prompts_stop_tool_use_after_terminal_success():
    prompts = (agent_prompt_w_emoji, agent_prompt_wo_emoji, agent_cooper_prompt)

    for prompt in prompts:
        assert "At least two Action" not in prompt
        assert "target_reached=true" in prompt
        assert "without calling another tool" in prompt
