# Gate A v4 control plane

Issue #507 defines one fixed, one-shot canary.  Identity, revision, child
manifest/count, runtime, model endpoint/digest, Docker contract, premanifest
hashes, and canary are constants in the v4 admission module; callers cannot
select a run, command, baseline, model, path, or container.

The lifecycle is monotonic and exact-accounted.  It permits one restore, one
executor, one validation, and one cleanup; retry, resume, replacement, a
second run, Gate B/C, matrices, and production are absent.  Docker is exposed
only through a lease-bound restricted capability.  The adapter and coordinator
are real and fake-testable. Their entry points require nominal host run-launch
and run-composition authorities; concrete minting is deliberately absent because
it belongs to a future externally authenticated launcher. These authorities apply
only to creating the outer owned run envelope. They are not EAC execution permits
and do not evaluate `EPre`, witnesses, `EAdm`, or per-action freshness.
Python module APIs are not the security boundary: without that launcher no
execution can be authenticated or composed, and no Gate A authorization is
claimed here. The bootstrap uses only the pinned Docker executable.

The envelope supervises the frozen Minecraft runtime without wrapping its native
effect gateway in a new semantic API. Future #509/#510 work must use a new
execution identity/premanifest and may mediate supported actions at the lowest
applicable effect boundary inside that supervised runtime; this v4 control plane
does not predefine or emulate that mediation.

An externally pinned bootstrap authenticates and executes the exact readiness-launcher
bytes. The launcher authenticates the remaining control-plane components and
independently authenticates runtime bytes from the frozen checkout. It returns
`execution_authority: false`. Readiness performs bounded read-only Git and Docker
CLI probes plus one Ollama inventory GET, but creates no owned state or container,
performs no model generation or Minecraft activity, and cannot invoke the adapter,
executor, validator, or a Gate run. The intended-host
premanifest is `/tmp/opencode/issue-506-v4-25113661-private/premanifest.json`.
Tests should inject safe fixture bindings and test-only authorization fakes
rather than invoke live readiness.
