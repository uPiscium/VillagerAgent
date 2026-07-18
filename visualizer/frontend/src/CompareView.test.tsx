import { describe, expect, it } from "vitest";
import { comparisonCsv, comparisonJson } from "./CompareView";
import type { Comparison } from "./api";

const comparison = {
  runs: [
    {
      run_id: "a",
      name: "A",
      state: "timed_out",
      mode: "execute",
      policy: "original",
      task_name: "Task",
      task_type: "build",
      task_state_source: "real_runtime",
      snapshot_source: "runtime_result",
      progress: null,
      score: null,
      task_count: null,
      completed_task_count: null,
      failed_task_count: null,
      action_count: 2,
      failed_action_count: 1,
      duration_seconds: null,
      recommendation_adopted_count: null,
      runtime_selected_task_ids: ["task-1"],
      posthoc_ranked_task_order: ["task-2"],
      agent_action_counts: { Alice: 2 },
      agent_idle_seconds: null,
      error: "timeout",
    },
  ],
  warnings: [],
  semantics: { missing_values: "null", inference: "descriptive_only" },
} as Comparison;
describe("comparison exports", () => {
  it("uses screen semantics and preserves missing values", () => {
    expect(JSON.parse(comparisonJson(comparison))).toEqual(comparison);
    const csv = comparisonCsv(comparison);
    expect(csv).toContain('"timed_out"');
    expect(csv).toContain('"N/A"');
    expect(csv).toContain('"[""task-1""]"');
  });
});
