import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import type { RunManifest } from "./api";

const baseRun: RunManifest = {
  run_id: "group/run-a",
  name: "run-a",
  state: "completed",
  started_at: "2026-07-17T10:00:00Z",
  mode: "execute",
  task: { name: "Build shelter", task_type: "construction", index: 2 },
  policy: "dual-dag",
  source: {
    producer: "benchmarks.minecraft.experiment",
    task_state: "real_runtime",
    snapshot: "real_runtime",
    source_of_truth: "runtime_task_dag",
  },
  progress: 0.75,
  error: null,
  artifacts: {
    summary: true,
    runtime_graph: true,
    runtime_checkpoint: false,
    analysis_graph: false,
    action_log: true,
  },
  warnings: [],
};

describe("App", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.history.replaceState({}, "", "/");
  });

  it("lists terminal and partial run states without hiding failures", async () => {
    mockApi([
      baseRun,
      {
        ...baseRun,
        run_id: "failed",
        name: "failed-run",
        state: "failed",
        error: "boom",
      },
      {
        ...baseRun,
        run_id: "timeout",
        name: "timeout-run",
        state: "timed_out",
      },
      { ...baseRun, run_id: "partial", name: "partial-run", state: "partial" },
    ]);

    render(<App />);

    expect(await screen.findByText("run-a")).toBeInTheDocument();
    expect(screen.getByText("failed-run")).toBeInTheDocument();
    expect(screen.getByText("timeout-run")).toBeInTheDocument();
    expect(screen.getByText("partial-run")).toBeInTheDocument();
    expect(await screen.findByText("API v1")).toBeInTheDocument();
  });

  it("keeps a usable shell when the backend is unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );

    render(<App />);

    expect(await screen.findByText("Runs unavailable")).toBeInTheDocument();
    expect(screen.getByText("Backend unavailable")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Choose a run to inspect." }),
    ).toBeInTheDocument();
  });

  it("shows an empty state separately from loading and errors", async () => {
    mockApi([]);

    render(<App />);

    expect(await screen.findByText("No runs found")).toBeInTheDocument();
    expect(screen.queryByText("Runs unavailable")).not.toBeInTheDocument();
  });

  it("restores a nested run route and safely encodes API and navigation URLs", async () => {
    const fetchMock = mockApi([baseRun]);
    window.history.replaceState({}, "", "/runs/group%2Frun-a/overview");

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "run-a" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Build shelter")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/runs/group%2Frun-a");
    const runLink = screen.getByRole("link", { name: /run-a/ });
    expect(runLink).toHaveAttribute("href", "/runs/group%2Frun-a/overview");
  });

  it("disables unavailable tabs and keeps available routes keyboard reachable", async () => {
    mockApi([baseRun]);
    window.history.replaceState({}, "", "/runs/group%2Frun-a/overview");

    render(<App />);
    await screen.findByRole("heading", { name: "run-a" });

    expect(screen.getByText("Analysis DAG")).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(screen.getByRole("link", { name: "Runtime DAG" })).toHaveAttribute(
      "href",
      "/runs/group%2Frun-a/runtime",
    );
    expect(screen.getByRole("link", { name: "Timeline" })).toHaveAttribute(
      "href",
      "/runs/group%2Frun-a/timeline",
    );
  });

  it("shows the unavailable state when a disabled route is opened directly", async () => {
    mockApi([baseRun]);
    window.history.replaceState({}, "", "/runs/group%2Frun-a/analysis");

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Analysis DAG unavailable" }),
    ).toBeInTheDocument();
  });

  it("keeps live data visible across ordered snapshots and disconnects without sending controls", async () => {
    const liveRun = { ...baseRun, state: "live" as const };
    mockApi([liveRun]);
    const sockets: FakeWebSocket[] = [];
    vi.stubGlobal(
      "WebSocket",
      class extends FakeWebSocket {
        constructor(url: string) {
          super(url);
          sockets.push(this);
        }
      },
    );
    window.history.replaceState({}, "", "/runs/group%2Frun-a/overview");

    render(<App />);
    expect(
      await screen.findByRole("heading", { name: "run-a" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Reconnecting")).toBeInTheDocument();
    expect(sockets[0].url).toContain("/api/v1/runs/group%2Frun-a/stream");

    act(() =>
      sockets[0].emit({
        version: "1.0",
        type: "snapshot",
        run_id: "group/run-a",
        revision: 2,
        emitted_at: "2026-07-18T10:00:00Z",
        payload: runtimeGraph(),
      }),
    );
    expect(screen.getByText("Revision 2").parentElement).toHaveTextContent("Live");
    act(() =>
      sockets[0].emit({
        version: "1.0",
        type: "error",
        run_id: "group/run-a",
        revision: 1,
        emitted_at: "2026-07-18T09:00:00Z",
        payload: {},
      }),
    );
    expect(screen.getByText("Revision 2").parentElement).toHaveTextContent("Live");
    act(() => sockets[0].disconnect());
    expect(screen.getByText("Disconnected")).toBeInTheDocument();
    expect(screen.getByText("Build shelter")).toBeInTheDocument();
    expect(sockets[0].send).not.toHaveBeenCalled();
  });
});

class FakeWebSocket {
  onmessage: ((event: MessageEvent) => void) | null = null;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  send = vi.fn();
  constructor(readonly url: string) {}
  close() {
    this.onclose?.();
  }
  emit(envelope: unknown) {
    this.onmessage?.({ data: JSON.stringify(envelope) } as MessageEvent);
  }
  disconnect() {
    this.onclose?.();
  }
}

function runtimeGraph() {
  return {
    authority: "canonical_runtime_state",
    schema_version: "1.0.0",
    snapshot_source: "runtime_checkpoint",
    source_of_truth: "runtime_task_dag",
    summary: {},
    nodes: [],
    edges: [],
    mutation_history: [],
    warnings: [],
  };
}

function mockApi(runs: RunManifest[]) {
  const fetchMock = vi.fn(async (input: string | URL | Request) => {
    const url = String(input);
    if (url === "/api/v1/health") {
      return jsonResponse({
        status: "ok",
        service: "villageragent-visualizer",
        api_version: "v1",
      });
    }
    if (url === "/api/v1/runs") {
      return jsonResponse({ runs });
    }
    const prefix = "/api/v1/runs/";
    if (url.startsWith(prefix)) {
      const runId = decodeURIComponent(url.slice(prefix.length));
      const run = runs.find((item) => item.run_id === runId);
      return run
        ? jsonResponse(run)
        : jsonResponse({ detail: "Run not found" }, 404);
    }
    return jsonResponse({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}
