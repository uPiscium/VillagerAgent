from model.openai_models import _contains_tag


def test_required_tag_accepts_json_underscore_variant():
    content = '{"assigned_agents": ["Alice"]}'

    assert _contains_tag(content, "assigned agents")


def test_required_tag_accepts_case_and_hyphen_variants():
    content = '{"Required-Subtasks": []}'

    assert _contains_tag(content, "required subtasks")


def test_required_tag_rejects_missing_key():
    content = '{"candidate_agents": ["Alice"]}'

    assert not _contains_tag(content, "assigned agents")
