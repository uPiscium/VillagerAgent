import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import {
  fetchComparison,
  fetchRuns,
  type Comparison,
  type ComparisonRun,
} from "./api";

const columns: Array<{ key: keyof ComparisonRun; label: string }> = [
  { key: "state", label: "Result" },
  { key: "mode", label: "Mode" },
  { key: "policy", label: "Policy" },
  { key: "task_state_source", label: "Task source" },
  { key: "snapshot_source", label: "Snapshot" },
  { key: "progress", label: "Progress" },
  { key: "score", label: "Score" },
  { key: "task_count", label: "Tasks" },
  { key: "completed_task_count", label: "Completed tasks" },
  { key: "failed_task_count", label: "Failed tasks" },
  { key: "action_count", label: "Actions" },
  { key: "failed_action_count", label: "Failed actions" },
  { key: "duration_seconds", label: "Duration (s)" },
  { key: "recommendation_adopted_count", label: "Recommendation adopted" },
  { key: "runtime_selected_task_ids", label: "Runtime selection" },
  { key: "posthoc_ranked_task_order", label: "Post-hoc ranking" },
  { key: "agent_action_counts", label: "Agent actions" },
  { key: "agent_idle_seconds", label: "Event-derived idle (s)" },
];

export function CompareView() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: fetchRuns });
  const [searchParams, setSearchParams] = useSearchParams();
  const selected = searchParams.getAll("run");
  const comparison = useQuery({
    queryKey: ["comparison", selected],
    queryFn: () => fetchComparison(selected),
    enabled: selected.length >= 2,
  });
  function toggle(runId: string) {
    const next = selected.includes(runId)
      ? selected.filter((id) => id !== runId)
      : [...selected, runId];
    const params = new URLSearchParams();
    next.forEach((id) => params.append("run", id));
    setSearchParams(params);
  }
  return (
    <section className="compare-view">
      <header className="compare-heading">
        <div>
          <p className="eyebrow">Descriptive run analysis</p>
          <h1>Compare Runs</h1>
        </div>
        <strong>{selected.length} selected</strong>
      </header>
      <section className="compare-picker" aria-label="Runs to compare">
        <h2>Select at least two runs</h2>
        {runs.data?.map((run) => (
          <label key={run.run_id}>
            <input
              type="checkbox"
              checked={selected.includes(run.run_id)}
              onChange={() => toggle(run.run_id)}
            />
            <span>
              <strong>{run.name}</strong>
              <small>
                {run.state} · {run.policy || "unknown policy"}
              </small>
            </span>
          </label>
        ))}
      </section>
      {selected.length < 2 ? (
        <Message
          title="Choose more runs"
          detail="Comparison requires at least two recorded runs."
        />
      ) : comparison.isPending ? (
        <Message
          title="Loading comparison"
          detail="Reading public summary and metrics artifacts."
        />
      ) : comparison.isError ? (
        <Message
          title="Comparison unavailable"
          detail={comparison.error.message}
        />
      ) : (
        comparison.data && <ComparisonDashboard comparison={comparison.data} />
      )}
    </section>
  );
}

function ComparisonDashboard({ comparison }: { comparison: Comparison }) {
  const json = comparisonJson(comparison);
  const csv = comparisonCsv(comparison);
  return (
    <>
      <div className="compare-warnings">
        {comparison.warnings.map((warning) => (
          <p key={warning.code}>
            <strong>{warning.code}</strong> {warning.message}
          </p>
        ))}
        <p>
          No statistical significance is inferred from this descriptive view.
        </p>
      </div>
      <div className="compare-exports">
        <a
          download="villageragent-comparison.json"
          href={`data:application/json;charset=utf-8,${encodeURIComponent(json)}`}
        >
          Export JSON
        </a>
        <a
          download="villageragent-comparison.csv"
          href={`data:text/csv;charset=utf-8,${encodeURIComponent(csv)}`}
        >
          Export CSV
        </a>
      </div>
      <div className="comparison-table-wrap">
        <table className="comparison-table">
          <thead>
            <tr>
              <th>Metric</th>
              {comparison.runs.map((run) => (
                <th key={run.run_id}>
                  {run.name}
                  <small>{run.task_name ?? "Unknown task"}</small>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {columns.map((column) => (
              <tr key={column.key}>
                <th>{column.label}</th>
                {comparison.runs.map((run) => (
                  <td key={run.run_id}>{displayValue(run[column.key])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <section className="comparison-charts">
        <h2>Recorded values</h2>
        {(["progress", "action_count", "failed_action_count"] as const).map(
          (metric) => (
            <ComparisonChart
              key={metric}
              metric={metric}
              runs={comparison.runs}
            />
          ),
        )}
      </section>
    </>
  );
}
function ComparisonChart({
  metric,
  runs,
}: {
  metric: "progress" | "action_count" | "failed_action_count";
  runs: ComparisonRun[];
}) {
  const values = runs.map((run) =>
    typeof run[metric] === "number" ? (run[metric] as number) : null,
  );
  const max = Math.max(
    ...values.filter((value): value is number => value !== null),
    1,
  );
  return (
    <div className="comparison-chart">
      <h3>{metric.replaceAll("_", " ")}</h3>
      {runs.map((run, index) => (
        <div key={run.run_id}>
          <span>{run.name}</span>
          {values[index] === null ? (
            <em>N/A</em>
          ) : (
            <i style={{ width: `${(values[index]! / max) * 100}%` }}>
              <b>{values[index]}</b>
            </i>
          )}
        </div>
      ))}
    </div>
  );
}
export function comparisonJson(comparison: Comparison): string {
  return JSON.stringify(comparison, null, 2);
}
export function comparisonCsv(comparison: Comparison): string {
  const headers = [
    "run_id",
    "name",
    ...columns.map((column) => String(column.key)),
  ];
  const rows = comparison.runs.map((run) =>
    headers.map((key) => csvValue(run[key as keyof ComparisonRun])).join(","),
  );
  return [headers.join(","), ...rows].join("\n") + "\n";
}
function csvValue(value: unknown): string {
  const text =
    value === null || value === undefined
      ? "N/A"
      : typeof value === "object"
        ? JSON.stringify(value)
        : String(value);
  return `"${text.replaceAll('"', '""')}"`;
}
function displayValue(value: unknown): string {
  return value === null || value === undefined || value === ""
    ? "N/A"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
}
function Message({ title, detail }: { title: string; detail: string }) {
  return (
    <section className="state-message" role="status">
      <h2>{title}</h2>
      <p>{detail}</p>
    </section>
  );
}
