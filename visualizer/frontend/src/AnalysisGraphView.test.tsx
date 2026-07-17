import { describe, expect, it } from "vitest";

import {
  filterAnalysisGraph,
  layoutAnalysisGraph,
  MAX_ANALYSIS_EDGES,
  MAX_ANALYSIS_NODES,
  type AnalysisFilters,
} from "./AnalysisGraphView";
import type { AnalysisGraph } from "./api";

const graph: AnalysisGraph = {
  authority: "posthoc_analysis_projection",
  schema_version: "1.0.0",
  task_state_source: "real_runtime",
  summary: {},
  schema: {},
  mapping: {},
  applied_filters: {},
  warnings: [],
  nodes: [
    {
      node_id: "task-1",
      node_type: "minecraft_task",
      content: { description: "Find chest", assigned_agents: ["Bob"] },
      provenance: {},
      confidence: 1,
      runtime_task_id: "runtime:task:1",
      extra: {},
    },
    {
      node_id: "task-2",
      node_type: "minecraft_task",
      content: { description: "Return home" },
      provenance: {},
      confidence: 1,
      runtime_task_id: "runtime:task:2",
      extra: {},
    },
    {
      node_id: "action-1",
      node_type: "minecraft_action",
      content: { tool: "openContainer", agent: "Bob" },
      provenance: { agent: "Bob" },
      confidence: 0.8,
      runtime_task_id: null,
      extra: {},
    },
    {
      node_id: "observation-1",
      node_type: "minecraft_observation",
      content: { result: "empty chest" },
      provenance: { agent: "Bob" },
      confidence: 0.6,
      runtime_task_id: null,
      extra: {},
    },
    {
      node_id: "future-1",
      node_type: "future_entity",
      content: { message: "unknown payload" },
      provenance: {},
      confidence: null,
      runtime_task_id: null,
      extra: {},
    },
  ],
  edges: [
    {
      edge_id: "e1",
      source_id: "task-1",
      target_id: "task-2",
      edge_type: "precedes_task",
      metadata: {},
      extra: {},
    },
    {
      edge_id: "e2",
      source_id: "task-1",
      target_id: "action-1",
      edge_type: "task_invokes_action",
      metadata: {},
      extra: {},
    },
    {
      edge_id: "e3",
      source_id: "action-1",
      target_id: "observation-1",
      edge_type: "produces_observation",
      metadata: {},
      extra: {},
    },
    {
      edge_id: "e4",
      source_id: "future-1",
      target_id: "observation-1",
      edge_type: "future_edge",
      metadata: {},
      extra: {},
    },
  ],
};

const defaults: AnalysisFilters = {
  nodeType: "",
  edgeType: "",
  agent: "",
  minimumConfidence: 0,
  text: "",
  focus: "all",
  selectedNodeId: null,
};

describe("AnalysisGraphView", () => {
  it("preserves unknown node and edge types without filters", () => {
    const filtered = filterAnalysisGraph(graph, defaults);
    expect(filtered.authority).toBe("posthoc_analysis_projection");
    expect(
      filtered.nodes.some((node) => node.node_type === "future_entity"),
    ).toBe(true);
    expect(
      filtered.edges.some((edge) => edge.edge_type === "future_edge"),
    ).toBe(true);
  });

  it("never leaves dangling edges after combined filters", () => {
    const filtered = filterAnalysisGraph(graph, {
      ...defaults,
      agent: "Bob",
      minimumConfidence: 0.7,
    });
    const ids = new Set(filtered.nodes.map((node) => node.node_id));
    expect(filtered.nodes.map((node) => node.node_id)).toEqual([
      "task-1",
      "action-1",
    ]);
    expect(
      filtered.edges.every(
        (edge) => ids.has(edge.source_id) && ids.has(edge.target_id),
      ),
    ).toBe(true);
    expect(filtered.edges.map((edge) => edge.edge_id)).toEqual(["e2"]);
  });

  it("supports one-hop, descendants, and task-subgraph focus", () => {
    const oneHop = filterAnalysisGraph(graph, {
      ...defaults,
      focus: "one-hop",
      selectedNodeId: "action-1",
    });
    expect(oneHop.nodes.map((node) => node.node_id)).toEqual([
      "task-1",
      "action-1",
      "observation-1",
    ]);
    const descendants = filterAnalysisGraph(graph, {
      ...defaults,
      focus: "descendants",
      selectedNodeId: "task-1",
    });
    expect(descendants.nodes.map((node) => node.node_id)).toEqual([
      "task-1",
      "task-2",
      "action-1",
      "observation-1",
    ]);
    const taskSubgraph = filterAnalysisGraph(graph, {
      ...defaults,
      focus: "task-subgraph",
      selectedNodeId: "task-1",
    });
    expect(taskSubgraph.nodes.map((node) => node.node_id)).toEqual([
      "task-1",
      "action-1",
      "observation-1",
    ]);
  });

  it("lays out directed analysis edges and defines finite layout guards", async () => {
    const layout = await layoutAnalysisGraph(graph);
    expect(layout.edges[0]).toMatchObject({
      source: "task-1",
      target: "task-2",
    });
    expect(layout.nodes[1].position.x).toBeGreaterThan(
      layout.nodes[0].position.x,
    );
    expect(MAX_ANALYSIS_NODES).toBe(200);
    expect(MAX_ANALYSIS_EDGES).toBe(500);
  });
});
