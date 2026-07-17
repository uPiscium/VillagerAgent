export type RunState = "live" | "completed" | "failed" | "timed_out" | "partial" | "invalid";

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

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
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
  return fetchJson<RuntimeGraph>(`/api/v1/runs/${encodeURIComponent(runId)}/runtime-graph`);
}

export function runPath(runId: string, section: RunSection): string {
  return `/runs/${encodeURIComponent(runId)}/${section}`;
}

export type RunSection = "overview" | "runtime" | "analysis" | "timeline";

async function fetchJson<T>(url: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url);
  } catch {
    throw new ApiError(0, "The visualizer backend is unavailable.");
  }
  if (!response.ok) {
    throw new ApiError(response.status, `The visualizer API returned ${response.status}.`);
  }
  try {
    return await response.json() as T;
  } catch {
    throw new ApiError(response.status, "The visualizer API returned invalid JSON.");
  }
}
