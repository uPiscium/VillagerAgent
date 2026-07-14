# Termination Semantics

Termination state is not the same as benchmark success. A run can terminate and still be failed, blocked, partial, timed out, or invalid for performance comparison.

## Success

- Condition: all required tasks or benchmark objectives complete successfully.
- Controller reason: no unfinished work remains and the graph terminal state is `SUCCESS`.
- Artifacts: preserved.
- Comparison: comparable when the run set and environment are documented.

## Failure

- Condition: one or more required tasks fail, or the benchmark score marks failure.
- Controller reason: task reflection or environment feedback marks a task `failure`. For a multi-agent execution group, any agent exception, failed reflection, or per-task timeout fails the whole task; all agents must succeed for task success.
- Artifacts: preserved when using benchmark harnesses.
- Comparison: comparable as a failed run when runtime itself did not fail.

## Blocked

- Condition: unknown tasks remain but none are runnable, and no task is running.
- Controller reason: dependencies cannot become satisfied from current graph state.
- Snapshot diagnosis: `dependency_blockers` identifies each direct/transitive non-success predecessor and its status. A failed predecessor is explicitly reported rather than producing an empty blocker list.
- Graph state: `BLOCKED`.
- Artifacts: should be preserved for diagnosis.
- Comparison: diagnostic unless the benchmark defines blocked as a normal failure outcome.

## Timeout

- Condition: bounded execute mode exceeds `--execute-timeout-seconds`.
- Harness representation: `timed_out == true`, `error_type == "timeout"`, and `execute_timeout_seconds` set in `summary.json`.
- Process representation: `runtime_process_isolated == true`, `runtime_process_terminated == true`, and `runtime_process_killed` records whether the kill fallback was required. The child has been joined before artifacts are written.
- Artifacts: partial artifacts are preserved.
- Comparison: diagnostic unless timeout policy is part of the benchmark protocol.

This harness timeout is distinct from a controller task-group timeout. A task-group timeout produces a normal task `failure` with per-agent results, clears `active_agents`, and preserves the assigned group in `last_assigned_agents`.

## Runtime Error

- Condition: exception outside normal task failure, such as server unavailable or bridge crash.
- Harness representation: `error` and `error_type` in `summary.json` and common reports.
- Artifacts: partial artifacts are preserved by the benchmark harness.
- Comparison: not a task-performance result.

## Partial

- Condition: some progress or artifacts exist but no successful terminal score is produced.
- Harness representation: `progress` may be present, with or without `error`.
- Artifacts: preserved.
- Comparison: diagnostic only unless the benchmark explicitly compares partial progress.

## Cancelled

- Condition: run is stopped externally or intentionally skipped.
- Harness representation: no standard Minecraft field currently represents cancellation; record it in external run notes if needed.
- Artifacts: may be incomplete.
- Comparison: not comparable.
