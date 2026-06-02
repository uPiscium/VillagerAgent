from dataclasses import asdict, dataclass


BLOCK_COLORS = {
    "y": "yellow",
    "b": "blue",
    "o": "orange",
    "g": "green",
    "r": "red",
}
COLOR_WORDS = set(BLOCK_COLORS.values())


@dataclass
class ActionCandidateNode:
    node_id: str
    action_type: str
    action: dict
    state: str
    confidence: float
    supported_by: list[str]
    conflicts_with: list[str]
    required_evidence: list[str]
    metadata: dict

    def to_dict(self) -> dict:
        return asdict(self)


def action_candidates_from_moves(
    *,
    moves: list[dict] | None,
    reported_claims: dict[str, dict],
    turn_index: int,
) -> list[ActionCandidateNode]:
    return [
        _candidate_from_action(
            action=move,
            index=index,
            turn_index=turn_index,
            reported_claims=reported_claims,
            physically_verified=True,
        )
        for index, move in enumerate(moves or [])
    ]


def action_candidate_from_parsed_action(
    *,
    action: dict,
    reported_claims: dict[str, dict],
    turn_index: int,
) -> ActionCandidateNode:
    return _candidate_from_action(
        action=action,
        index=0,
        turn_index=turn_index,
        reported_claims=reported_claims,
        physically_verified=False,
    )


def build_action_candidate_metadata(
    *,
    candidates: list[ActionCandidateNode],
    chosen_action: dict,
    chosen_by: str,
) -> dict:
    chosen = _find_matching_candidate(candidates, chosen_action)
    if chosen is None and candidates:
        chosen = candidates[0] if chosen_by == "oracle_fallback" else None
    if chosen is None:
        chosen = action_candidate_from_parsed_action(
            action=chosen_action,
            reported_claims={},
            turn_index=0,
        )
    return {
        "candidate_count": len(candidates),
        "chosen_candidate_id": chosen.node_id,
        "chosen_by": chosen_by,
        "chosen_confidence": chosen.confidence,
        "claim_support_count": len(chosen.supported_by),
        "claim_conflict_count": len(chosen.conflicts_with),
        "candidates": [candidate.to_dict() for candidate in candidates],
    }


def _candidate_from_action(
    *,
    action: dict,
    index: int,
    turn_index: int,
    reported_claims: dict[str, dict],
    physically_verified: bool,
) -> ActionCandidateNode:
    support, conflict = _claim_support_conflict(action, reported_claims)
    confidence = _bounded_confidence(0.5 + 0.15 * len(support) - 0.2 * len(conflict))
    if physically_verified:
        confidence = min(1.0, confidence + 0.1)
    return ActionCandidateNode(
        node_id=f"action:{turn_index}:{index}",
        action_type=_action_type(action),
        action=_public_action(action),
        state="candidate",
        confidence=confidence,
        supported_by=support,
        conflicts_with=conflict,
        required_evidence=[],
        metadata={"physically_verified": physically_verified},
    )


def _claim_support_conflict(action: dict, reported_claims: dict[str, dict]) -> tuple[list[str], list[str]]:
    supported_by = []
    conflicts_with = []
    action_color = _action_color(action)
    block = action.get("block")
    for claim in reported_claims.values():
        content = claim.get("content", {}) if isinstance(claim, dict) else {}
        keywords = set(content.get("keywords", []))
        claim_id = claim.get("node_id", "")
        if not claim_id:
            continue
        if block in keywords or (action_color and action_color in keywords):
            supported_by.append(claim_id)
            continue
        claim_colors = keywords & COLOR_WORDS
        if action_color and len(claim_colors) == 1 and action_color not in claim_colors:
            conflicts_with.append(claim_id)
    return supported_by, conflicts_with


def _action_color(action: dict) -> str | None:
    block = action.get("block")
    if not isinstance(block, str) or not block:
        return None
    return BLOCK_COLORS.get(block[0])


def _action_type(action: dict) -> str:
    if action.get("action") == "place":
        return "place_block"
    if action.get("action") == "remove":
        return "remove_block"
    if action.get("action") == "clarify":
        return "clarify"
    return "wait_for_evidence"


def _public_action(action: dict) -> dict:
    return {key: value for key, value in action.items() if not key.startswith("_")}


def _find_matching_candidate(candidates: list[ActionCandidateNode], action: dict) -> ActionCandidateNode | None:
    return next(
        (candidate for candidate in candidates if _matches_action(candidate.action, action)),
        None,
    )


def _matches_action(candidate: dict, action: dict) -> bool:
    return (
        candidate.get("action") == action.get("action")
        and candidate.get("position") == action.get("position")
        and candidate.get("layer") == action.get("layer")
        and candidate.get("block") == action.get("block")
        and candidate.get("span_to") == action.get("span_to")
    )


def _bounded_confidence(value: float) -> float:
    return max(0.0, min(1.0, value))
