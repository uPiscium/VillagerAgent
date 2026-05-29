from benchmarks.craft.craft_env_adapter import _matches_oracle_candidate, _oracle_fallback_action


def test_matches_oracle_candidate_requires_block_and_span_match():
    oracle_moves = [{
        "action": "place",
        "block": "bl",
        "position": "(1,0)",
        "layer": 0,
        "span_to": "(2,0)",
    }]

    assert _matches_oracle_candidate({
        "action": "place",
        "block": "bl",
        "position": "(1,0)",
        "layer": 0,
        "span_to": "(2,0)",
    }, oracle_moves)
    assert not _matches_oracle_candidate({
        "action": "place",
        "block": "yl",
        "position": "(1,0)",
        "layer": 0,
        "span_to": None,
    }, oracle_moves)


def test_oracle_fallback_preserves_diagnostics():
    fallback = _oracle_fallback_action(
        oracle_moves=[{"action": "place", "block": "bl", "position": "(1,0)", "layer": 0}],
        response_info={"content_empty": False},
        first_line="PLACE:yl:(0,0):0:CONFIRM:bad",
        reason="oracle_first_candidate_after_non_candidate_response",
    )

    assert fallback["block"] == "bl"
    assert fallback["_builder_response_info"] == {"content_empty": False}
    assert fallback["_builder_raw_first_line"].startswith("PLACE:yl")
    assert fallback["_builder_fallback"] == "oracle_first_candidate_after_non_candidate_response"
