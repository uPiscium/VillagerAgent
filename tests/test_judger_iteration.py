from env.judger_iteration import (
    build_iteration_metadata,
    normalize_iteration_metadata,
    validate_iteration_availability,
)


def test_partial_iteration_availability_is_explicit():
    record = build_iteration_metadata(
        source="Alice_history.json outer episode count",
        limit=1,
        used=None,
        terminal_observations=0,
        usage_unavailable_reason={
            "code": "history_not_observable_at_terminal_evaluation",
            "message": "Alice_history.json was not observable at terminal evaluation",
        },
    )

    assert record["available"] is True
    assert record["source_available"] is True
    assert record["limit_available"] is True
    assert record["usage_available"] is False
    assert record["used"] is None
    assert record["usage_unavailable_reason"]["code"] == (
        "history_not_observable_at_terminal_evaluation"
    )
    assert record["terminal_observations_available"] is True
    assert validate_iteration_availability(record) == []


def test_fully_available_iteration_has_no_unavailable_reason():
    record = build_iteration_metadata(
        source="Alice_history.json outer episode count",
        limit=1,
        used=1,
        terminal_observations=3,
    )

    assert record["usage_available"] is True
    assert record["used"] == 1
    assert record["usage_unavailable_reason"] is None


def test_completely_unavailable_iteration_is_consistent():
    record = build_iteration_metadata(
        source=None,
        limit=None,
        used=None,
        terminal_observations=None,
    )

    assert record["available"] is False
    assert record["source_available"] is False
    assert record["limit_available"] is False
    assert record["usage_available"] is False
    assert record["terminal_observations_available"] is False
    assert record["usage_unavailable_reason"]["code"] == "iteration_not_measured"


def test_legacy_payload_normalization_marks_missing_usage():
    record = normalize_iteration_metadata({
        "available": True,
        "source": "history",
        "limit": 1,
        "used": None,
    })

    assert record["available"] is True
    assert record["usage_available"] is False
    assert record["used"] is None
    assert record["usage_unavailable_reason"]["code"] == (
        "legacy_payload_omitted_usage_availability"
    )


def test_validation_rejects_inconsistent_usage_availability():
    errors = validate_iteration_availability({
        "usage_available": True,
        "used": None,
        "limit_available": False,
        "source_available": False,
        "terminal_observations_available": False,
    })

    assert errors == ["usage_available=true requires used"]
