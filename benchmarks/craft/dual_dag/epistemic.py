from dataclasses import asdict, dataclass


@dataclass
class Provenance:
    source: str
    director_id: str | None
    turn_index: int
    visibility: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EpistemicNode:
    node_id: str
    node_type: str
    content: dict
    confidence: float
    provenance: Provenance

    def to_dict(self) -> dict:
        data = asdict(self)
        data["provenance"] = self.provenance.to_dict()
        return data


@dataclass
class EpistemicEdge:
    source_id: str
    target_id: str
    edge_type: str
    metadata: dict

    def to_dict(self) -> dict:
        return asdict(self)


def nodes_to_dicts(nodes: list[EpistemicNode]) -> list[dict]:
    return [node.to_dict() for node in nodes]
