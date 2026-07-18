import { useQuery } from "@tanstack/react-query";
import { useState, type KeyboardEvent } from "react";
import { useSearchParams } from "react-router-dom";

import { fetchTimeline, type Timeline, type TimelineItem } from "./api";
import { EntityInspector, type InspectorEntity } from "./EntityInspector";

export function TimelineView({ runId }: { runId: string }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const timeline = useQuery({
    queryKey: ["timeline", runId],
    queryFn: () => fetchTimeline(runId),
  });
  const [zoom, setZoom] = useState(1);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const selectedActionId = searchParams.get("entity");

  function selectEntity(id: string | null) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (id) next.set("entity", id);
      else next.delete("entity");
      return next;
    });
  }

  if (timeline.isPending)
    return (
      <Message
        title="Loading Timeline"
        detail="Reading recorded agent actions."
      />
    );
  if (timeline.isError)
    return (
      <Message title="Timeline unavailable" detail={timeline.error.message} />
    );

  const allItems = orderedTimelineItems(timeline.data);
  const untimed = allItems.filter((item) => item.timing === "untimed");
  if (allItems.length === 0)
    return (
      <Message
        title="Empty Timeline"
        detail="The action log contains no recorded actions."
      />
    );

  function toggleLane(agent: string) {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(agent)) next.delete(agent);
      else next.add(agent);
      return next;
    });
  }

  return (
    <section className="timeline-view" aria-label="Agent action timeline">
      <header className="timeline-heading">
        <div>
          <p className="eyebrow">Recorded action order</p>
          <h2>Agent Timeline</h2>
        </div>
        <label>
          Zoom {zoom.toFixed(1)}×
          <input
            type="range"
            min="1"
            max="3"
            step="0.25"
            value={zoom}
            onChange={(event) => setZoom(Number(event.target.value))}
          />
        </label>
      </header>
      {timeline.data.bounds ? (
        <div className="time-ruler-summary">
          <span>{timeline.data.bounds.start_time}</span>
          <strong>
            {timeline.data.bounds.timezone_kind === "naive_local"
              ? "Timezone unspecified"
              : "Offset-aware timestamps"}
          </strong>
          <span>{timeline.data.bounds.end_time}</span>
        </div>
      ) : (
        <p className="timing-notice">
          No compatible absolute bounds. Duration-only and untimed records
          remain visible.
        </p>
      )}

      <div className="timeline-scroll">
        <div
          className="timeline-chart"
          style={{ width: `${Math.max(100, zoom * 100)}%` }}
        >
          {timeline.data.lanes.map((lane) => {
            const exact = lane.items.filter((item) => item.timing === "exact");
            const durationOnly = lane.items.filter(
              (item) => item.timing === "duration_only",
            );
            const isCollapsed = collapsed.has(lane.agent);
            return (
              <section
                className="timeline-lane"
                key={lane.agent}
                aria-label={`${lane.agent} action lane`}
              >
                <button
                  className="lane-label"
                  aria-expanded={!isCollapsed}
                  onClick={() => toggleLane(lane.agent)}
                >
                  <span>{isCollapsed ? "+" : "−"}</span>
                  {lane.agent}
                  <small>{lane.items.length} actions</small>
                </button>
                {!isCollapsed && (
                  <div className="lane-content">
                    <div
                      className="exact-track"
                      aria-label={`${lane.agent} exact-time actions`}
                    >
                      {exact.map((item) => {
                        const geometry = timelineGeometry(item, timeline.data);
                        return (
                          geometry && (
                            <ActionButton
                              key={item.action_id}
                              item={item}
                              selected={selectedActionId === item.action_id}
                              style={{
                                left: `${geometry.left}%`,
                                width: `${Math.max(geometry.width, 1.5)}%`,
                              }}
                              onSelect={selectEntity}
                            />
                          )
                        );
                      })}
                    </div>
                    {durationOnly.length > 0 && (
                      <div
                        className="duration-row"
                        aria-label={`${lane.agent} duration-only actions`}
                      >
                        {durationOnly.map((item) => (
                          <ActionButton
                            key={item.action_id}
                            item={item}
                            selected={selectedActionId === item.action_id}
                            style={{ width: durationWidth(item, durationOnly) }}
                            onSelect={selectEntity}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      </div>

      <section className="untimed-section">
        <h3>
          Untimed actions <span>{untimed.length}</span>
        </h3>
        {untimed.length === 0 ? (
          <p>No untimed actions.</p>
        ) : (
          <div>
            {untimed.map((item) => (
              <ActionButton
                key={item.action_id}
                item={item}
                selected={selectedActionId === item.action_id}
                onSelect={selectEntity}
              />
            ))}
          </div>
        )}
      </section>
      <div className="timeline-legend" aria-label="Timeline legend">
        <span>
          <i className="timing-swatch timing-swatch--exact" />
          Exact
        </span>
        <span>
          <i className="timing-swatch timing-swatch--duration_only" />
          Duration only
        </span>
        <span>
          <i className="timing-swatch timing-swatch--untimed" />
          Untimed
        </span>
      </div>
      {selectedActionId && (
        <p className="selection-note" aria-live="polite">
          Selected action: <code>{selectedActionId}</code>
        </p>
      )}
      <EntityInspector
        runId={runId}
        selectionId={selectedActionId}
        entity={timelineInspectorEntity(timeline.data, selectedActionId)}
        onClose={() => selectEntity(null)}
      />
    </section>
  );
}

export function timelineInspectorEntity(
  timeline: Timeline,
  id: string | null,
): InspectorEntity | null {
  const item = id
    ? orderedTimelineItems(timeline).find(
        (candidate) => candidate.action_id === id,
      )
    : null;
  if (!item) return null;
  return {
    id: item.action_id,
    type: "timeline_action",
    status: item.status,
    content: {
      tool: item.tool,
      arguments: item.arguments,
      timing: item.timing,
      start_time: item.start_time,
      end_time: item.end_time,
      duration_seconds: item.duration_seconds,
    },
    provenance: { agent: item.agent, record_index: item.record_index },
    raw: item,
    related: [
      ...item.related_task_ids.map((relatedId) => ({
        id: relatedId,
        label: "Related task",
        view: "runtime" as const,
      })),
      ...item.observation_ids.map((relatedId) => ({
        id: relatedId,
        label: "Observation",
        view: "analysis" as const,
      })),
      ...item.claim_ids.map((relatedId) => ({
        id: relatedId,
        label: "Claim",
        view: "analysis" as const,
      })),
      { id: item.action_id, label: "World context", view: "world" as const },
    ],
  };
}

function ActionButton({
  item,
  selected,
  style,
  onSelect,
}: {
  item: TimelineItem;
  selected: boolean;
  style?: React.CSSProperties;
  onSelect: (id: string) => void;
}) {
  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const buttons = [
      ...document.querySelectorAll<HTMLButtonElement>("[data-timeline-action]"),
    ];
    const index = buttons.indexOf(event.currentTarget);
    const next =
      buttons[
        (index + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) %
          buttons.length
      ];
    if (next) {
      event.preventDefault();
      next.focus();
      next.click();
    }
  }
  return (
    <button
      data-timeline-action
      className={`action-bar action-bar--${item.timing} action-bar--${item.status}${selected ? " action-bar--selected" : ""}`}
      style={style}
      onClick={() => onSelect(item.action_id)}
      onKeyDown={handleKeyDown}
      aria-label={actionLabel(item)}
    >
      <strong>{item.tool}</strong>
      <span>{item.status}</span>
      {item.duration_seconds !== null && (
        <small>{item.duration_seconds.toFixed(2)}s</small>
      )}
      <em>
        {item.related_task_ids.length > 0
          ? `${item.related_task_ids.length} task`
          : "No task"}{" "}
        · {item.observation_ids.length > 0 ? "observation" : "no observation"} ·{" "}
        {item.claim_ids.length > 0 ? "claim" : "no claim"}
      </em>
    </button>
  );
}

export function timelineGeometry(
  item: TimelineItem,
  timeline: Timeline,
): { left: number; width: number } | null {
  if (
    item.timing !== "exact" ||
    !item.start_time ||
    !item.end_time ||
    !timeline.bounds
  )
    return null;
  const start = parseTimestamp(item.start_time);
  const end = parseTimestamp(item.end_time);
  const boundStart = parseTimestamp(timeline.bounds.start_time);
  const boundEnd = parseTimestamp(timeline.bounds.end_time);
  if (
    [start, end, boundStart, boundEnd].some((value) => value === null) ||
    boundEnd === boundStart ||
    end! < start!
  )
    return null;
  return {
    left: ((start! - boundStart!) / (boundEnd! - boundStart!)) * 100,
    width: ((end! - start!) / (boundEnd! - boundStart!)) * 100,
  };
}

export function parseTimestamp(value: string): number | null {
  const normalized = value.includes("T") ? value : value.replace(" ", "T");
  const parsed = Date.parse(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}
export function orderedTimelineItems(timeline: Timeline): TimelineItem[] {
  return timeline.lanes.flatMap((lane) => lane.items);
}
export function durationWidth(
  item: TimelineItem,
  peers: TimelineItem[],
): string {
  const max = Math.max(...peers.map((peer) => peer.duration_seconds ?? 0), 0);
  return `${max > 0 ? Math.max(((item.duration_seconds ?? 0) / max) * 100, 8) : 8}%`;
}
function actionLabel(item: TimelineItem) {
  return `${item.agent} ${item.tool}; ${item.timing}; status ${item.status}; record ${item.record_index}`;
}
function Message({ title, detail }: { title: string; detail: string }) {
  return (
    <section className="state-message" role="status">
      <h2>{title}</h2>
      <p>{detail}</p>
    </section>
  );
}
