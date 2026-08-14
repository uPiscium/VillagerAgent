"""Deterministic, non-executing primary matrix for the EAC-511 benchmark."""
from __future__ import annotations

from .model import Condition, MatrixCell, Scenario, SEEDS, Tier
from .identity import semantic_digest


def paired_cell_equal(left: MatrixCell, right: MatrixCell) -> bool:
    """Return whether two cells are the same paired unit apart from condition."""
    return (left.scenario_id, left.scenario_digest, left.family, left.seed,
            left.pre_gate_input_digest) == (right.scenario_id, right.scenario_digest,
                                            right.family, right.seed,
                                            right.pre_gate_input_digest)


def expand_matrix(scenarios: tuple[Scenario, ...] | list[Scenario]) -> tuple[MatrixCell, ...]:
    """Expand the fourteen ordered Tier.TASK fixtures into 210 planned cells.

    Injection phase is a property of a fixture, not a run dimension.  This
    function only constructs immutable run identities; it never executes a
    fixture or applies an operator.
    """
    fixtures = tuple(item for item in scenarios if item.tier is Tier.TASK)
    if len(fixtures) != 14:
        raise ValueError("primary matrix requires exactly 14 Tier.TASK scenarios")
    if len({item.scenario_id for item in fixtures}) != 14:
        raise ValueError("primary scenarios must have unique identities")
    if len({item.digest for item in fixtures}) != 14:
        raise ValueError("primary scenarios must have unique digests")

    cells = tuple(
        MatrixCell(
            run_id=f"eac511:{scenario.scenario_id}:{seed}:{condition.value}",
            scenario_id=scenario.scenario_id,
            scenario_digest=scenario.digest,
            family=scenario.family,
            seed=seed,
            condition=condition,
            pre_gate_input_digest=semantic_digest({
                "pre_gate_contract": scenario.document["pre_gate_contract"],
                "scenario_digest": scenario.digest,
                "seed": seed,
            }),
            enforcement={
                "condition": condition.value,
                "execution_authorized": False,
                "materialized_inputs_verified": False,
                "planned_contract_only": True,
            },
        )
        for scenario in fixtures
        for seed in SEEDS
        for condition in Condition
    )
    if len(cells) != 210 or len({cell.run_id for cell in cells}) != 210:
        raise ValueError("matrix is not exactly 210 unique cells")
    return cells
