import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useEffectEvent, useRef, useState } from "react";

import type { RunState, RuntimeGraph } from "./api";

export type LiveConnectionStatus =
  | "live"
  | "reconnecting"
  | "stale"
  | "disconnected"
  | "completed"
  | "failed"
  | "timed_out";
export type LiveStreamEnvelope = {
  version: string;
  type:
    | "snapshot"
    | "heartbeat"
    | "run_completed"
    | "run_unavailable"
    | "error";
  run_id: string;
  revision: number;
  emitted_at: string;
  payload: unknown;
};
export type LiveUpdateState = {
  status: LiveConnectionStatus;
  revision: number;
  lastEventAt: number | null;
  reconnectAttempt: number;
};

const initialState: LiveUpdateState = {
  status: "reconnecting",
  revision: 0,
  lastEventAt: null,
  reconnectAttempt: 0,
};

export function LiveRunUpdates({ runId }: { runId: string }) {
  const queryClient = useQueryClient();
  const [state, setState] = useState(initialState);
  const [retryGeneration, setRetryGeneration] = useState(0);
  const revision = useRef(0);
  const terminal = useRef(false);

  const applyEnvelope = useEffectEvent((envelope: LiveStreamEnvelope) => {
    if (envelope.run_id !== runId || envelope.revision <= revision.current)
      return;
    revision.current = envelope.revision;
    setState((current) => applyLiveEnvelope(current, envelope, Date.now()));
    if (envelope.type === "snapshot" && isRuntimeGraph(envelope.payload)) {
      queryClient.setQueryData(["runtime-graph", runId], envelope.payload);
      void queryClient.invalidateQueries({ queryKey: ["timeline", runId] });
    }
    if (envelope.type === "run_completed") {
      terminal.current = true;
      void queryClient.invalidateQueries({ queryKey: ["run", runId] });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({
        queryKey: ["runtime-graph", runId],
      });
      void queryClient.invalidateQueries({ queryKey: ["timeline", runId] });
    }
  });

  useEffect(() => {
    terminal.current = false;
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let attempts = 0;

    function connect() {
      if (!active || terminal.current) return;
      setState((current) => ({
        ...current,
        status: attempts === 0 ? "reconnecting" : current.status,
        reconnectAttempt: attempts,
      }));
      socket = new WebSocket(liveStreamUrl(runId));
      socket.onmessage = (message) => {
        const envelope = parseLiveEnvelope(message.data);
        if (envelope) applyEnvelope(envelope);
      };
      socket.onopen = () => {
        attempts = 0;
      };
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        if (!active || terminal.current) return;
        attempts += 1;
        setState((current) => ({
          ...current,
          status:
            current.lastEventAt === null ? "reconnecting" : "disconnected",
          reconnectAttempt: attempts,
        }));
        reconnectTimer = window.setTimeout(connect, reconnectDelay(attempts));
      };
    }
    connect();
    return () => {
      active = false;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [runId, retryGeneration]);

  useEffect(() => {
    const timer = window.setInterval(
      () =>
        setState((current) => {
          if (
            current.status !== "live" ||
            current.lastEventAt === null ||
            Date.now() - current.lastEventAt < 30_000
          )
            return current;
          return { ...current, status: "stale" };
        }),
      1_000,
    );
    return () => window.clearInterval(timer);
  }, []);

  const retryable = ["reconnecting", "stale", "disconnected"].includes(
    state.status,
  );
  return (
    <div
      className={`live-status live-status--${state.status}`}
      role="status"
      aria-live="polite"
    >
      <span aria-hidden="true" />
      <strong>{liveStatusLabel(state.status)}</strong>
      <small>
        {state.revision > 0
          ? `Revision ${state.revision}`
          : state.reconnectAttempt > 0
            ? `Retry ${state.reconnectAttempt}`
            : "Waiting for snapshot"}
      </small>
      {retryable && (
        <button onClick={() => setRetryGeneration((value) => value + 1)}>
          Retry now
        </button>
      )}
    </div>
  );
}

export function applyLiveEnvelope(
  state: LiveUpdateState,
  envelope: LiveStreamEnvelope,
  receivedAt: number,
): LiveUpdateState {
  if (envelope.revision <= state.revision) return state;
  let status: LiveConnectionStatus =
    envelope.type === "error"
      ? "stale"
      : envelope.type === "run_unavailable"
        ? "disconnected"
        : "live";
  if (envelope.type === "run_completed")
    status = terminalStatus(envelope.payload);
  return {
    status,
    revision: envelope.revision,
    lastEventAt: receivedAt,
    reconnectAttempt: 0,
  };
}

export function liveStreamUrl(
  runId: string,
  location: Pick<Location, "protocol" | "host"> = window.location,
): string {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${location.host}/api/v1/runs/${encodeURIComponent(runId)}/stream`;
}

export function reconnectDelay(attempt: number): number {
  return Math.min(1_000 * 2 ** Math.max(0, attempt - 1), 30_000);
}
function parseLiveEnvelope(value: unknown): LiveStreamEnvelope | null {
  try {
    const parsed = JSON.parse(String(value)) as Partial<LiveStreamEnvelope>;
    return parsed.version === "1.0" &&
      typeof parsed.type === "string" &&
      typeof parsed.run_id === "string" &&
      Number.isInteger(parsed.revision)
      ? (parsed as LiveStreamEnvelope)
      : null;
  } catch {
    return null;
  }
}
function terminalStatus(payload: unknown): LiveConnectionStatus {
  const state =
    typeof payload === "object" && payload !== null && "state" in payload
      ? (payload as { state?: RunState }).state
      : undefined;
  return state === "failed" || state === "timed_out" || state === "completed"
    ? state
    : "completed";
}
function isRuntimeGraph(value: unknown): value is RuntimeGraph {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as Partial<RuntimeGraph>).authority === "canonical_runtime_state" &&
    Array.isArray((value as Partial<RuntimeGraph>).nodes)
  );
}
function liveStatusLabel(status: LiveConnectionStatus): string {
  return status === "timed_out"
    ? "Timed out"
    : `${status.charAt(0).toUpperCase()}${status.slice(1)}`;
}
