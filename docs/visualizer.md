# Offline Experiment Visualizer

## Architecture

The optional Visualizer has three layers:

1. `visualizer/backend` discovers run directories, validates artifact versions and shapes, sanitizes public values, and exposes read-only FastAPI DTOs.
2. `visualizer/frontend` provides the run browser, canonical Runtime DAG, post-hoc Analysis DAG, Timeline, and shared Entity Inspector.
3. `visualizer/fixtures/runs` supplies deterministic offline runs for all supported run states and malformed/version-isolation checks.

No package under `pipeline`, `env`, `model`, or `benchmarks` imports the Visualizer. Artifact producers remain authoritative; the Visualizer is a consumer and does not rewrite source files.

## Data Flow

```text
result root
  -> bounded artifact repository
  -> run/runtime/analysis/timeline adapters
  -> sanitized API DTOs under /api/v1
  -> React views and read-only Inspector
```

Runtime and analysis data deliberately remain separate. `runtime_dual_dag_snapshot.json` is canonical runtime task state. `dual_dag_artifact.json` is a post-hoc projection for analysis. The frontend labels both boundaries rather than merging their semantics.

## Development

Install only when using the Visualizer:

```bash
npm ci --prefix visualizer/frontend
uv sync --project visualizer/backend --extra dev
```

Start the complete fixture explorer:

```bash
just visualizer-fixture
```

Use another result root with `just visualizer-dev path/to/results`. Vite proxies `/api` to the backend. Backend-only development is available through `just visualizer-backend-dev --result-root path/to/results`.

## Production Static Serving

`just visualizer-serve path/to/results` performs a frontend production build and passes `visualizer/frontend/dist` to the backend. Static serving is optional: without `--frontend-dist`, FastAPI remains API-only.

The backend mounts hashed files only under `/assets` and returns `index.html` for non-API frontend routes. Any path beginning with `/api` is excluded from SPA fallback. This preserves API error semantics and prevents an HTML 200 response for misspelled endpoints.

## Failure Isolation

Run discovery processes directories independently. A malformed summary, invalid encoding, incomplete artifact set, or unsupported schema major produces an invalid/partial run or a view-level error; it does not prevent healthy runs from being listed or opened. Missing related entities stay inside the shared Inspector rather than becoming a page-level 404.

The fixture matrix demonstrates:

- `successful`: all offline views and cross-view Inspector links.
- `failed`: terminal failure with a failed timeline action.
- `timed-out`: timeout state and zero-duration action.
- `partial`: action log without terminal metadata plus malformed records.
- `malformed`: invalid summary JSON isolated from other runs.
- `schema-version`: unsupported Runtime DAG schema major isolated to that view.

Runs with `events.jsonl` also expose a separate recorded Replay view. Replay is reconstructed from public events, never writes state back to runtime, and labels action records as log records rather than observed start/completion hooks.

## Security Boundary

The artifact repository rejects traversal and external symlinks. Sanitization occurs in the backend before DTO construction, recursively removing credential keys and suspicious secret-bearing names. The Inspector's “raw JSON” is raw only relative to the public DTO; it never reads artifact files in the browser.

The service defaults to loopback and has no authentication. Do not expose a result root to untrusted users. Review artifacts before deliberately binding to a non-loopback host.

## Troubleshooting

- Empty run list: verify `--result-root` points to the directory containing run folders, not to one individual run.
- Disabled tab: the selected run lacks that view's required artifact.
- Unsupported schema: regenerate artifacts with the current producer or use a supported major version; the Visualizer does not migrate source data.
- Frontend route returns 404 in production: pass a built directory containing `index.html` through `--frontend-dist` or use `just visualizer-serve`.
- Backend unavailable in development: confirm port 8765 is free and both child processes from `visualizer-dev` are running.
- Large graph warning: narrow Analysis DAG node, edge, agent, confidence, or text filters before layout.

Optional extensions provide resilient live snapshots, normalized event replay, and descriptive multi-run comparison without changing runtime authority. They remain disabled or unavailable when their source artifacts are absent.
An external read-only world camera can be explicitly configured; see [World View feasibility](visualizer_world_view.md). It remains a separate process and optional dependency. Bot control, runtime mutation, evaluator state, and unauthenticated automatic world-server exposure remain out of scope.
