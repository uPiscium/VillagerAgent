from __future__ import annotations

import hashlib
from pathlib import Path

from villageragent_visualizer.artifacts import ArtifactRepository
from villageragent_visualizer.dto import (
    AnalysisGraph,
    AnalysisGraphEdge,
    AnalysisGraphErrorCode,
    AnalysisGraphFilters,
    AnalysisGraphLoadError,
    AnalysisGraphLoadResult,
    AnalysisGraphNode,
    ArtifactErrorCode,
    JSONValue,
    RunWarning,
)
from villageragent_visualizer.runs import RunRepository


ANALYSIS_GRAPH_SCHEMA_VERSION = "1.0.0"


class AnalysisGraphService:
    def __init__(self, *, artifacts: ArtifactRepository, runs: RunRepository) -> None:
        self.artifacts = artifacts
        self.runs = runs

    def load(
        self,
        run_id: str,
        *,
        filters: AnalysisGraphFilters | None = None,
    ) -> AnalysisGraphLoadResult:
        if self.runs.get_run(run_id) is None:
            return _error(AnalysisGraphErrorCode.RUN_NOT_FOUND, "Run not found.")

        result = self.artifacts.load_json(
            Path(run_id) / "dual_dag_artifact.json",
            supported_schema_version=ANALYSIS_GRAPH_SCHEMA_VERSION,
        )
        if result.error is not None:
            code = (
                AnalysisGraphErrorCode.ARTIFACT_MISSING
                if result.error.code is ArtifactErrorCode.MISSING
                else AnalysisGraphErrorCode.ARTIFACT_INVALID
            )
            warning = RunWarning(
                code=result.error.code.value,
                message=result.error.message,
                artifact="analysis_graph",
            )
            return _error(code, "Post-hoc analysis artifact is unavailable.", [warning])
        if result.artifact is None or not isinstance(result.artifact.data, dict):
            return _error(
                AnalysisGraphErrorCode.ARTIFACT_INVALID,
                "Post-hoc analysis artifact must contain a JSON object.",
            )

        warnings = [
            RunWarning(
                code=warning.code.value,
                message=warning.message,
                artifact="analysis_graph",
            )
            for warning in result.artifact.warnings
        ]
        graph = _adapt_graph(result.artifact.data, filters=filters or AnalysisGraphFilters(), warnings=warnings)
        if graph is None:
            return _error(
                AnalysisGraphErrorCode.ARTIFACT_INVALID,
                "Post-hoc analysis artifact has an invalid shape.",
                warnings,
            )
        return AnalysisGraphLoadResult(graph=graph)


def _adapt_graph(
    artifact: dict[str, JSONValue],
    *,
    filters: AnalysisGraphFilters,
    warnings: list[RunWarning],
) -> AnalysisGraph | None:
    raw_nodes = artifact.get("nodes")
    raw_edges = artifact.get("edges")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        warnings.append(RunWarning(
            code="invalid_graph_shape",
            message="Analysis graph nodes and edges must be arrays.",
            artifact="analysis_graph",
        ))
        return None

    nodes: list[AnalysisGraphNode] = []
    for index, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, dict):
            warnings.append(RunWarning(
                code="invalid_node",
                message=f"Analysis graph node at index {index} is not an object and was skipped.",
                artifact="analysis_graph",
            ))
            continue
        node_id = _text(raw_node.get("node_id")) or f"minecraft:unknown:{index}"
        node_type = _text(raw_node.get("node_type")) or "unknown"
        confidence_value = raw_node.get("confidence")
        confidence = float(confidence_value) if isinstance(confidence_value, (int, float)) and not isinstance(confidence_value, bool) else None
        nodes.append(AnalysisGraphNode(
            node_id=node_id,
            node_type=node_type,
            content=_mapping(raw_node.get("content")),
            provenance=_mapping(raw_node.get("provenance")),
            confidence=confidence,
            runtime_task_id=(
                f"runtime:task:{node_id.removeprefix('minecraft:task:')}"
                if node_type == "minecraft_task" and node_id.startswith("minecraft:task:")
                else None
            ),
            extra={
                key: value
                for key, value in raw_node.items()
                if key not in {"node_id", "node_type", "content", "provenance", "confidence"}
            },
        ))

    edges: list[AnalysisGraphEdge] = []
    edge_counts: dict[str, int] = {}
    for index, raw_edge in enumerate(raw_edges):
        if not isinstance(raw_edge, dict):
            warnings.append(RunWarning(
                code="invalid_edge",
                message=f"Analysis graph edge at index {index} is not an object and was skipped.",
                artifact="analysis_graph",
            ))
            continue
        source_id = _text(raw_edge.get("source_id"))
        target_id = _text(raw_edge.get("target_id"))
        edge_type = _text(raw_edge.get("edge_type")) or "unknown"
        edges.append(AnalysisGraphEdge(
            edge_id=_stable_edge_id(source_id, target_id, edge_type, edge_counts),
            source_id=source_id,
            target_id=target_id,
            edge_type=edge_type,
            metadata=_mapping(raw_edge.get("metadata")),
            extra={
                key: value
                for key, value in raw_edge.items()
                if key not in {"source_id", "target_id", "edge_type", "metadata"}
            },
        ))

    nodes, edges = _apply_filters(nodes, edges, filters)
    task_state_source = _text(artifact.get("task_state_source"))
    if not task_state_source:
        task_state_source = "unknown"
        warnings.append(RunWarning(
            code="task_state_source_missing",
            message="Analysis artifact does not declare its task state source.",
            artifact="analysis_graph",
        ))
    return AnalysisGraph(
        authority="posthoc_analysis_projection",
        schema_version=_optional_text(artifact.get("schema_version")),
        task_state_source=task_state_source,
        summary=_mapping(artifact.get("summary")),
        schema=_mapping(artifact.get("schema")),
        mapping=_mapping(artifact.get("mapping")),
        nodes=tuple(nodes),
        edges=tuple(edges),
        applied_filters={
            "node_types": tuple(sorted(filters.node_types)),
            "edge_types": tuple(sorted(filters.edge_types)),
            "agents": tuple(sorted(filters.agents)),
            "task_ids": tuple(sorted(filters.task_ids)),
        },
        warnings=tuple(warnings),
    )


def _apply_filters(
    nodes: list[AnalysisGraphNode],
    edges: list[AnalysisGraphEdge],
    filters: AnalysisGraphFilters,
) -> tuple[list[AnalysisGraphNode], list[AnalysisGraphEdge]]:
    selected_ids = {node.node_id for node in nodes}
    node_by_id = {node.node_id: node for node in nodes}

    if filters.task_ids:
        task_scope = {
            node_id
            for node_id in filters.task_ids
            if node_id in node_by_id and node_by_id[node_id].node_type == "minecraft_task"
        }
        changed = True
        while changed:
            changed = False
            for edge in edges:
                if edge.source_id not in task_scope:
                    continue
                target = node_by_id.get(edge.target_id)
                if target is None or (edge.edge_type == "precedes_task" and target.node_type == "minecraft_task"):
                    continue
                if target.node_id not in task_scope:
                    task_scope.add(target.node_id)
                    changed = True
        selected_ids &= task_scope

    if filters.agents:
        selected_ids &= {
            node.node_id
            for node in nodes
            if _node_agents(node) & filters.agents
        }
    if filters.node_types:
        selected_ids &= {
            node.node_id
            for node in nodes
            if node.node_type in filters.node_types
        }

    filtered_nodes = [node for node in nodes if node.node_id in selected_ids]
    filtered_edges = [
        edge
        for edge in edges
        if edge.source_id in selected_ids
        and edge.target_id in selected_ids
        and (not filters.edge_types or edge.edge_type in filters.edge_types)
    ]
    return filtered_nodes, filtered_edges


def _node_agents(node: AnalysisGraphNode) -> frozenset[str]:
    values: set[str] = set()
    for value in (node.content.get("agent"), node.provenance.get("agent")):
        if isinstance(value, str):
            values.add(value)
    for key in ("candidate_agents", "assigned_agents", "last_assigned_agents"):
        value = node.content.get(key)
        if isinstance(value, list):
            values.update(item for item in value if isinstance(item, str))
    return frozenset(values)


def _stable_edge_id(
    source_id: str,
    target_id: str,
    edge_type: str,
    counts: dict[str, int],
) -> str:
    digest = hashlib.sha256(f"{source_id}\0{target_id}\0{edge_type}".encode("utf-8")).hexdigest()[:16]
    base = f"analysis:edge:{digest}"
    counts[base] = counts.get(base, 0) + 1
    return base if counts[base] == 1 else f"{base}:{counts[base]}"


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    text = _text(value)
    return text or None


def _error(
    code: AnalysisGraphErrorCode,
    message: str,
    warnings: list[RunWarning] | None = None,
) -> AnalysisGraphLoadResult:
    return AnalysisGraphLoadResult(error=AnalysisGraphLoadError(
        code=code,
        message=message,
        warnings=tuple(warnings or ()),
    ))
