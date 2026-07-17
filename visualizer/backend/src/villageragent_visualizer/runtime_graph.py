from __future__ import annotations

import hashlib
from pathlib import Path

from villageragent_visualizer.artifacts import ArtifactRepository
from villageragent_visualizer.dto import (
    ArtifactErrorCode,
    JSONValue,
    RunWarning,
    RuntimeGraph,
    RuntimeGraphEdge,
    RuntimeGraphErrorCode,
    RuntimeGraphLoadError,
    RuntimeGraphLoadResult,
    RuntimeGraphNode,
)
from villageragent_visualizer.runs import RunRepository
from villageragent_visualizer.versioning import validate_schema_version


RUNTIME_GRAPH_SCHEMA_VERSION = "1.0.0"


class RuntimeGraphService:
    def __init__(self, *, artifacts: ArtifactRepository, runs: RunRepository) -> None:
        self.artifacts = artifacts
        self.runs = runs

    def load(self, run_id: str) -> RuntimeGraphLoadResult:
        if self.runs.get_run(run_id) is None:
            return _error(RuntimeGraphErrorCode.RUN_NOT_FOUND, "Run not found.")

        warnings: list[RunWarning] = []
        snapshot = self._load_normalized_snapshot(run_id, warnings)
        snapshot_source = ""
        if snapshot is not None:
            snapshot_source = _text(snapshot.get("snapshot_source"))
        else:
            snapshot = self._load_checkpoint_snapshot(run_id, warnings)
            if snapshot is not None:
                snapshot_source = _text(snapshot.get("snapshot_source")) or "runtime_checkpoint"
                warnings.append(RunWarning(
                    code="runtime_checkpoint_fallback",
                    message="Canonical runtime graph is served from the live checkpoint fallback.",
                    artifact="runtime_checkpoint",
                ))

        if snapshot is None:
            invalid = any(warning.code != ArtifactErrorCode.MISSING.value for warning in warnings)
            return _error(
                RuntimeGraphErrorCode.SNAPSHOT_INVALID if invalid else RuntimeGraphErrorCode.SNAPSHOT_MISSING,
                "Canonical runtime task DAG snapshot is unavailable.",
                warnings,
            )

        graph = _adapt_snapshot(snapshot, snapshot_source=snapshot_source, warnings=warnings)
        if graph is None:
            return _error(
                RuntimeGraphErrorCode.SNAPSHOT_INVALID,
                "Canonical runtime task DAG snapshot has an invalid shape.",
                warnings,
            )
        return RuntimeGraphLoadResult(graph=graph)

    def _load_normalized_snapshot(
        self,
        run_id: str,
        warnings: list[RunWarning],
    ) -> dict[str, JSONValue] | None:
        result = self.artifacts.load_json(
            Path(run_id) / "runtime_dual_dag_snapshot.json",
            supported_schema_version=RUNTIME_GRAPH_SCHEMA_VERSION,
        )
        return _artifact_mapping(result, artifact="runtime_graph", warnings=warnings)

    def _load_checkpoint_snapshot(
        self,
        run_id: str,
        warnings: list[RunWarning],
    ) -> dict[str, JSONValue] | None:
        result = self.artifacts.load_json(Path(run_id) / ".runtime" / "runtime_result.json")
        checkpoint = _artifact_mapping(result, artifact="runtime_checkpoint", warnings=warnings)
        if checkpoint is None:
            return None
        snapshot = checkpoint.get("runtime_task_dag_snapshot")
        if not isinstance(snapshot, dict) or not snapshot:
            warnings.append(RunWarning(
                code="runtime_snapshot_missing",
                message="Runtime checkpoint does not contain a canonical task DAG snapshot.",
                artifact="runtime_checkpoint",
            ))
            return None
        validation = validate_schema_version(
            snapshot.get("schema_version"),
            supported_version=RUNTIME_GRAPH_SCHEMA_VERSION,
        )
        if validation.error is not None:
            warnings.append(RunWarning(
                code=validation.error.code.value,
                message=validation.error.message,
                artifact="runtime_checkpoint",
            ))
            return None
        warnings.extend(
            RunWarning(
                code=warning.code.value,
                message=warning.message,
                artifact="runtime_checkpoint",
            )
            for warning in validation.warnings
        )
        return snapshot


def _artifact_mapping(result, *, artifact: str, warnings: list[RunWarning]) -> dict[str, JSONValue] | None:
    if result.error is not None:
        if result.error.code is not ArtifactErrorCode.MISSING:
            warnings.append(RunWarning(
                code=result.error.code.value,
                message=result.error.message,
                artifact=artifact,
            ))
        return None
    if result.artifact is None or not isinstance(result.artifact.data, dict):
        warnings.append(RunWarning(
            code="invalid_shape",
            message="Runtime graph artifact must contain a JSON object.",
            artifact=artifact,
        ))
        return None
    warnings.extend(
        RunWarning(
            code=warning.code.value,
            message=warning.message,
            artifact=artifact,
        )
        for warning in result.artifact.warnings
    )
    return result.artifact.data


def _adapt_snapshot(
    snapshot: dict[str, JSONValue],
    *,
    snapshot_source: str,
    warnings: list[RunWarning],
) -> RuntimeGraph | None:
    raw_nodes = snapshot.get("nodes")
    raw_edges = snapshot.get("edges")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        warnings.append(RunWarning(
            code="invalid_graph_shape",
            message="Runtime graph nodes and edges must be arrays.",
            artifact="runtime_graph",
        ))
        return None

    nodes: list[RuntimeGraphNode] = []
    for index, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, dict):
            warnings.append(RunWarning(
                code="invalid_node",
                message=f"Runtime graph node at index {index} is not an object and was skipped.",
                artifact="runtime_graph",
            ))
            continue
        lifecycle = _mapping(raw_node.get("lifecycle"))
        if "available" in lifecycle:
            lifecycle.pop("available")
            warnings.append(RunWarning(
                code="deprecated_available_ignored",
                message="Deprecated available field is not treated as canonical lifecycle state.",
                artifact="runtime_graph",
            ))
        nodes.append(RuntimeGraphNode(
            node_id=_text(raw_node.get("node_id")) or f"runtime:unknown-node:{index}",
            node_type=_text(raw_node.get("node_type")) or "unknown",
            content=_mapping(raw_node.get("content")),
            lifecycle=lifecycle,
            derived=_mapping(raw_node.get("derived")),
            provenance=_mapping(raw_node.get("provenance")),
            extra={
                key: value
                for key, value in raw_node.items()
                if key not in {"node_id", "node_type", "content", "lifecycle", "derived", "provenance"}
            },
        ))

    edges: list[RuntimeGraphEdge] = []
    edge_ids: dict[str, int] = {}
    for index, raw_edge in enumerate(raw_edges):
        if not isinstance(raw_edge, dict):
            warnings.append(RunWarning(
                code="invalid_edge",
                message=f"Runtime graph edge at index {index} is not an object and was skipped.",
                artifact="runtime_graph",
            ))
            continue
        source_id = _text(raw_edge.get("source_id"))
        target_id = _text(raw_edge.get("target_id"))
        edge_type = _text(raw_edge.get("edge_type")) or "unknown"
        edge_id = _stable_edge_id(source_id, target_id, edge_type, edge_ids)
        edges.append(RuntimeGraphEdge(
            edge_id=edge_id,
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

    raw_history = snapshot.get("mutation_history")
    mutation_history = tuple(
        _mapping(item)
        for item in raw_history
        if isinstance(item, dict)
    ) if isinstance(raw_history, list) else ()
    return RuntimeGraph(
        authority="canonical_runtime_state",
        schema_version=_optional_text(snapshot.get("schema_version")),
        snapshot_source=snapshot_source,
        source_of_truth=_text(snapshot.get("source_of_truth")),
        summary=_mapping(snapshot.get("summary")),
        nodes=tuple(nodes),
        edges=tuple(edges),
        mutation_history=mutation_history,
        warnings=tuple(warnings),
    )


def _stable_edge_id(
    source_id: str,
    target_id: str,
    edge_type: str,
    counts: dict[str, int],
) -> str:
    digest = hashlib.sha256(f"{source_id}\0{target_id}\0{edge_type}".encode("utf-8")).hexdigest()[:16]
    base = f"runtime:edge:{digest}"
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
    code: RuntimeGraphErrorCode,
    message: str,
    warnings: list[RunWarning] | None = None,
) -> RuntimeGraphLoadResult:
    return RuntimeGraphLoadResult(error=RuntimeGraphLoadError(
        code=code,
        message=message,
        warnings=tuple(warnings or ()),
    ))
