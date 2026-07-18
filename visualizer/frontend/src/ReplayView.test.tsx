import { describe, expect, it } from "vitest";
import { clampReplaySeq } from "./ReplayView";

describe("ReplayView", () => {
  it("clamps backward, forward, jump, and malformed sequence input", () => {
    expect(clampReplaySeq(-1, 8)).toBe(0);
    expect(clampReplaySeq(4.6, 8)).toBe(5);
    expect(clampReplaySeq(20, 8)).toBe(8);
    expect(clampReplaySeq(Number.NaN, 8)).toBe(0);
  });
});
