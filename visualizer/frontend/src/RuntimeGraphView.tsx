import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeProps,
  type ReactFlowInstance,
} from "@xyflow/react";

import {
  fetchRuntimeGraph,
  type RuntimeGraph,
  type RuntimeGraphNode,
} from "./api";
import { EntityInspector, type InspectorEntity } from "./EntityInspector";

export type LayoutDirection = "RIGHT" | "DOWN";
export type RuntimeNodeData = Record<string, unknown> & {
  source: RuntimeGraphNode;
  description: string;
  status: string;
  agents: string[];
  requiredCount: number | null;
  dependencyReady: boolean | null;
  blockers: string[];
  milestones: string[];
};

const nodeTypes = { runtimeTask: RuntimeTaskNode };
const elk = import("elkjs/lib/elk.bundled.js").then(
  ({ default: ELK }) => new ELK(),
);

export function RuntimeGraphView({ runId }: { runId: string }) {
  const graph = useQuery({
    queryKey: ["runtime-graph", runId],
    queryFn: () => fetchRuntimeGraph(runId),
  });
  const [direction, setDirection] = useState<LayoutDirection>("RIGHT");
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<RuntimeNodeData>>(
    [],
  );
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedNodeId = searchParams.get("entity");
  const [flow, setFlow] = useState<ReactFlowInstance<
    Node<RuntimeNodeData>,
    Edge
  > | null>(null);

  function selectEntity(id: string | null) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (id) next.set("entity", id);
      else next.delete("entity");
      return next;
    });
  }

  useEffect(() => {
    if (!graph.data) return;
    let active = true;
    void layoutRuntimeGraph(graph.data, direction).then((layout) => {
      if (!active) return;
      setNodes(
        layout.nodes.map((node) => ({
          ...node,
          selected: node.id === selectedNodeId,
        })),
      );
      setEdges(layout.edges);
    });
    return () => {
      active = false;
    };
  }, [direction, graph.data, setEdges, setNodes]);

  if (graph.isPending)
    return (
      <GraphMessage
        title="Loading Runtime DAG"
        detail="Computing the canonical task layout."
      />
    );
  if (graph.isError)
    return (
      <GraphMessage
        title="Runtime DAG unavailable"
        detail={graph.error.message}
      />
    );
  if (graph.data.nodes.length === 0)
    return (
      <GraphMessage
        title="Empty Runtime DAG"
        detail="The canonical snapshot contains no task nodes."
      />
    );

  return (
    <section
      className="runtime-graph-view"
      aria-label="Canonical Runtime Task DAG"
    >
      <header className="authority-banner">
        <div>
          <strong>Canonical Runtime State</strong>
          <span>Read-only source of truth</span>
        </div>
        <dl>
          <div>
            <dt>Source</dt>
            <dd>{graph.data.source_of_truth || "unknown"}</dd>
          </div>
          <div>
            <dt>Snapshot</dt>
            <dd>{graph.data.snapshot_source || "unknown"}</dd>
          </div>
        </dl>
      </header>
      <div className="graph-toolbar" aria-label="Runtime graph layout controls">
        <button
          aria-pressed={direction === "RIGHT"}
          onClick={() => setDirection("RIGHT")}
        >
          Left to right
        </button>
        <button
          aria-pressed={direction === "DOWN"}
          onClick={() => setDirection("DOWN")}
        >
          Top to bottom
        </button>
        <button
          onClick={() => {
            selectEntity(null);
            setNodes((current) =>
              current.map((node) => ({ ...node, selected: false })),
            );
            void flow?.fitView({ duration: 250 });
          }}
        >
          Reset view
        </button>
      </div>
      <div className="graph-canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onInit={setFlow}
          onNodeClick={(_, node) => {
            selectEntity(node.id);
            setNodes((current) =>
              current.map((item) => ({
                ...item,
                selected: item.id === node.id,
              })),
            );
          }}
          onEdgeClick={(_, edge) => selectEntity(edge.id)}
          fitView
          nodesConnectable={false}
          elementsSelectable
          aria-label="Runtime task dependency graph"
        >
          <Background color="#36504a" gap={24} />
          <MiniMap pannable zoomable nodeColor="#91b66d" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <div className="graph-legend" aria-label="Runtime status legend">
        {["running", "success", "failure", "blocked", "unknown"].map(
          (status) => (
            <span key={status}>
              <i
                className={`status-icon status-icon--${status}`}
                aria-hidden="true"
              />
              {status}
            </span>
          ),
        )}
      </div>
      {selectedNodeId && (
        <p className="selection-note" aria-live="polite">
          Selected task: <code>{selectedNodeId}</code>
        </p>
      )}
      <EntityInspector
        runId={runId}
        selectionId={selectedNodeId}
        entity={runtimeInspectorEntity(graph.data, selectedNodeId)}
        onClose={() => selectEntity(null)}
      />
    </section>
  );
}

export function runtimeInspectorEntity(
  graph: RuntimeGraph,
  id: string | null,
): InspectorEntity | null {
  if (!id) return null;
  const node = graph.nodes.find((item) => item.node_id === id);
  if (node) {
    const status =
      typeof node.lifecycle.status === "string"
        ? node.lifecycle.status
        : undefined;
    const related = new Set<string>();
    graph.edges.forEach((edge) => {
      if (edge.source_id === id) related.add(edge.target_id);
      if (edge.target_id === id) related.add(edge.source_id);
    });
    return {
      id,
      type: node.node_type,
      status,
      content: node.content,
      lifecycle: node.lifecycle,
      derived: node.derived,
      provenance: node.provenance,
      warnings: graph.warnings,
      raw: node,
      related: [...related].map((relatedId) => ({
        id: relatedId,
        label: "Runtime task",
        view: "runtime",
      })),
    };
  }
  const edge = graph.edges.find((item) => item.edge_id === id);
  return edge
    ? {
        id,
        type: edge.edge_type,
        content: edge.metadata,
        raw: edge,
        related: [
          { id: edge.source_id, label: "Source task", view: "runtime" },
          { id: edge.target_id, label: "Target task", view: "runtime" },
        ],
      }
    : null;
}

export async function layoutRuntimeGraph(
  graph: RuntimeGraph,
  direction: LayoutDirection,
) {
  const flowNodes: Array<Node<RuntimeNodeData>> = graph.nodes.map((source) => ({
    id: source.node_id,
    type: "runtimeTask",
    position: { x: 0, y: 0 },
    data: runtimeNodeData(source),
    ariaLabel: runtimeNodeLabel(source),
  }));
  const flowEdges: Edge[] = graph.edges.map((edge) => ({
    id: edge.edge_id,
    source: edge.source_id,
    target: edge.target_id,
    type: "smoothstep",
    markerEnd: { type: MarkerType.ArrowClosed, color: "#a7c87b" },
    style: { stroke: "#789681", strokeWidth: 1.5 },
    ariaLabel: `${edge.edge_type}: ${edge.source_id} to ${edge.target_id}`,
  }));
  const engine = await elk;
  const layout = await engine.layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": direction,
      "elk.spacing.nodeNode": "42",
      "elk.layered.spacing.nodeNodeBetweenLayers": "72",
    },
    children: flowNodes.map((node) => ({
      id: node.id,
      width: 270,
      height: 190,
    })),
    edges: flowEdges.map((edge) => ({
      id: edge.id,
      sources: [edge.source],
      targets: [edge.target],
    })),
  });
  const positions = new Map(
    layout.children?.map((node) => [
      node.id,
      { x: node.x ?? 0, y: node.y ?? 0 },
    ]) ?? [],
  );
  return {
    nodes: flowNodes.map((node) => ({
      ...node,
      position: positions.get(node.id) ?? node.position,
    })),
    edges: flowEdges,
  };
}

export function runtimeNodeData(source: RuntimeGraphNode): RuntimeNodeData {
  const content = source.content;
  const lifecycle = source.lifecycle;
  const derived = source.derived;
  const blockers = Array.isArray(derived.blocked_by_tasks)
    ? derived.blocked_by_tasks.filter(
        (item): item is string => typeof item === "string",
      )
    : [];
  return {
    source,
    description:
      typeof content.description === "string"
        ? content.description
        : "Untitled runtime task",
    status: typeof lifecycle.status === "string" ? lifecycle.status : "unknown",
    agents:
      stringList(lifecycle.active_agents).length > 0
        ? stringList(lifecycle.active_agents)
        : stringList(lifecycle.last_assigned_agents),
    requiredCount:
      typeof lifecycle.required_agent_count === "number"
        ? lifecycle.required_agent_count
        : null,
    dependencyReady:
      typeof derived.dependency_ready === "boolean"
        ? derived.dependency_ready
        : null,
    blockers,
    milestones: stringList(content.milestones),
  };
}

function RuntimeTaskNode({ data, selected }: NodeProps<Node<RuntimeNodeData>>) {
  return (
    <article
      className={`runtime-node runtime-node--${cssToken(data.status)}${selected ? " runtime-node--selected" : ""}`}
      aria-label={runtimeNodeLabel(data.source)}
    >
      <Handle type="target" position={Position.Left} />
      <div className="runtime-node-heading">
        <span
          className={`status-icon status-icon--${cssToken(data.status)}`}
          aria-hidden="true"
        />
        <strong>{data.status}</strong>
        <small>{data.source.node_type || "unknown type"}</small>
      </div>
      <p>{data.description}</p>
      <dl>
        <div>
          <dt>Agents</dt>
          <dd>{data.agents.join(", ") || "None"}</dd>
        </div>
        <div>
          <dt>Required</dt>
          <dd>{data.requiredCount ?? "Unknown"}</dd>
        </div>
        <div>
          <dt>Dependency-ready</dt>
          <dd>
            {data.dependencyReady === null
              ? "Unknown"
              : data.dependencyReady
                ? "Yes"
                : "No"}
          </dd>
        </div>
      </dl>
      {data.blockers.length > 0 && (
        <small className="blocked-by">
          Blocked by {data.blockers.join(", ")}
        </small>
      )}
      {data.milestones.length > 0 && (
        <small className="milestones">
          Milestones: {data.milestones.join(" · ")}
        </small>
      )}
      <Handle type="source" position={Position.Right} />
    </article>
  );
}

function GraphMessage({ title, detail }: { title: string; detail: string }) {
  return (
    <section className="state-message" role="status">
      <h2>{title}</h2>
      <p>{detail}</p>
    </section>
  );
}

function runtimeNodeLabel(source: RuntimeGraphNode): string {
  const data = runtimeNodeData(source);
  return `${data.description}; status ${data.status}; dependency-ready ${data.dependencyReady === null ? "unknown" : data.dependencyReady ? "yes" : "no"}`;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function cssToken(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9_-]/g, "-") || "unknown";
}
