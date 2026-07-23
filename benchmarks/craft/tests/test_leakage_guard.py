import json

import pytest

from benchmarks.craft.leakage_guard import LeakageGuard, PartialInformationLeakageError
from benchmarks.craft.craft_env_adapter import _target_structure_for_guard


class _GameState:
    def __init__(self, current_structure):
        self.current_structure = current_structure


def test_oracle_moves_not_in_prompt():
    guard = LeakageGuard({})
    prompt = [{"role": "user", "content": "Use only my private view."}]
    report = guard.inspect_prompt(
        director_id="D1",
        prompt_messages=prompt,
        forbidden_payloads={"oracle_moves": [{"action": "place", "position": "(0,0)"}]},
    )
    assert report["passed"] is True


def test_target_structure_not_in_prompt():
    guard = LeakageGuard({})
    prompt = [{"role": "user", "content": "target hidden payload"}]
    with pytest.raises(PartialInformationLeakageError):
        guard.inspect_prompt(
            director_id="D1",
            prompt_messages=prompt,
            forbidden_payloads={"target_structure": "target hidden payload"},
        )


def test_forbidden_payload_is_allowed_after_explicit_public_disclosure():
    guard = LeakageGuard({})
    private_view = {"row_0": [{"color": "yellow", "size": 1}]}
    public_message = json.dumps(private_view, sort_keys=True)

    report = guard.inspect_prompt(
        director_id="D2",
        prompt_messages=[{
            "role": "user",
            "content": f"Public conversation:\nD1: {public_message}",
        }],
        forbidden_payloads={"other_private_view:D1": private_view},
        allowed_payloads=[public_message],
    )

    assert report["passed"] is True


def test_forbidden_payload_is_rejected_before_public_disclosure():
    guard = LeakageGuard({})
    private_view = {"row_0": [{"color": "yellow", "size": 1}]}

    with pytest.raises(PartialInformationLeakageError, match="other_private_view:D1"):
        guard.inspect_prompt(
            director_id="D2",
            prompt_messages=[{
                "role": "user",
                "content": json.dumps(private_view, sort_keys=True),
            }],
            forbidden_payloads={"other_private_view:D1": private_view},
            allowed_payloads=[],
        )


def test_saved_builder_prompt_artifact_is_checked_without_leakage(tmp_path):
    artifact_path = tmp_path / "Builder_turn_001.json"
    artifact_path.write_text(
        json.dumps({
            "director_id": "Builder",
            "turn_index": 1,
            "prompt_messages": [
                {"role": "system", "content": "You are the CRAFT Builder."},
                {"role": "user", "content": "Use only public Director claims."},
            ],
        }),
        encoding="utf-8",
    )
    guard = LeakageGuard({})

    report = guard.inspect_prompt_artifact(
        artifact_path=artifact_path,
        forbidden_payloads={
            "target_structure": "hidden target payload",
            "oracle_moves": [{"action": "place", "position": "(0,0)"}],
            "D1_raw_private_view": "hidden raw private view",
        },
    )

    assert report["director_id"] == "Builder"
    assert report["artifact_path"] == str(artifact_path)
    assert report["passed"] is True
    assert guard.reports == [report]


def test_saved_builder_prompt_artifact_reports_hidden_payload(tmp_path):
    artifact_path = tmp_path / "Builder_turn_001.json"
    artifact_path.write_text(
        json.dumps({
            "director_id": "Builder",
            "turn_index": 1,
            "prompt_messages": [
                {"role": "user", "content": "Use hidden raw private view."},
            ],
        }),
        encoding="utf-8",
    )
    guard = LeakageGuard({})

    with pytest.raises(PartialInformationLeakageError):
        guard.inspect_prompt_artifact(
            artifact_path=artifact_path,
            forbidden_payloads={"D1_raw_private_view": "hidden raw private view"},
        )

    assert guard.reports[0]["director_id"] == "Builder"
    assert guard.reports[0]["passed"] is False
    assert guard.reports[0]["violations"][0]["label"] == "D1_raw_private_view"


def test_saved_builder_prompt_artifact_reports_hidden_key(tmp_path):
    artifact_path = tmp_path / "Builder_turn_001.json"
    artifact_path.write_text(
        json.dumps({
            "director_id": "Builder",
            "turn_index": 1,
            "prompt_messages": [
                {"role": "user", "content": "Do not expose target_structure."},
            ],
        }),
        encoding="utf-8",
    )
    guard = LeakageGuard({})

    with pytest.raises(PartialInformationLeakageError):
        guard.inspect_prompt_artifact(
            artifact_path=artifact_path,
            forbidden_payloads={"hidden_key:target_structure": "target_structure"},
        )

    assert guard.reports[0]["violations"][0]["label"] == "hidden_key:target_structure"


def test_prompt_source_visibility_allows_public_and_own_private_sources():
    guard = LeakageGuard({})

    report = guard.inspect_prompt(
        director_id="D1",
        prompt_messages=[{"role": "user", "content": "Use allowed evidence."}],
        forbidden_payloads={},
        included_source_ids=["public:builder_action:1:0", "observed:D1:1:row_0:0"],
        source_visibility={
            "public:builder_action:1:0": {"public": True},
            "observed:D1:1:row_0:0": {"visible_to": ["D1"]},
        },
    )

    assert report["passed"] is True
    assert report["included_source_ids"] == ["public:builder_action:1:0", "observed:D1:1:row_0:0"]


def test_prompt_source_visibility_rejects_other_agent_private_source():
    guard = LeakageGuard({})

    with pytest.raises(PartialInformationLeakageError):
        guard.inspect_prompt(
            director_id="D1",
            prompt_messages=[{"role": "user", "content": "This text is harmless."}],
            forbidden_payloads={},
            included_source_ids=["observed:D2:1:row_2:2"],
            source_visibility={"observed:D2:1:row_2:2": {"visible_to": ["D2"]}},
        )

    violation = guard.reports[0]["violations"][0]
    assert violation == {
        "label": "source_visibility",
        "source_id": "observed:D2:1:row_2:2",
        "reason": "not_visible_to_agent",
    }


def test_prompt_source_visibility_rejects_evaluator_only_source():
    guard = LeakageGuard({})

    with pytest.raises(PartialInformationLeakageError):
        guard.inspect_prompt(
            director_id="D1",
            prompt_messages=[{"role": "user", "content": "This text is harmless."}],
            forbidden_payloads={},
            included_source_ids=["target_structure:0"],
            source_visibility={"target_structure:0": {"evaluator_only": True}},
        )

    assert guard.reports[0]["violations"][0]["reason"] == "evaluator_only"


def test_prompt_artifact_uses_embedded_source_visibility(tmp_path):
    artifact_path = tmp_path / "D1_turn_001.json"
    artifact_path.write_text(
        json.dumps({
            "director_id": "D1",
            "prompt_messages": [{"role": "user", "content": "Use public evidence."}],
            "included_source_ids": ["claim:D1:1"],
            "source_visibility": {"claim:D1:1": {"public": True}},
        }),
        encoding="utf-8",
    )
    guard = LeakageGuard({})

    report = guard.inspect_prompt_artifact(
        artifact_path=artifact_path,
        forbidden_payloads={},
    )

    assert report["passed"] is True
    assert report["included_source_ids"] == ["claim:D1:1"]


def test_target_structure_guard_allows_publicly_completed_structure():
    target = {"(0,0)": ["gs"], "(0,1)": []}
    sample = {"structure": target}

    assert _target_structure_for_guard(sample=sample, game_state=_GameState({"(0,0)": [], "(0,1)": []})) == target
    assert _target_structure_for_guard(sample=sample, game_state=_GameState({"(0,0)": ["gs"], "(0,1)": []})) is None
