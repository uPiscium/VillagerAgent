import { describe, expect, it } from "vitest";
import { worldFrameUrl } from "./WorldView";

describe("WorldView", () => {
  it("passes selected agent/action context without control commands", () => {
    const url = new URL(
      worldFrameUrl("http://127.0.0.1:3007", {
        agent: "Alice",
        action: "minecraft:action:Alice:0",
        target: "x=1, y=2, z=3",
      }),
    );
    expect(url.origin).toBe("http://127.0.0.1:3007");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      va_agent: "Alice",
      va_action: "minecraft:action:Alice:0",
      va_target: "x=1, y=2, z=3",
    });
    expect(url.search).not.toContain("command");
  });
});
