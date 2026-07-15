import json
from pathlib import Path

import pytest

from benchmarks.craft.retrieval_probe import RetrievalProbeError, run_probe, run_probe_file


FIXTURE = Path(__file__).parents[1] / "fixtures" / "issue_291_retrieval_probe_input.json"


def test_checked_in_probe_retrieves_public_claim_and_changes_top_action(tmp_path):
    output = tmp_path / "probe.json"

    result = run_probe_file(FIXTURE, output)

    assert result["input_visibility"] == "public_history_only"
    assert result["retrieval"] == {
        "retrieved_node_count": 1,
        "retrieved_claim_count": 1,
        "retrieved_action_count": 0,
        "retrieval_used_in_top_action_count": 1,
        "retrieval_changed_top_action_count": 1,
    }
    assert result["top_action"] == {
        "without_retrieval": "candidate:a",
        "with_retrieval": "candidate:b",
        "influenced": True,
    }
    assert json.loads(output.read_text(encoding="utf-8")) == result


@pytest.mark.parametrize(
    "hidden_key",
    ["target_structure", "oracle_moves", "raw_private_view", "private_reasoning", "builder_prompt", "_private"],
)
def test_probe_rejects_hidden_state_keys(hidden_key):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload[hidden_key] = "not public"

    with pytest.raises(RetrievalProbeError, match="hidden-state keys"):
        run_probe(payload)
