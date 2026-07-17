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
