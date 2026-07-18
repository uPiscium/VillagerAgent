import { describe, expect, it } from "vitest";

import {
  applyLiveEnvelope,
  liveStreamUrl,
  reconnectDelay,
  type LiveStreamEnvelope,
  type LiveUpdateState,
} from "./LiveRunUpdates";

const state: LiveUpdateState = {
  status: "live",
  revision: 4,
  lastEventAt: 100,
  reconnectAttempt: 0,
};
const envelope = (
  revision: number,
  type: LiveStreamEnvelope["type"] = "snapshot",
  payload: unknown = {},
): LiveStreamEnvelope => ({
  version: "1.0",
  type,
  run_id: "group/run",
  revision,
  emitted_at: "2026-07-18T10:00:00Z",
  payload,
});

describe("LiveRunUpdates", () => {
  it("never rolls state back for duplicate or out-of-order revisions", () => {
    expect(applyLiveEnvelope(state, envelope(4), 200)).toBe(state);
    expect(applyLiveEnvelope(state, envelope(3), 200)).toBe(state);
    expect(applyLiveEnvelope(state, envelope(5), 200)).toMatchObject({
      status: "live",
      revision: 5,
      lastEventAt: 200,
    });
  });

  it("represents disconnect, error, and every terminal transition textually", () => {
    expect(
      applyLiveEnvelope(state, envelope(5, "run_unavailable"), 200).status,
    ).toBe("disconnected");
    expect(applyLiveEnvelope(state, envelope(5, "error"), 200).status).toBe(
      "stale",
    );
    expect(
      applyLiveEnvelope(
        state,
        envelope(5, "run_completed", { state: "completed" }),
        200,
      ).status,
    ).toBe("completed");
    expect(
      applyLiveEnvelope(
        state,
        envelope(5, "run_completed", { state: "failed" }),
        200,
      ).status,
    ).toBe("failed");
    expect(
      applyLiveEnvelope(
        state,
        envelope(5, "run_completed", { state: "timed_out" }),
        200,
      ).status,
    ).toBe("timed_out");
  });

  it("encodes nested run IDs and selects ws/wss from the page protocol", () => {
    expect(
      liveStreamUrl("group/run", { protocol: "http:", host: "localhost:5173" }),
    ).toBe("ws://localhost:5173/api/v1/runs/group%2Frun/stream");
    expect(
      liveStreamUrl("run", { protocol: "https:", host: "example.test" }),
    ).toBe("wss://example.test/api/v1/runs/run/stream");
  });

  it("uses capped exponential reconnect delays", () => {
    expect([1, 2, 3, 9].map(reconnectDelay)).toEqual([
      1_000, 2_000, 4_000, 30_000,
    ]);
  });
});
