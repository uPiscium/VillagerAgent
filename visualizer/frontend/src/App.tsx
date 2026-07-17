import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  BrowserRouter,
  NavLink,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useParams,
} from "react-router-dom";

import {
  fetchHealth,
  fetchRun,
  fetchRuns,
  runPath,
  type RunManifest,
  type RunSection,
} from "./api";

const sections: Array<{ id: RunSection; label: string; artifact?: string }> = [
  { id: "overview", label: "Overview" },
  { id: "runtime", label: "Runtime DAG", artifact: "runtime_graph" },
  { id: "analysis", label: "Analysis DAG", artifact: "analysis_graph" },
  { id: "timeline", label: "Timeline", artifact: "action_log" },
];

export default function App() {
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 10_000 } },
  }));

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Workspace />}>
            <Route index element={<Navigate to="/runs" replace />} />
            <Route path="runs" element={<RunLanding />} />
            <Route path="runs/:runId/overview" element={<RunPage section="overview" />} />
            <Route path="runs/:runId/runtime" element={<RunPage section="runtime" />} />
            <Route path="runs/:runId/analysis" element={<RunPage section="analysis" />} />
            <Route path="runs/:runId/timeline" element={<RunPage section="timeline" />} />
            <Route path="*" element={<Navigate to="/runs" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

function Workspace() {
  const health = useQuery({ queryKey: ["health"], queryFn: fetchHealth });
  const runs = useQuery({ queryKey: ["runs"], queryFn: fetchRuns });
  const location = useLocation();

  return (
    <div className="workspace">
      <header className="topbar">
        <NavLink className="brand" to="/runs" aria-label="VillagerAgent Visualizer runs">
          <span className="brand-mark">VA</span>
          <span>Experiment Visualizer</span>
        </NavLink>
        <div className={`connection connection--${health.isSuccess ? "online" : health.isError ? "offline" : "pending"}`} aria-live="polite">
          <span aria-hidden="true" />
          {health.isSuccess ? `API ${health.data.api_version}` : health.isError ? "Backend unavailable" : "Connecting"}
        </div>
      </header>

      <aside className="sidebar" aria-label="Experiment runs">
        <div className="sidebar-heading">
          <p>Recorded runs</p>
          {runs.data && <span>{runs.data.length}</span>}
        </div>
        {runs.isPending && <StateMessage title="Loading runs" detail="Reading experiment manifests." compact />}
        {runs.isError && <StateMessage title="Runs unavailable" detail={runs.error.message} compact />}
        {runs.data?.length === 0 && <StateMessage title="No runs found" detail="Point the backend at a result directory." compact />}
        {runs.data && runs.data.length > 0 && (
          <nav className="run-list">
            {runs.data.map((run) => (
              <NavLink
                className={({ isActive }) => `run-link${isActive || location.pathname.startsWith(`/runs/${encodeURIComponent(run.run_id)}/`) ? " run-link--active" : ""}`}
                key={run.run_id}
                to={runPath(run.run_id, "overview")}
              >
                <span className={`state-dot state-dot--${run.state}`} aria-hidden="true" />
                <span>
                  <strong>{run.name}</strong>
                  <small>{stateLabel(run.state)} · {run.mode || "unknown mode"}</small>
                </span>
              </NavLink>
            ))}
          </nav>
        )}
      </aside>

      <main className="content" id="main-content">
        <Outlet />
      </main>
    </div>
  );
}

function RunLanding() {
  return (
    <section className="landing">
      <p className="eyebrow">Read-only experiment workspace</p>
      <h1>Choose a run to inspect.</h1>
      <p>Completed, failed, timed-out, live, and partial records remain visible without changing runtime state.</p>
    </section>
  );
}

function RunPage({ section }: { section: RunSection }) {
  const { runId = "" } = useParams();
  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => fetchRun(runId),
    enabled: Boolean(runId),
  });

  if (run.isPending) {
    return <StateMessage title="Loading run" detail="Reading the selected manifest." />;
  }
  if (run.isError) {
    return <StateMessage title="Run unavailable" detail={run.error.message} />;
  }

  const available = sectionAvailable(run.data, section);
  return (
    <article className="run-page">
      <header className="run-header">
        <div>
          <p className="eyebrow">{run.data.mode || "Unknown mode"} · {run.data.source.task_state || "Unknown task source"}</p>
          <h1>{run.data.name}</h1>
        </div>
        <span className={`state-pill state-pill--${run.data.state}`}>{stateLabel(run.data.state)}</span>
      </header>

      <nav className="tabs" aria-label="Run views">
        {sections.map((item) => sectionAvailable(run.data, item.id) ? (
          <NavLink key={item.id} to={runPath(run.data.run_id, item.id)}>{item.label}</NavLink>
        ) : (
          <span key={item.id} aria-disabled="true" title={`${item.label} artifact unavailable`}>{item.label}</span>
        ))}
      </nav>

      {!available ? (
        <StateMessage title={`${sections.find((item) => item.id === section)?.label} unavailable`} detail="This run does not contain the required artifact." />
      ) : section === "overview" ? (
        <Overview run={run.data} />
      ) : (
        <section className="view-placeholder">
          <p className="eyebrow">{sections.find((item) => item.id === section)?.label}</p>
          <h2>Artifact available</h2>
          <p>The dedicated read-only view will render this data in its implementation issue.</p>
        </section>
      )}
    </article>
  );
}

function Overview({ run }: { run: RunManifest }) {
  const rows = [
    ["Task", run.task.name || "N/A"],
    ["Task type", run.task.task_type || "N/A"],
    ["Policy", run.policy || "N/A"],
    ["Progress", formatValue(run.progress)],
    ["Snapshot", run.source.snapshot || "N/A"],
    ["Source of truth", run.source.source_of_truth || "N/A"],
  ];
  return (
    <div className="overview-grid">
      <section className="metadata-card">
        <h2>Run metadata</h2>
        <dl>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
      </section>
      <section className="metadata-card">
        <h2>Artifact availability</h2>
        <ul className="artifact-list">
          {Object.entries(run.artifacts).map(([name, present]) => (
            <li key={name}><span>{name.replaceAll("_", " ")}</span><strong>{present ? "Available" : "Missing"}</strong></li>
          ))}
        </ul>
      </section>
      {run.error && <section className="error-card"><h2>Run error</h2><p>{run.error}</p></section>}
    </div>
  );
}

function StateMessage({ title, detail, compact = false }: { title: string; detail: string; compact?: boolean }) {
  return <section className={`state-message${compact ? " state-message--compact" : ""}`} role="status"><h2>{title}</h2><p>{detail}</p></section>;
}

function sectionAvailable(run: RunManifest, section: RunSection): boolean {
  if (section === "overview") return true;
  if (section === "runtime") return Boolean(run.artifacts.runtime_graph || run.artifacts.runtime_checkpoint);
  if (section === "analysis") return Boolean(run.artifacts.analysis_graph);
  return Boolean(run.artifacts.action_log);
}

function stateLabel(state: RunManifest["state"]): string {
  return state === "timed_out" ? "Timed out" : `${state.charAt(0).toUpperCase()}${state.slice(1)}`;
}

function formatValue(value: RunManifest["progress"]): string {
  return value === null || value === "" ? "N/A" : String(value);
}
