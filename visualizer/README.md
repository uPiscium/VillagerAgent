# VillagerAgent Visualizer 0.1

The Visualizer is an optional, local, read-only explorer for recorded VillagerAgent runs. It is isolated from the runtime and benchmark dependency graph: the repository setup, `just check`, runtime, and benchmarks do not install or start it.

## Quick Start

Requirements are Python 3.10.19, `uv`, Node.js, npm, and `just`.

```bash
npm ci --prefix visualizer/frontend
just visualizer-fixture
```

Open `http://127.0.0.1:5173`. The checked-in fixture root contains successful, failed, timed-out, partial, malformed, and unsupported-schema examples. No Minecraft server, LLM, or credentials are needed.

To inspect existing results instead:

```bash
just visualizer-dev result
```

The development command starts the API on `127.0.0.1:8765` and Vite on `127.0.0.1:5173`. Stop either process with Ctrl-C.

## Production Build

Build the frontend and serve it from the read-only backend:

```bash
npm ci --prefix visualizer/frontend
just visualizer-serve result
```

Open `http://127.0.0.1:8765`. `visualizer-serve` builds `visualizer/frontend/dist` before starting the backend. The SPA fallback applies only to frontend paths; unknown `/api/*` paths remain JSON 404 responses.

Equivalent commands are:

```bash
npm run --prefix visualizer/frontend build
uv run --project visualizer/backend python -m villageragent_visualizer \
  --result-root result \
  --frontend-dist visualizer/frontend/dist
```

## Verification

```bash
just visualizer-backend-test
just visualizer-frontend-test
just visualizer-frontend-build
just visualizer-check
```

`just visualizer-check` is separate from root `just check` so Visualizer dependencies remain optional.

## Authority Boundaries

- Runtime DAG reads only the canonical runtime task DAG snapshot and labels it `Canonical Runtime State`.
- Analysis DAG is always a `Post-hoc Analysis Projection`. It is not runtime epistemic state or action-candidate authority.
- Timeline preserves API timing categories: exact, duration-only, and untimed. It does not invent absolute timestamps.
- Entity Inspector displays backend-sanitized DTOs and provides no mutation controls.
- Opening the Visualizer never resumes, retries, schedules, edits, or otherwise changes a run.

## Artifact Requirements

| View | Required artifact | Behavior when absent or invalid |
| --- | --- | --- |
| Run browser | Any recognized run marker or artifact | Run remains listed as partial or invalid where possible |
| Runtime DAG | `runtime_dual_dag_snapshot.json` or live `.runtime/runtime_result.json` checkpoint | View is unavailable; legacy `task_graph_snapshot.json` is not promoted to authority |
| Analysis DAG | `dual_dag_artifact.json` | View is unavailable |
| Timeline | `action_log.json` | View is unavailable; malformed individual records become warnings |

Common metadata is read from `attempt.json`, `summary.json`, and `artifact_manifest.json` when present. Supported artifact schema major is 1. Unsupported major versions are isolated to their view and reported without breaking other runs.

## Security

- The server binds to `127.0.0.1` by default. Treat `--host 0.0.0.0` as an explicit decision to expose local artifacts.
- Result paths are resolved beneath `--result-root`; traversal and external symlinks are rejected.
- Credential-like fields such as API keys, passwords, tokens, and secrets are recursively removed before API DTOs and Inspector JSON are produced.
- Absolute private paths are not exposed in public run warnings.
- The frontend consumes only the read-only API and has no runtime mutation endpoint.

Do not use this tool as an access-control boundary for an untrusted multi-user service. It has no authentication and is intended for local research artifacts.

## Limitations

Version 0.1 is an offline snapshot explorer. It does not implement live WebSocket updates, event replay, run comparison, a Minecraft world renderer, artifact repair, or runtime control. Large Analysis DAGs require filtering below the frontend layout threshold. ELK is loaded lazily but its layout chunk is intentionally large.

See [the full integration guide](../docs/visualizer.md) for architecture and troubleshooting.
