from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from benchmarks.craft.dual_dag.runtime import DualDAGRuntime
from benchmarks.craft.hidden_state_keys import official_runner_hidden_state_key_labels


SCHEMA_VERSION = "1.0.0"


class RetrievalProbeError(ValueError):
    """Raised when a retrieval probe contains invalid or hidden input."""


def run_probe(payload: dict[str, Any], *, input_sha256: str | None = None) -> dict[str, Any]:
    _validate_payload(payload)
    runtime = DualDAGRuntime(director_ids=payload["director_ids"], config={})
    for claim in payload["public_history"].get("reported_claims", []):
        runtime.add_reported_claim(
            director_id=claim["director_id"],
            turn_index=claim["turn_index"],
            message=claim["message"],
        )
    for action in payload["public_history"].get("builder_actions", []):
        runtime.add_public_builder_action(
            turn_index=action["turn_index"],
            action=action["action"],
        )

    turn_index = payload["turn_index"]
    candidates = payload["candidates"]
    without_retrieval = runtime.current_turn_decision_support(
        turn_index=turn_index,
        candidates=candidates,
        use_historical_graph_context=False,
    )
    with_retrieval = runtime.current_turn_decision_support(
        turn_index=turn_index,
        candidates=candidates,
        use_historical_graph_context=True,
    )
    top_id = with_retrieval["recommended_candidate_id"]
    top_row = next(row for row in with_retrieval["candidates"] if row["node_id"] == top_id)
    contexts = [row.get("graph_context", {}) for row in with_retrieval["candidates"]]
    retrieved_claim_count = sum(len(context.get("relevant_public_claims", [])) for context in contexts)
    retrieved_action_count = sum(len(context.get("relevant_public_actions", [])) for context in contexts)
    top_context = top_row.get("graph_context", {})
    retrieval_used = bool(
        top_context.get("relevant_public_claims")
        or top_context.get("relevant_public_actions")
    )
    retrieval_changed = without_retrieval["recommended_candidate_id"] != top_id
    return {
        "schema_version": SCHEMA_VERSION,
        "probe_id": payload["probe_id"],
        "classification": "diagnostic",
        "input_visibility": "public_history_only",
        "input_sha256": input_sha256,
        "turn_index": turn_index,
        "retrieval": {
            "retrieved_node_count": retrieved_claim_count + retrieved_action_count,
            "retrieved_claim_count": retrieved_claim_count,
            "retrieved_action_count": retrieved_action_count,
            "retrieval_used_in_top_action_count": int(retrieval_used),
            "retrieval_changed_top_action_count": int(retrieval_changed),
        },
        "top_action": {
            "without_retrieval": without_retrieval["recommended_candidate_id"],
            "with_retrieval": top_id,
            "influenced": retrieval_changed,
        },
        "without_retrieval": without_retrieval,
        "with_retrieval": with_retrieval,
    }


def run_probe_file(input_path: Path, output_path: Path) -> dict[str, Any]:
    input_bytes = input_path.read_bytes()
    payload = json.loads(input_bytes)
    if not isinstance(payload, dict):
        raise RetrievalProbeError("Retrieval probe input must be a JSON object.")
    result = run_probe(payload, input_sha256=hashlib.sha256(input_bytes).hexdigest())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a deterministic public-history retrieval probe.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_probe_file(Path(args.input), Path(args.output))
    print(json.dumps({
        "output": args.output,
        "probe_id": result["probe_id"],
        **result["retrieval"],
        "top_action_influenced": result["top_action"]["influenced"],
    }, sort_keys=True))
    return 0


def _validate_payload(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RetrievalProbeError(f"schema_version must be {SCHEMA_VERSION!r}.")
    if not isinstance(payload.get("probe_id"), str) or not payload["probe_id"]:
        raise RetrievalProbeError("probe_id must be a non-empty string.")
    if not isinstance(payload.get("turn_index"), int) or payload["turn_index"] <= 0:
        raise RetrievalProbeError("turn_index must be a positive integer.")
    director_ids = payload.get("director_ids")
    if not isinstance(director_ids, list) or not director_ids or not all(
        isinstance(item, str) and item for item in director_ids
    ):
        raise RetrievalProbeError("director_ids must be a non-empty string array.")
    history = payload.get("public_history")
    if not isinstance(history, dict):
        raise RetrievalProbeError("public_history must be an object.")
    claims = history.get("reported_claims", [])
    actions = history.get("builder_actions", [])
    if not isinstance(claims, list) or not isinstance(actions, list):
        raise RetrievalProbeError("public history entries must be arrays.")
    for claim in claims:
        if not isinstance(claim, dict) or not {
            "director_id", "turn_index", "message"
        }.issubset(claim):
            raise RetrievalProbeError("Each reported claim requires director_id, turn_index, and message.")
    for action in actions:
        if not isinstance(action, dict) or not isinstance(action.get("action"), dict):
            raise RetrievalProbeError("Each builder action requires turn_index and an action object.")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise RetrievalProbeError("candidates must be a non-empty array.")
    for candidate in candidates:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("node_id"), str):
            raise RetrievalProbeError("Each candidate requires a node_id.")
        if not isinstance(candidate.get("action"), dict):
            raise RetrievalProbeError("Each candidate requires an action object.")
    hidden_hits = sorted(_hidden_key_hits(payload))
    if hidden_hits:
        raise RetrievalProbeError("Retrieval probe input contains hidden-state keys: " + ", ".join(hidden_hits))


def _hidden_key_hits(value: Any) -> set[str]:
    hidden = set(official_runner_hidden_state_key_labels())
    hits: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if key_text in hidden or key_text.startswith("_"):
                hits.add(key_text)
            hits.update(_hidden_key_hits(child))
    elif isinstance(value, list):
        for child in value:
            hits.update(_hidden_key_hits(child))
    return hits


if __name__ == "__main__":
    raise SystemExit(main())
