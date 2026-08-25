# K11 P0 Instrumentation Validation Runbook

Status: development/pilot procedure only. P0 is not a prevalence cohort and must never be pooled with final K11 evidence.

## 1. Required checkout

Use the dedicated branch/worktree:

```bash
git fetch origin
git worktree add ../VillagerAgent-k11-k12 experiment/k11-k12-ecological-validation
cd ../VillagerAgent-k11-k12
```

Before P0, the checkout must be clean:

```bash
git status --short
```

Expected output: empty.

The P0 runner independently checks cleanliness and fails closed otherwise.

## 2. Unit/integration contract tests

Run the K11-specific tests before any natural pilot run:

```bash
python -m pytest -q \
  tests/test_minecraft_k11_trace.py \
  tests/test_minecraft_k11_analysis.py \
  tests/test_minecraft_k11_calibration.py \
  tests/test_minecraft_k11_pilot.py
```

Do not start P0 if these tests fail.

These tests are development fixtures, not K11 natural observations. The controlled invalidation used by `test_minecraft_k11_analysis.py` exists only to verify the offline classifier and must never enter a P0/P1/final prevalence denominator.

## 3. Endpoint preflight

The checked-in P0 manifest reuses infrastructure identities already disclosed by existing repository configs:

- model: `gemma4:12b`
- Ollama endpoint: `http://10.255.255.5:11434`
- Minecraft target: `10.12.3.1:40000`

These are configuration provenance, not an assertion that the endpoints are currently available.

Before P0, verify reachability from the runtime host using the ordinary operator tools available there. For example:

```bash
curl -fsS http://10.255.255.5:11434/api/tags >/dev/null
nc -vz 10.12.3.1 40000
```

Endpoint failure is infrastructure failure. Do not silently replace the endpoint, model, Minecraft target, or task prompts after inspecting P0 outcomes. If infrastructure must change, update and commit the P0 manifest before restarting the pilot from a fresh output directory.

## 4. P0 manifest

The pilot manifest is:

```text
configs/minecraft/k11-p0-natural-manifest-v0.json
```

It contains exactly eight Advisory, `task_type=none`, non-judged, non-production runs. It forbids supplied stale EAC premanifest/revision values.

At pilot start the runner:

1. requires a clean Git checkout;
2. resolves the current `RuntimeExecution` source closure;
3. resolves exact `HEAD`;
4. deterministically derives the EAC runtime identity;
5. writes a run-local `K11_P0_EAC_PREMANIFEST.json`;
6. passes that exact revision and premanifest to the existing EAC admission path.

This prevents the historical checked-in Issue #510 premanifest from being incorrectly reused for K11 code.

## 5. Output location

P0 output must be outside the repository. Recommended sibling path:

```text
../VillagerAgent-k11-p0-results
```

The runner rejects an output root inside the source repository.

Do not reuse a non-empty run directory.

## 6. Run P0

```bash
python -m benchmarks.minecraft.k11_pilot \
  --manifest configs/minecraft/k11-p0-natural-manifest-v0.json \
  --output-root ../VillagerAgent-k11-p0-results
```

The command exits `0` only when `p0_passed=true`. Otherwise it exits `2` for a completed-but-invalid P0 validation result or raises on a manifest/identity precondition failure.

## 7. Expected artifacts

At the output root:

```text
K11_P0_EAC_PREMANIFEST.json
P0_CALIBRATION.json
P0_VALIDATION.json
K11-P0-01/
...
K11-P0-08/
```

Each run directory contains at minimum:

```text
k11_trace.json
k11_analysis.json
p0_validation.json
runtime_result.json        # when runtime collection reaches that path
runtime_events.jsonl       # existing baseline runtime journal
runtime/                   # isolated runtime artifacts
```

On runtime exception, `exception.txt` is preserved as pilot evidence.

## 8. P0 pass conditions

`P0_VALIDATION.json` must report all of the following:

```text
p0_passed = true
run_count = 8
runtime_error_count = 0
trace_valid_count = 8
offline_analysis_valid_count = 8
coverage_sufficient = true
calibration_error = null
```

Coverage additionally requires observation of:

- model-call start events;
- guarded tool-call entry events;
- EAC prepared-action events;
- actor-visible evidence-ingestion events;
- more than one actor/thread pair across the pilot.

The traced in-process calibration must also have a valid trace.

## 9. P0 does not answer prevalence

Even if the P0 diagnostic outputs contain D1-D6 or N0-N4 rows, they are development observations only.

Forbidden:

- reporting P0 N2 as a K11 prevalence result;
- pooling P0 actions with P1 or final K11;
- changing the final task pool because a P0 task happened to produce N2;
- treating zero P0 N2 as evidence that natural N2 cannot occur.

P0 answers only whether the instrumentation, reconstruction, correlation, ordering, and overhead measurement are usable.

## 10. After execution

Do not proceed directly to final K11.

After P0, inspect:

- `P0_VALIDATION.json`;
- all trace validation warnings/errors;
- offline replay errors;
- prepare-to-decision timing diagnostics;
- `P0_CALIBRATION.json` incremental overhead;
- whether multiple actor/thread execution was actually observed;
- whether the existing 256-entry EAC audit truncates while the K11 trace remains complete.

Only after that review may K11-D be marked passed and the workflow advance to the reconnaissance/freeze checkpoints specified by the protocol.
