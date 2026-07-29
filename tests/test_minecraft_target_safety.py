import pytest

from benchmarks.minecraft.target_safety import assess_minecraft_target_safety


@pytest.mark.parametrize(
    ("runtime_process", "bridge_cleanup", "reason"),
    [
        (
            {"process_alive_after_kill": True},
            {"cleanup_complete": True, "processes": {}},
            "runtime_process_alive_after_kill",
        ),
        (
            {"process_group_alive_after_kill": True},
            {"cleanup_complete": True, "processes": {}},
            "runtime_process_group_alive_after_kill",
        ),
        ({}, {}, "bridge_cleanup_missing"),
        ({}, {"cleanup_complete": False, "processes": {}}, "bridge_cleanup_incomplete"),
        (
            {},
            {"cleanup_complete": True, "processes": []},
            "bridge_process_metadata_invalid",
        ),
        ([], {"cleanup_complete": True, "processes": {}}, "runtime_process_metadata_invalid"),
        (
            {"process_alive_after_kill": "false"},
            {"cleanup_complete": True, "processes": {}},
            "runtime_process_metadata_invalid",
        ),
        (
            {"process_group_alive_after_kill": 0},
            {"cleanup_complete": True, "processes": {}},
            "runtime_process_metadata_invalid",
        ),
        ({}, [], "bridge_cleanup_metadata_invalid"),
        (
            {},
            {"cleanup_complete": "true", "processes": {}},
            "bridge_cleanup_metadata_invalid",
        ),
        (
            {},
            {"cleanup_complete": True, "processes": {"Alice": "malformed"}},
            "bridge_process_metadata_invalid:Alice",
        ),
        (
            {},
            {"cleanup_complete": True, "processes": {"Alice": {"alive_after_kill": 0}}},
            "bridge_process_metadata_invalid:Alice",
        ),
        (
            {},
            {"cleanup_complete": True, "processes": {1: {"alive_after_kill": False}}},
            "bridge_process_metadata_invalid",
        ),
        (
            {},
            {
                "cleanup_complete": True,
                "processes": {"Alice": {"alive_after_kill": True}},
            },
            "bridge_process_alive_after_kill:Alice",
        ),
    ],
)
def test_target_safety_rejects_unverified_cleanup(runtime_process, bridge_cleanup, reason):
    assessment = assess_minecraft_target_safety(
        runtime_started=True,
        runtime_process=runtime_process,
        bridge_cleanup=bridge_cleanup,
    )

    assert assessment.safe is False
    assert reason in assessment.reasons


def test_target_safety_accepts_completed_cleanup():
    assessment = assess_minecraft_target_safety(
        runtime_started=True,
        runtime_process={},
        bridge_cleanup={
            "cleanup_complete": True,
            "processes": {"Alice": {"alive_after_kill": False}},
        },
    )

    assert assessment.safe is True
    assert assessment.reasons == ()


def test_target_safety_does_not_quarantine_before_runtime_starts():
    assessment = assess_minecraft_target_safety(
        runtime_started=False,
        runtime_process={"process_alive_after_kill": True},
        bridge_cleanup={},
    )

    assert assessment.safe is True
    assert assessment.reasons == ()
