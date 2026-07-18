import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { fetchReplayEvents, fetchReplayState } from "./api";

export function ReplayView({ runId }: { runId: string }) {
  const events = useQuery({
    queryKey: ["replay-events", runId],
    queryFn: () => fetchReplayEvents(runId),
  });
  const [selectedSeq, setSelectedSeq] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const maxSeq = events.data?.max_seq ?? 0;
  const seq = clampReplaySeq(selectedSeq ?? maxSeq, maxSeq);
  const replay = useQuery({
    queryKey: ["replay-state", runId, seq],
    queryFn: () => fetchReplayState(runId, seq),
    enabled: events.isSuccess,
  });

  useEffect(() => {
    if (!playing || maxSeq === 0) return;
    const timer = window.setInterval(
      () =>
        setSelectedSeq((current) => {
          const next = clampReplaySeq((current ?? 0) + 1, maxSeq);
          if (next >= maxSeq) setPlaying(false);
          return next;
        }),
      1_000 / speed,
    );
    return () => window.clearInterval(timer);
  }, [playing, speed, maxSeq]);

  if (events.isPending)
    return (
      <Message
        title="Loading Replay"
        detail="Reading the normalized event journal."
      />
    );
  if (events.isError)
    return (
      <Message
        title="Replay unavailable"
        detail="This run has no readable normalized event journal."
      />
    );
  if (events.data.events.length === 0)
    return (
      <Message
        title="Replay unavailable"
        detail="The event journal is empty."
      />
    );

  return (
    <section className="replay-view" aria-label="Recorded event replay">
      <header className="replay-heading">
        <div>
          <p className="eyebrow">Recorded events, not live state</p>
          <h2>Run Replay</h2>
        </div>
        <strong>
          Seq {seq} / {maxSeq}
        </strong>
      </header>
      <div className="replay-controls" aria-label="Replay controls">
        <button onClick={() => setPlaying((value) => !value)}>
          {playing ? "Pause" : "Play"}
        </button>
        <button
          onClick={() => {
            setPlaying(false);
            setSelectedSeq((value) =>
              clampReplaySeq((value ?? maxSeq) - 1, maxSeq),
            );
          }}
        >
          Step backward
        </button>
        <button
          onClick={() =>
            setSelectedSeq((value) => clampReplaySeq((value ?? 0) + 1, maxSeq))
          }
        >
          Step forward
        </button>
        <label>
          Jump
          <input
            type="number"
            min="0"
            max={maxSeq}
            value={seq}
            onChange={(event) => {
              setPlaying(false);
              setSelectedSeq(
                clampReplaySeq(Number(event.target.value), maxSeq),
              );
            }}
          />
        </label>
        <label>
          Speed
          <select
            value={speed}
            onChange={(event) => setSpeed(Number(event.target.value))}
          >
            <option value="0.5">0.5×</option>
            <option value="1">1×</option>
            <option value="2">2×</option>
            <option value="4">4×</option>
          </select>
        </label>
      </div>
      <input
        className="replay-scrubber"
        aria-label="Replay sequence"
        type="range"
        min="0"
        max={maxSeq}
        value={seq}
        onChange={(event) => {
          setPlaying(false);
          setSelectedSeq(Number(event.target.value));
        }}
      />
      {replay.isPending ? (
        <Message
          title="Reconstructing state"
          detail={`Reducing events through seq ${seq}.`}
        />
      ) : replay.isError ? (
        <Message
          title="Replay state unavailable"
          detail={replay.error.message}
        />
      ) : (
        replay.data && (
          <div className="replay-grid">
            <section className="replay-panel">
              <h3>Task state</h3>
              {replay.data.graph.nodes.length === 0 ? (
                <p>No task snapshot yet.</p>
              ) : (
                replay.data.graph.nodes.map((node, index) => (
                  <article key={String(node.node_id ?? index)}>
                    <strong>{String(node.node_id ?? "Unknown task")}</strong>
                    <span>{nodeStatus(node)}</span>
                  </article>
                ))
              )}
            </section>
            <section className="replay-panel">
              <h3>Assignments</h3>
              {Object.keys(replay.data.assignments).length === 0 ? (
                <p>No assignments yet.</p>
              ) : (
                Object.entries(replay.data.assignments).map(
                  ([task, agents]) => (
                    <article key={task}>
                      <strong>{task}</strong>
                      <span>{agents.join(", ") || "None"}</span>
                    </article>
                  ),
                )
              )}
            </section>
            <section className="replay-panel">
              <h3>Accumulated actions</h3>
              <p className="replay-caveat">
                action_recorded is a log record, not an observed action
                start/completion hook.
              </p>
              {replay.data.timeline.map((event) => (
                <article key={event.event_id}>
                  <strong>{event.entity_id}</strong>
                  <span>{String(event.payload.tool ?? "unknown tool")}</span>
                </article>
              ))}
            </section>
            <section className="replay-panel replay-payload">
              <h3>Current event</h3>
              <pre>{JSON.stringify(replay.data.current_event, null, 2)}</pre>
            </section>
            {replay.data.warnings.length > 0 && (
              <section className="replay-panel replay-warnings">
                <h3>Warnings</h3>
                <ul>
                  {replay.data.warnings.map((warning, index) => (
                    <li key={`${warning.code}-${index}`}>
                      <strong>{warning.code}</strong>: {warning.message}
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        )
      )}
      <section className="replay-event-list">
        <h3>
          Event journal <span>{events.data.total}</span>
        </h3>
        {events.data.events.map((event) => (
          <button
            key={event.event_id}
            aria-pressed={event.seq === seq}
            onClick={() => {
              setPlaying(false);
              setSelectedSeq(event.seq);
            }}
          >
            <strong>{event.seq}</strong>
            <span>{event.event_type}</span>
            <small>{event.entity_id ?? "run"}</small>
          </button>
        ))}
      </section>
    </section>
  );
}

export function clampReplaySeq(value: number, max: number): number {
  return Number.isFinite(value)
    ? Math.max(0, Math.min(Math.round(value), max))
    : 0;
}
function nodeStatus(node: Record<string, unknown>): string {
  const lifecycle =
    typeof node.lifecycle === "object" && node.lifecycle !== null
      ? (node.lifecycle as Record<string, unknown>)
      : {};
  return String(lifecycle.status ?? "unknown");
}
function Message({ title, detail }: { title: string; detail: string }) {
  return (
    <section className="state-message" role="status">
      <h2>{title}</h2>
      <p>{detail}</p>
    </section>
  );
}
