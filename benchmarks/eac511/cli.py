"""No-execution command line interface for validation and expansion only."""
from __future__ import annotations
import argparse
from .fixtures import tier1_fixtures
from .identity import verify_frozen_runtime_inputs
from .matrix import expand_matrix
from .artifacts import load_json_object
from .protocol import (EVENT_SCHEMA_PATH, PROTOCOL_ID, SCENARIO_SCHEMA_PATH,
                        event_schema_document, freeze_design_artifacts,
                        load_committed_protocol, load_committed_scenarios,
                        protocol_document, scenario_definitions,
                        scenario_schema_document)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eac511")
    parser.add_argument("command", choices=("validate", "expand", "tier1", "identity", "freeze"))
    args = parser.parse_args(argv)
    if args.command == "tier1":
        records = tier1_fixtures()
        if not all(record.passed for record in records):
            return 1
        print(len(records))
    elif args.command == "expand":
        print(len(expand_matrix(load_committed_scenarios())))
    elif args.command == "identity":
        print(PROTOCOL_ID)
    elif args.command == "freeze":
        print("\n".join(str(path) for path in freeze_design_artifacts()))
    else:
        verify_frozen_runtime_inputs()
        if load_committed_protocol() != protocol_document():
            raise ValueError("committed protocol differs from authoritative source")
        scenarios = load_committed_scenarios()
        if tuple(scenario.document for scenario in scenarios) != scenario_definitions():
            raise ValueError("committed scenarios differ from authoritative source")
        if load_json_object(SCENARIO_SCHEMA_PATH) != scenario_schema_document():
            raise ValueError("committed scenario schema differs from authoritative source")
        if load_json_object(EVENT_SCHEMA_PATH) != event_schema_document():
            raise ValueError("committed event schema differs from authoritative source")
        if len(expand_matrix(scenarios)) != 210:
            return 1
        print("design-valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
