export type RunState =
  | "live"
  | "completed"
  | "failed"
  | "timed_out"
  | "partial"
  | "invalid";

export type RunManifest = {
  run_id: string;
  name: string;
  state: RunState;
  started_at: string | null;
  mode: string;
  task: {
    name: string;
    task_type: string;
    index: string | number | boolean | null;
  };
  policy: string;
  source: {
    producer: string;
    task_state: string;
    snapshot: string;
    source_of_truth: string;
  };
  progress: string | number | boolean | null;
  error: string | null;
  artifacts: Record<string, boolean>;
  warnings: Array<{ code: string; message: string; artifact: string | null }>;
};

export type HealthResponse = {
  status: "ok";
  service: string;
  api_version: string;
};

export type RuntimeGraph = {
  authority: "canonical_runtime_state";
  schema_version: string | null;
  snapshot_source: string;
  source_of_truth: string;
  summary: Record<string, unknown>;
  nodes: RuntimeGraphNode[];
  edges: RuntimeGraphEdge[];
  mutation_history: Array<Record<string, unknown>>;
  warnings: Array<{ code: string; message: string; artifact: string | null }>;
};

export type RuntimeGraphNode = {
  node_id: string;
  node_type: string;
  content: Record<string, unknown>;
  lifecycle: Record<string, unknown>;
  derived: Record<string, unknown>;
  provenance: Record<string, unknown>;
  extra: Record<string, unknown>;
};

export type RuntimeGraphEdge = {
  edge_id: string;
  source_id: string;
  target_id: string;
  edge_type: string;
  metadata: Record<string, unknown>;
  extra: Record<string, unknown>;
};

export type AnalysisGraph = {
  authority: "posthoc_analysis_projection";
  schema_version: string | null;
  task_state_source: string;
  summary: Record<string, unknown>;
  schema: Record<string, unknown>;
  mapping: Record<string, unknown>;
  nodes: AnalysisGraphNode[];
  edges: AnalysisGraphEdge[];
  applied_filters: Record<string, string[]>;
  warnings: Array<{ code: string; message: string; artifact: string | null }>;
};

export type AnalysisGraphNode = {
  node_id: string;
  node_type: string;
  content: Record<string, unknown>;
  provenance: Record<string, unknown>;
  confidence: number | null;
  runtime_task_id: string | null;
  extra: Record<string, unknown>;
};

export type AnalysisGraphEdge = {
  edge_id: string;
  source_id: string;
  target_id: string;
  edge_type: string;
  metadata: Record<string, unknown>;
  extra: Record<string, unknown>;
};

export type Timeline = {
  lanes: TimelineLane[];
  bounds: {
    start_time: string;
    end_time: string;
    timezone_kind: string;
  } | null;
  warnings: Array<{ code: string; message: string; artifact: string | null }>;
};

export type TimelineLane = { agent: string; items: TimelineItem[] };

export type TimelineItem = {
  action_id: string;
  agent: string;
  record_index: number;
  tool: string;
  status: "success" | "failure" | "unknown";
  timing: "exact" | "duration_only" | "untimed";
  start_time: string | null;
  end_time: string | null;
  duration_seconds: number | null;
  arguments: Record<string, unknown>;
  related_task_ids: string[];
  observation_ids: string[];
  claim_ids: string[];
};

export type ReplayEvent = {
  seq: number;
  event_id: string;
  event_type: string;
  occurred_at: string | null;
  entity_id: string | null;
  source: string;
  payload: Record<string, unknown>;
  provenance?: Record<string, unknown>;
};

export type ReplayEvents = {
  events: ReplayEvent[];
  total: number;
  max_seq: number;
  next_seq: number | null;
  warnings: Array<{ code: string; message: string }>;
};
export type ReplayState = {
  seq: number;
  max_seq: number;
  authority: "recorded_event_replay";
  graph: {
    nodes: Array<Record<string, unknown>>;
    edges: Array<Record<string, unknown>>;
  };
  assignments: Record<string, string[]>;
  timeline: ReplayEvent[];
  current_event: ReplayEvent | null;
  warnings: Array<{ code: string; message: string }>;
};

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function fetchHealth(): Promise<HealthResponse> {
  return fetchJson<HealthResponse>("/api/v1/health");
}

export async function fetchRuns(): Promise<RunManifest[]> {
  const response = await fetchJson<{ runs: RunManifest[] }>("/api/v1/runs");
  if (!Array.isArray(response.runs)) {
    throw new ApiError(500, "The run list response is invalid.");
  }
  return response.runs;
}

export async function fetchRun(runId: string): Promise<RunManifest> {
  return fetchJson<RunManifest>(`/api/v1/runs/${encodeURIComponent(runId)}`);
}

export async function fetchRuntimeGraph(runId: string): Promise<RuntimeGraph> {
  return fetchJson<RuntimeGraph>(
    `/api/v1/runs/${encodeURIComponent(runId)}/runtime-graph`,
  );
}

export async function fetchAnalysisGraph(
  runId: string,
): Promise<AnalysisGraph> {
  return fetchJson<AnalysisGraph>(
    `/api/v1/runs/${encodeURIComponent(runId)}/analysis-graph`,
  );
}

export async function fetchTimeline(runId: string): Promise<Timeline> {
  return fetchJson<Timeline>(
    `/api/v1/runs/${encodeURIComponent(runId)}/timeline`,
  );
}

export async function fetchReplayEvents(runId: string): Promise<ReplayEvents> {
  return fetchJson<ReplayEvents>(
    `/api/v1/runs/${encodeURIComponent(runId)}/events?limit=1000`,
  );
}

export async function fetchReplayState(
  runId: string,
  seq: number,
): Promise<ReplayState> {
  return fetchJson<ReplayState>(
    `/api/v1/runs/${encodeURIComponent(runId)}/replay-state?seq=${seq}`,
  );
}

export function runPath(runId: string, section: RunSection): string {
  return `/runs/${encodeURIComponent(runId)}/${section}`;
}

export type RunSection =
  | "overview"
  | "runtime"
  | "analysis"
  | "timeline"
  | "replay";

async function fetchJson<T>(url: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url);
  } catch {
    throw new ApiError(0, "The visualizer backend is unavailable.");
  }
  if (!response.ok) {
    throw new ApiError(
      response.status,
      `The visualizer API returned ${response.status}.`,
    );
  }
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(
      response.status,
      "The visualizer API returned invalid JSON.",
    );
  }
}
