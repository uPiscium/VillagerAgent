# Gate A v3 restart-marker defect investigation

Issue #503 is offline-only. It does not start Minecraft or Docker, issue a live
RCON query, contact the model, execute a Gate, or create a new experiment
identity. The consumed v3 execute launcher now rejects even an otherwise
authenticated invocation with `consumed_v3_execution_disabled`.

## Retained evidence

The retained v3 world scoreboard contains both approved entries with score
exactly one:

- `baseline_open` in `va_baseline`;
- the source marker derived from the approved source archive in `va_baseline`.

The post-restart server log contains two completed marker-query RCON sessions,
followed by the separate cleanup `stop` session. The server had reached `Done`
and started its RCON listener. This rejects missing baseline contents, a source
marker identity mismatch, and total RCON unavailability. Because raw query
responses were intentionally not retained, the artifacts alone cannot prove
the source response comparison; all historical marker conclusions remain
bounded in that respect.

The execution process had neither
`VILLAGER_MINECRAFT_MODEL_API_BASE` nor
`VILLAGER_MINECRAFT_MODEL_API_KEY_ENV`. In the consumed implementation,
`DockerMatrixExecutor._config()` ran immediately after successful marker
verification and unconditionally required both values. It therefore had a
deterministic post-restart failure before writing `matrix_launch_config.json`,
which matches the empty retained output directory and absence of any client or
judged-runtime connection.

The supported high-confidence root category is
`model_execution_environment_missing`, with affected check `other`. This is an
inference from deterministic code/environment plus retained sequencing, not a
claim that a raw historical source-marker response was recovered.

## Bounded marker evidence

`restart_marker_verification.py` defines the closed schema
`minecraft_restart_marker_verification.v1`. It reports only stage categories:

```text
baseline_marker.read=<not_attempted|success|failed>
baseline_marker.normalization=<not_attempted|success|failed>
baseline_marker.compare=<not_attempted|match|mismatch>
source_marker.read=<not_attempted|success|failed>
source_marker.normalization=<not_attempted|success|failed>
source_marker.compare=<not_attempted|match|mismatch>
```

No response, marker value, command output, container name, host path, or
exception text is retained. The Docker runtime keeps its general RCON exception
contract; only the marker boundary translates failures. Production marker
checks remain single-attempt. Fake-only tests exercise bounded readiness delay
without changing the approved fail-fast production semantics.

## Minimal correction and identity impact

The future runtime must still receive an explicitly admitted model endpoint.
For credentialless Ollama, the runtime now preserves the launch-bound model and
endpoint while supplying only the fixed dummy key `ollama`; it never forwards
an ambient `OLLAMA_API_KEY`. A named credential binding remains mandatory when
one is explicitly selected.

The marker verifier is part of the runtime composite digest. Changes to
`docker_runtime.py`, `experiment.py`, and the new verifier alter executable
runtime behavior and therefore cannot reuse the consumed v3 revision,
premanifest, child manifest, runtime digest, or component hashes.

The next candidate identity is `minecraft-judged-production-v4`. It requires a
new execution revision, runtime closure/manifest, runtime digest, premanifest,
component manifest, readiness evidence, and explicit approval before any
judged execution. The baseline, model identity, target, tolerance, entity-feet
convention, prompts, generation parameters, judging semantics, and no-retry
policy are unchanged. Issue #503 creates none of those artifacts and performs
no v4 execution.
