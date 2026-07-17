import { describe, expect, it } from "vitest";

import type { RuntimeGraph, RuntimeGraphNode } from "./api";
import { layoutRuntimeGraph, runtimeNodeData } from "./RuntimeGraphView";

const taskNode: RuntimeGraphNode = {
  node_id: "runtime:task:1",
  node_type: "runtime_task",
  content: {
    description: "Collect enough oak logs to build a shelter with a deliberately long task description",
    milestones: ["find forest", "collect logs"],
  },
  lifecycle: {
    status: "future_status",
    active_agents: ["Alice", "Bob"],
    required_agent_count: 2,
  },
  derived: {
    dependency_ready: false,
    blocked_by_tasks: ["runtime:task:0"],
  },
  provenance: {},
  extra: {},
};

describe("RuntimeGraphView", () => {
  it("preserves canonical dependency semantics and unknown status", () => {
    const data = runtimeNodeData(taskNode);

    expect(data.status).toBe("future_status");
    expect(data.agents).toEqual(["Alice", "Bob"]);
    expect(data.requiredCount).toBe(2);
    expect(data.dependencyReady).toBe(false);
    expect(data.blockers).toEqual(["runtime:task:0"]);
    expect(data.milestones).toEqual(["find forest", "collect logs"]);
    expect(data).not.toHaveProperty("runnable");
  });

  it("creates stable directed edges and changes layered orientation", async () => {
    const second = { ...taskNode, node_id: "runtime:task:2", content: { description: "Build shelter" } };
    const graph: RuntimeGraph = {
      authority: "canonical_runtime_state",
      schema_version: "1.0.0",
      snapshot_source: "real_runtime",
      source_of_truth: "runtime_task_dag",
      summary: {},
      nodes: [taskNode, second],
      edges: [{
        edge_id: "runtime:edge:1",
        source_id: taskNode.node_id,
        target_id: second.node_id,
        edge_type: "precedes_task",
        metadata: {},
        extra: {},
      }],
      mutation_history: [],
      warnings: [],
    };

    const horizontal = await layoutRuntimeGraph(graph, "RIGHT");
    const vertical = await layoutRuntimeGraph(graph, "DOWN");

    expect(horizontal.edges[0]).toMatchObject({ id: "runtime:edge:1", source: "runtime:task:1", target: "runtime:task:2" });
    expect(horizontal.nodes[1].position.x).toBeGreaterThan(horizontal.nodes[0].position.x);
    expect(vertical.nodes[1].position.y).toBeGreaterThan(vertical.nodes[0].position.y);
    expect(horizontal.nodes[0].ariaLabel).toContain("dependency-ready no");
  });
});
