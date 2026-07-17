import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow, useEdgesState, useNodesState, type Edge, type Node, type NodeProps } from "@xyflow/react";

import { fetchAnalysisGraph, type AnalysisGraph, type AnalysisGraphNode } from "./api";

export const MAX_ANALYSIS_NODES = 200;
export const MAX_ANALYSIS_EDGES = 500;
type FocusMode = "all" | "selected" | "one-hop" | "ancestors" | "descendants" | "task-subgraph";
export type AnalysisFilters = { nodeType: string; edgeType: string; agent: string; minimumConfidence: number; text: string; focus: FocusMode; selectedNodeId: string | null };
type AnalysisNodeData = Record<string, unknown> & { source: AnalysisGraphNode; title: string; detail: string; agent: string; confidence: number | null };

const nodeTypes = { analysisNode: AnalysisNode };
const elk = import("elkjs/lib/elk.bundled.js").then(({ default: ELK }) => new ELK());

export function AnalysisGraphView({ runId }: { runId: string }) {
  const graph = useQuery({ queryKey: ["analysis-graph", runId], queryFn: () => fetchAnalysisGraph(runId) });
  const [filters, setFilters] = useState<AnalysisFilters>({ nodeType: "", edgeType: "", agent: "", minimumConfidence: 0, text: "", focus: "all", selectedNodeId: null });
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<AnalysisNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  const filtered = graph.data ? filterAnalysisGraph(graph.data, filters) : null;
  const tooLarge = Boolean(filtered && (filtered.nodes.length > MAX_ANALYSIS_NODES || filtered.edges.length > MAX_ANALYSIS_EDGES));

  useEffect(() => {
    if (!filtered || tooLarge) { setNodes([]); setEdges([]); return; }
    let active = true;
    void layoutAnalysisGraph(filtered).then((layout) => {
      if (!active) return;
      setNodes(layout.nodes.map((node) => ({ ...node, selected: node.id === filters.selectedNodeId })));
      setEdges(layout.edges);
    });
    return () => { active = false; };
  }, [graph.data, filters.nodeType, filters.edgeType, filters.agent, filters.minimumConfidence, filters.text, filters.focus, filters.selectedNodeId, tooLarge, setEdges, setNodes]);

  if (graph.isPending) return <Message title="Loading Analysis DAG" detail="Reading the post-hoc projection." />;
  if (graph.isError) return <Message title="Analysis DAG unavailable" detail={graph.error.message} />;

  const nodeTypesAvailable = [...new Set(graph.data.nodes.map((node) => node.node_type))].sort();
  const edgeTypesAvailable = [...new Set(graph.data.edges.map((edge) => edge.edge_type))].sort();
  const agents = [...new Set(graph.data.nodes.map(nodeAgent).filter(Boolean))].sort();
  return (
    <section className="analysis-graph-view" aria-label="Post-hoc Minecraft Analysis DAG">
      <header className="authority-banner authority-banner--analysis"><div><strong>Post-hoc Analysis Projection</strong><span>Not runtime epistemic or action-candidate authority</span></div><dl><div><dt>Task source</dt><dd>{graph.data.task_state_source}</dd></div><div><dt>Visible</dt><dd>{filtered?.nodes.length ?? 0} / {graph.data.nodes.length} nodes</dd></div></dl></header>
      <div className="analysis-filters" aria-label="Analysis graph filters">
        <label>Node type<select value={filters.nodeType} onChange={(event) => setFilters({ ...filters, nodeType: event.target.value })}><option value="">All</option>{nodeTypesAvailable.map((type) => <option key={type}>{type}</option>)}</select></label>
        <label>Edge type<select value={filters.edgeType} onChange={(event) => setFilters({ ...filters, edgeType: event.target.value })}><option value="">All</option>{edgeTypesAvailable.map((type) => <option key={type}>{type}</option>)}</select></label>
        <label>Agent<select value={filters.agent} onChange={(event) => setFilters({ ...filters, agent: event.target.value })}><option value="">All</option>{agents.map((agent) => <option key={agent}>{agent}</option>)}</select></label>
        <label>Confidence ≥ {filters.minimumConfidence.toFixed(1)}<input type="range" min="0" max="1" step="0.1" value={filters.minimumConfidence} onChange={(event) => setFilters({ ...filters, minimumConfidence: Number(event.target.value) })} /></label>
        <label>Text<input type="search" value={filters.text} onChange={(event) => setFilters({ ...filters, text: event.target.value })} placeholder="Search content" /></label>
        <label>Focus<select value={filters.focus} onChange={(event) => setFilters({ ...filters, focus: event.target.value as FocusMode })}><option value="all">All</option><option value="selected">Selected</option><option value="one-hop">1-hop</option><option value="ancestors">Ancestors</option><option value="descendants">Descendants</option><option value="task-subgraph">Task subgraph</option></select></label>
      </div>
      {tooLarge ? <Message title="Graph exceeds layout threshold" detail={`Filter below ${MAX_ANALYSIS_NODES} nodes and ${MAX_ANALYSIS_EDGES} edges before layout.`} /> : filtered?.nodes.length === 0 ? <Message title="No matching analysis entities" detail="Adjust filters or select another focus mode." /> : (
        <div className="graph-canvas"><ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onNodeClick={(_, node) => setFilters({ ...filters, selectedNodeId: node.id })} fitView nodesConnectable={false} aria-label="Post-hoc analysis dependency graph"><Background color="#493b56" gap={24} /><MiniMap pannable zoomable nodeColor="#a982bd" /><Controls showInteractive={false} /></ReactFlow></div>
      )}
      <div className="analysis-legend" aria-label="Analysis entity legend">{["minecraft_task", "minecraft_action", "minecraft_observation", "minecraft_claim", "unknown"].map((type) => <span key={type}><i className={`entity-icon entity-icon--${entityKind(type)}`} aria-hidden="true" />{type.replace("minecraft_", "")}</span>)}</div>
      {filters.selectedNodeId && <p className="selection-note" aria-live="polite">Selected entity: <code>{filters.selectedNodeId}</code></p>}
    </section>
  );
}

export function filterAnalysisGraph(graph: AnalysisGraph, filters: AnalysisFilters): AnalysisGraph {
  let ids = new Set(graph.nodes.filter((node) => (!filters.nodeType || node.node_type === filters.nodeType) && (!filters.agent || nodeAgent(node) === filters.agent) && (filters.minimumConfidence === 0 || (node.confidence ?? -1) >= filters.minimumConfidence) && (!filters.text || JSON.stringify(node.content).toLowerCase().includes(filters.text.toLowerCase()))).map((node) => node.node_id));
  if (filters.focus !== "all") ids = intersect(ids, focusIds(graph, filters.selectedNodeId, filters.focus));
  const nodes = graph.nodes.filter((node) => ids.has(node.node_id));
  const edges = graph.edges.filter((edge) => ids.has(edge.source_id) && ids.has(edge.target_id) && (!filters.edgeType || edge.edge_type === filters.edgeType));
  return { ...graph, nodes, edges };
}

export async function layoutAnalysisGraph(graph: AnalysisGraph) {
  const nodes: Array<Node<AnalysisNodeData>> = graph.nodes.map((source) => ({ id: source.node_id, type: "analysisNode", position: { x: 0, y: 0 }, data: analysisNodeData(source), ariaLabel: `${source.node_type}: ${analysisNodeData(source).title}` }));
  const edges: Edge[] = graph.edges.map((edge) => ({ id: edge.edge_id, source: edge.source_id, target: edge.target_id, type: "smoothstep", label: edge.edge_type, markerEnd: { type: MarkerType.ArrowClosed, color: "#a989bd" }, style: { stroke: "#886e98" } }));
  const engine = await elk; const layout = await engine.layout({ id: "root", layoutOptions: { "elk.algorithm": "layered", "elk.direction": "RIGHT", "elk.spacing.nodeNode": "38", "elk.layered.spacing.nodeNodeBetweenLayers": "68" }, children: nodes.map((node) => ({ id: node.id, width: 240, height: 145 })), edges: edges.map((edge) => ({ id: edge.id, sources: [edge.source], targets: [edge.target] })) });
  const positions = new Map(layout.children?.map((node) => [node.id, { x: node.x ?? 0, y: node.y ?? 0 }]) ?? []);
  return { nodes: nodes.map((node) => ({ ...node, position: positions.get(node.id) ?? node.position })), edges };
}

export function analysisNodeData(source: AnalysisGraphNode): AnalysisNodeData { const content = source.content; return { source, title: stringValue(content.description) || stringValue(content.tool) || stringValue(content.message) || source.node_id, detail: stringValue(content.status) || stringValue((content.result as Record<string, unknown> | undefined)?.status) || "Recorded entity", agent: nodeAgent(source), confidence: source.confidence }; }
function AnalysisNode({ data, selected }: NodeProps<Node<AnalysisNodeData>>) { const kind = entityKind(data.source.node_type); return <article className={`analysis-node analysis-node--${kind}${selected ? " analysis-node--selected" : ""}`}><Handle type="target" position={Position.Left} /><div><span className={`entity-icon entity-icon--${kind}`} aria-hidden="true" /><strong>{data.source.node_type}</strong></div><p>{data.title}</p><small>{data.agent || "No agent"} · confidence {data.confidence ?? "N/A"}</small><small>{data.detail}</small><Handle type="source" position={Position.Right} /></article>; }
function focusIds(graph: AnalysisGraph, selected: string | null, mode: FocusMode): Set<string> { if (!selected) return new Set(); if (mode === "selected") return new Set([selected]); const ids = new Set([selected]); if (mode === "one-hop") { graph.edges.forEach((edge) => { if (edge.source_id === selected) ids.add(edge.target_id); if (edge.target_id === selected) ids.add(edge.source_id); }); return ids; } const forward = mode === "descendants" || mode === "task-subgraph"; let changed = true; while (changed) { changed = false; graph.edges.forEach((edge) => { const from = forward ? edge.source_id : edge.target_id; const to = forward ? edge.target_id : edge.source_id; if (ids.has(from) && !ids.has(to) && !(mode === "task-subgraph" && edge.edge_type === "precedes_task")) { ids.add(to); changed = true; } }); } return ids; }
function intersect(left: Set<string>, right: Set<string>) { return new Set([...left].filter((value) => right.has(value))); }
function nodeAgent(node: AnalysisGraphNode): string { return stringValue(node.content.agent) || stringValue(node.provenance.agent) || (Array.isArray(node.content.assigned_agents) ? stringValue(node.content.assigned_agents[0]) : ""); }
function entityKind(type: string) { if (type === "minecraft_task") return "task"; if (type === "minecraft_action") return "action"; if (type === "minecraft_observation") return "observation"; if (type === "minecraft_claim") return "claim"; return "unknown"; }
function stringValue(value: unknown): string { return typeof value === "string" ? value : ""; }
function Message({ title, detail }: { title: string; detail: string }) { return <section className="state-message" role="status"><h2>{title}</h2><p>{detail}</p></section>; }
