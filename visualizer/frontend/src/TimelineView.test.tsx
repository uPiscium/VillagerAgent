import { describe, expect, it } from "vitest";

import {
  durationWidth,
  orderedTimelineItems,
  parseTimestamp,
  timelineGeometry,
  timelineInspectorEntity,
} from "./TimelineView";
import type { Timeline, TimelineItem } from "./api";

const exact = (
  id: string,
  agent: string,
  start: string,
  end: string,
): TimelineItem => ({
  action_id: id,
  agent,
  record_index: 0,
  tool: "move",
  status: "success",
  timing: "exact",
  start_time: start,
  end_time: end,
  duration_seconds: 2,
  arguments: {},
  related_task_ids: [],
  observation_ids: [],
  claim_ids: [],
});
const base: Timeline = {
  bounds: {
    start_time: "2026-07-17 10:00:00",
    end_time: "2026-07-17 10:00:10",
    timezone_kind: "naive_local",
  },
  lanes: [],
  warnings: [],
};

describe("TimelineView", () => {
  it("places parallel exact actions on one shared relative ruler", () => {
    const first = exact(
      "a",
      "Alice",
      "2026-07-17 10:00:00",
      "2026-07-17 10:00:05",
    );
    const second = exact(
      "b",
      "Bob",
      "2026-07-17 10:00:02",
      "2026-07-17 10:00:07",
    );
    expect(timelineGeometry(first, base)).toEqual({ left: 0, width: 50 });
    expect(timelineGeometry(second, base)).toEqual({ left: 20, width: 50 });
  });

  it("does not invent geometry for duration-only, untimed, or missing bounds", () => {
    const duration = {
      ...exact("d", "Alice", "", ""),
      timing: "duration_only" as const,
      start_time: null,
      end_time: null,
    };
    expect(timelineGeometry(duration, base)).toBeNull();
    expect(
      timelineGeometry(
        exact("a", "Alice", "2026-07-17 10:00:00", "2026-07-17 10:00:01"),
        { ...base, bounds: null },
      ),
    ).toBeNull();
  });

  it("preserves lane and record order including untimed actions", () => {
    const untimed = {
      ...exact("untimed", "Alice", "", ""),
      timing: "untimed" as const,
      start_time: null,
      end_time: null,
    };
    const timeline = {
      ...base,
      lanes: [
        { agent: "Alice", items: [untimed] },
        {
          agent: "Bob",
          items: [
            exact("bob", "Bob", "2026-07-17 10:00:00", "2026-07-17 10:00:01"),
          ],
        },
      ],
    };
    expect(
      orderedTimelineItems(timeline).map((item) => item.action_id),
    ).toEqual(["untimed", "bob"]);
  });

  it("handles zero duration and malformed timestamps without invalid numbers", () => {
    const zero = {
      ...exact("zero", "Alice", "", ""),
      timing: "duration_only" as const,
      start_time: null,
      end_time: null,
      duration_seconds: 0,
    };
    expect(durationWidth(zero, [zero])).toBe("8%");
    expect(parseTimestamp("not-a-time")).toBeNull();
    expect(Number.isFinite(parseTimestamp("2026-07-17 10:00:00"))).toBe(true);
  });

  it("adapts timeline actions and cross-view relations to the shared Inspector", () => {
    const item = {
      ...exact(
        "action-1",
        "Alice",
        "2026-07-17 10:00:00",
        "2026-07-17 10:00:02",
      ),
      related_task_ids: ["task-1"],
      observation_ids: ["observation-1"],
      claim_ids: ["claim-1"],
    };
    const inspected = timelineInspectorEntity(
      { ...base, lanes: [{ agent: "Alice", items: [item] }] },
      "action-1",
    );
    expect(inspected).toMatchObject({ id: "action-1", status: "success" });
    expect(inspected?.related.map((related) => related.view)).toEqual([
      "runtime",
      "analysis",
      "analysis",
    ]);
    expect(timelineInspectorEntity(base, "missing")).toBeNull();
  });
});
