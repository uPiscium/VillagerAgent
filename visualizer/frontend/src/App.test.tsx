import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

describe("App", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows the backend connection", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "ok",
        service: "villageragent-visualizer",
        api_version: "v1",
      }),
    }));

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Backend connected" })).toBeInTheDocument();
  });

  it("keeps a usable page when the backend is unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Backend unavailable" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "VillagerAgent Visualizer" })).toBeInTheDocument();
  });
});
