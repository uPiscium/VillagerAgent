import json
from pathlib import Path


DECISION_PATH = Path("configs/benchmark_selection/issue_374.json")


def test_issue_374_selects_exactly_one_benchmark():
    decision = json.loads(DECISION_PATH.read_text(encoding="utf-8"))

    selected = decision["selected_for_first_implementation"]
    assert selected == "partnr"
    assert selected not in decision["deferred"]
    assert set(decision["deferred"]) == {"habitat_mas"}
    assert decision["performance_claim"] is False


def test_partnr_gate_preserves_evaluator_isolation():
    decision = json.loads(DECISION_PATH.read_text(encoding="utf-8"))
    gate = decision["partnr_first_gate"]

    assert gate["split"] == "val_mini"
    assert gate["baseline"] == "heuristic_full_obs"
    assert 0 < gate["episode_limit"] <= 4
    assert gate["requires_isolated_environment"] is True
    assert gate["requires_headless_preflight"] is True
    assert gate["expose_evaluator_propositions_to_agent"] is False


def test_selection_records_pinned_sources_and_common_adapter_contract():
    decision = json.loads(DECISION_PATH.read_text(encoding="utf-8"))

    assert all(len(source["commit"]) == 40 for source in decision["sources"].values())
    assert set(decision["required_adapter_methods"]) == {
        "reset",
        "agent_ids",
        "capabilities",
        "get_observation",
        "get_public_observation",
        "get_legal_actions",
        "decision_context",
        "execute_action",
        "execute_information_action",
        "is_terminal",
        "task_progress",
        "final_metrics",
    }
