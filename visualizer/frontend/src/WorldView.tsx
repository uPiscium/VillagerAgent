import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { fetchTimeline, fetchWorldViewConfig, type TimelineItem } from "./api";

export function WorldView({ runId }: { runId: string }) {
  const config = useQuery({
    queryKey: ["world-view-config"],
    queryFn: fetchWorldViewConfig,
  });
  const timeline = useQuery({
    queryKey: ["timeline", runId],
    queryFn: () => fetchTimeline(runId),
    enabled: config.data?.enabled === true,
  });
  const [searchParams, setSearchParams] = useSearchParams();
  const [agent, setAgent] = useState("");
  const [frameKey, setFrameKey] = useState(0);

  if (config.isPending)
    return (
      <Message
        title="Checking World View"
        detail="Reading optional viewer configuration."
      />
    );
  if (config.isError || !config.data.enabled || !config.data.url)
    return (
      <section className="world-disabled">
        <p className="eyebrow">Optional integration</p>
        <h2>World View disabled</h2>
        <p>
          Start a compatible read-only prismarine viewer separately, then pass
          its explicit URL to the Visualizer.
        </p>
        <code>--world-view-url http://127.0.0.1:3007</code>
        <small>
          Reason: {config.data?.reason ?? "configuration unavailable"}. No
          viewer dependency is loaded by default.
        </small>
      </section>
    );

  const items = timeline.data?.lanes.flatMap((lane) => lane.items) ?? [];
  const selectedId = searchParams.get("entity");
  const selected = items.find((item) => item.action_id === selectedId) ?? null;
  const agents = timeline.data?.lanes.map((lane) => lane.agent) ?? [];
  const selectedAgent = (selected?.agent ?? agent) || agents[0] || "";
  const url = worldFrameUrl(config.data.url, {
    agent: selectedAgent,
    action: selected?.action_id ?? null,
    target: selected ? actionTarget(selected) : null,
  });

  return (
    <section className="world-view">
      <header className="world-heading">
        <div>
          <p className="eyebrow">External read-only camera</p>
          <h2>Minecraft World View</h2>
        </div>
        <span>
          {config.data.remote
            ? "Remote viewer: explicitly enabled"
            : "Loopback viewer"}
        </span>
      </header>
      <div className="world-controls">
        <label>
          Camera agent
          <select
            value={selectedAgent}
            onChange={(event) => {
              setAgent(event.target.value);
              setSearchParams(new URLSearchParams());
            }}
          >
            {agents.map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </label>
        <label>
          Timeline action
          <select
            value={selected?.action_id ?? ""}
            onChange={(event) => {
              const next = new URLSearchParams(searchParams);
              if (event.target.value) next.set("entity", event.target.value);
              else next.delete("entity");
              setSearchParams(next);
            }}
          >
            <option value="">No selected action</option>
            {items.map((item) => (
              <option key={item.action_id} value={item.action_id}>
                {item.agent}: {item.tool}
              </option>
            ))}
          </select>
        </label>
        <button onClick={() => setFrameKey((value) => value + 1)}>
          Reload viewer
        </button>
      </div>
      {selected && (
        <dl className="world-context">
          <div>
            <dt>Agent</dt>
            <dd>{selected.agent}</dd>
          </div>
          <div>
            <dt>Action</dt>
            <dd>{selected.tool}</dd>
          </div>
          <div>
            <dt>Target context</dt>
            <dd>{actionTarget(selected) ?? "Not recorded"}</dd>
          </div>
        </dl>
      )}
      <iframe
        key={frameKey}
        className="world-frame"
        title="Read-only Minecraft world viewer"
        src={url}
        sandbox="allow-scripts allow-same-origin"
        referrerPolicy="no-referrer"
      />
      <p className="world-warning">
        Read-only context only. The Visualizer sends no bot commands, teleport,
        inventory, or block-edit requests. A blank frame means the external
        viewer is unavailable; runtime execution continues independently.
      </p>
    </section>
  );
}

export function worldFrameUrl(
  base: string,
  context: { agent: string; action: string | null; target: string | null },
): string {
  const url = new URL(base);
  if (context.agent) url.searchParams.set("va_agent", context.agent);
  if (context.action) url.searchParams.set("va_action", context.action);
  if (context.target) url.searchParams.set("va_target", context.target);
  return url.toString();
}
function actionTarget(item: TimelineItem): string | null {
  const direct = item.arguments.target ?? item.arguments.position;
  if (direct !== undefined)
    return typeof direct === "string" ? direct : JSON.stringify(direct);
  const coordinates = ["x", "y", "z"]
    .filter((key) => key in item.arguments)
    .map((key) => `${key}=${String(item.arguments[key])}`);
  return coordinates.length > 0 ? coordinates.join(", ") : null;
}
function Message({ title, detail }: { title: string; detail: string }) {
  return (
    <section className="state-message" role="status">
      <h2>{title}</h2>
      <p>{detail}</p>
    </section>
  );
}
