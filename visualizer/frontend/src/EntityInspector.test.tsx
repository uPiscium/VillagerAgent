import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  EntityInspector,
  entityHref,
  type InspectorEntity,
} from "./EntityInspector";

const entity: InspectorEntity = {
  id: "task:1",
  type: "runtime_task",
  status: "blocked",
  content: { description: "Build shelter" },
  lifecycle: { status: "blocked" },
  derived: { dependency_ready: false },
  provenance: { source: "runtime" },
  confidence: null,
  warnings: [{ code: "partial", message: "Artifact was incomplete" }],
  raw: { node_id: "task:1", content: { description: "Build shelter" } },
  related: [{ id: "task:0", label: "Dependency", view: "runtime" }],
};

afterEach(cleanup);

describe("EntityInspector", () => {
  it("shows status, details, sanitized raw DTO, and related navigation", () => {
    render(
      <MemoryRouter>
        <EntityInspector
          runId="run/1"
          selectionId="task:1"
          entity={entity}
          onClose={() => undefined}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole("dialog", { name: /task:1/ })).toHaveTextContent(
      "blocked",
    );
    expect(screen.getAllByText(/Build shelter/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Artifact was incomplete/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /task:0/ })).toHaveAttribute(
      "href",
      "/runs/run%2F1/runtime?entity=task%3A0",
    );
    expect(screen.getByText(/node_id/)).toBeInTheDocument();
  });

  it("contains a missing selection without replacing the surrounding view", () => {
    render(
      <MemoryRouter>
        <main>Timeline remains mounted</main>
        <EntityInspector
          runId="run"
          selectionId="missing"
          entity={null}
          onClose={() => undefined}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("Timeline remains mounted")).toBeInTheDocument();
    expect(screen.getByText("Entity unavailable")).toBeInTheDocument();
  });

  it("focuses close and closes with Escape", () => {
    const onClose = vi.fn();
    render(
      <MemoryRouter>
        <EntityInspector
          runId="run"
          selectionId="task:1"
          entity={entity}
          onClose={onClose}
        />
      </MemoryRouter>,
    );
    expect(
      screen.getByRole("button", { name: "Close Entity Inspector" }),
    ).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("builds encoded cross-view selection URLs", () => {
    expect(
      entityHref("run 1", { id: "claim/2", label: "Claim", view: "analysis" }),
    ).toBe("/runs/run%201/analysis?entity=claim%2F2");
  });
});
