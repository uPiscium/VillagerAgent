# CRAFT Issue 291 Replication Plan

<!-- benchmark-result: craft-issue291-upstream-smoke-v1 -->

The replication matrix is declared in
`configs/craft/experiments/gemma4_12b_clarify_policy_official.yaml`. It includes
the full upstream CRAFT runner, the VillagerAgent baseline, Clarify-disabled
Dual-DAG, and the existing Clarify policy variants under matched structures,
seeds, `oracle_n=5`, no builder tools, and 20 turns. Smoke runs are diagnostic
only and must not be reported as performance evidence.

Issue #291 is not accepted or complete. The full matched matrix has not run,
retrieval activation and downstream influence have not been established, and
there is not yet evidence for the required Clarify recommendation. The bounded
smokes below establish integration behavior only.

The two-turn upstream integration smoke is retained as immutable release
`benchmark-craft-issue291-smoke-v1`. Its sanitized archive and checksums are
registered in `docs/benchmark_archives.json`; it is integration evidence only,
not a performance result.

## Isolated Upstream Runtime

The upstream runner uses the interpreter configured by
`craft.official_runner_interpreter`; it does not use or modify the project
environment. The checked-in Ollama config points to the dedicated issue venv:

```bash
uv venv --python 3.12 /tmp/opencode/issue-291-craft-py312-venv
uv pip install --python /tmp/opencode/issue-291-craft-py312-venv/bin/python \
  -r external/CRAFT/requirements.txt
/tmp/opencode/issue-291-craft-py312-venv/bin/python external/CRAFT/run_craft.py --help
```

The configured bootstrap sets `PYTHONHASHSEED` before interpreter startup and
seeds Python `random` and NumPy before executing the unmodified upstream script
with `runpy`. It preserves upstream `sys.argv`, script-relative imports, seed,
and structure order.

`configs/craft/official_baseline_gemma4_12b_ollama.yaml` runs `gemma4:12b`
through a temporary loopback compatibility proxy because this model exposes
thinking text but empty visible content through Ollama's OpenAI endpoint. The
proxy binds `127.0.0.1` on an ephemeral port, accepts the upstream runner's
OpenAI chat-completion calls, forwards them to Ollama native `/api/chat` with
`think=false`, maps generation options, and returns standard
`message.content`. Tool calls receive OpenAI IDs and JSON-string arguments;
contentless valid tool-call responses are accepted. Configured director and
builder temperature and token limits override upstream hard-coded request
values, and the run seed is forwarded as the native generation seed. It is
stopped in the runner cleanup path and both proxy HTTP requests
and the external process have explicit timeouts.

The adapter constructs a minimal child environment and supplies a random
per-run bearer token as `OPENAI_API_KEY` plus the ephemeral loopback
`OPENAI_BASE_URL`. The proxy rejects unauthenticated requests. The token,
which the external process needs for loopback authentication, is never retained;
unrelated credentials and the remote Ollama endpoint are not exposed to that
process. Credential-bearing upstream proxy URLs are rejected.

The non-proxy `official_baseline_full.yaml` declares only the allowlisted names
`OPENAI_API_KEY` and `OPENAI_BASE_URL` for parent-environment forwarding. Their
values are read at process launch, are not materialized in the resolved config,
and API keys are included in runtime redaction. Endpoint URLs are accepted only
with `http`/`https`, a clean host and optional port, and a known base API path;
userinfo, query strings, fragments, and arbitrary credential-bearing paths are
rejected before provenance or run artifacts are written.

Before sanitization, each available upstream director prompt is inspected for
the target structure, oracle candidates, and other directors' private views.
Each public director output is checked against every other director's private
view, and each public builder action/response is checked against every
director's private view, in addition to target/oracle guards where applicable.
The run fails when no inspectable evidence exists or a violation is found.
Normalized and derived-public artifacts exclude target structures, oracle
candidate lists, director/builder prompts, private reasoning, raw responses,
stdout/stderr, and upstream Markdown. Provenance fingerprints the upstream
commit, dataset, runner config and bootstrap, interpreter binary, requirements
file, installed Python distribution set, compatibility proxy source, and
observed Ollama model digest. Runtime metadata records authenticated request
counts by role, model names, `think=false`, timeouts, configured effective
generation settings, and the non-secret upstream endpoint.

## Execution Gates

Run and validate one structure for one turn before starting the matrix:

```bash
python -m benchmarks.craft.run \
  --config configs/craft/official_baseline_gemma4_12b_ollama.yaml \
  --structure 0 --turns 1 --seed 3 --overwrite
python -m benchmarks.craft.artifact_validator \
  --runs craft_official_baseline_gemma4_12b_ollama \
  --result-root result/craft \
  --output /tmp/opencode/issue-291-final-one-turn-validation.json
python -m benchmarks.common.publish_bundle sanitize \
  result/craft/craft_official_baseline_gemma4_12b_ollama \
  --output /tmp/opencode/issue-291-final-one-turn-public
python -m benchmarks.common.publish_bundle validate \
  /tmp/opencode/issue-291-final-one-turn-public
```

The semantic gate rejects known director fallback/error phrases and requires a
parsed builder action with `move_executed=true`. After the one-turn gate passes,
a two-turn bounded integration run may use `--turns 2` and the same validation
sequence. Do not launch the following full command unless both bounded runs
complete, real leakage checks pass, provenance has no unverifiable required
identities, and the bundle validator accepts the sanitized artifacts:

```bash
python -m benchmarks.craft.experiment \
  --config configs/craft/experiments/gemma4_12b_clarify_policy_official.yaml
```

## Analysis And Archive

Create one paired-analysis observation per `(structure_id, seed)` from each
run's `normalized/metrics.csv`. Failed and missing runs remain explicit. Then
run the #298 analysis contract before making any performance claim:

```bash
python -m benchmarks.common.analysis \
  result/craft/issue-291-comparison.json \
  --output result/craft/issue-291-comparison-report.json
```

After deriving and validating a sanitized public bundle, use the #297 archive
commands. Local output is staging only and is not an immutable publication:

```bash
python -m benchmarks.common.publish_bundle sanitize \
  result/craft/issue-291-private \
  --output result/public/craft-issue-291
python -m benchmarks.common.publish_bundle validate result/public/craft-issue-291
python -m benchmarks.common.publish_bundle archive \
  result/public/craft-issue-291 \
  --output /tmp/opencode/craft-issue-291.zip
```

Record the immutable release identifier, archive checksum, and archived
manifest checksum according to `docs/benchmark_artifact_retention.md` before a
paper-facing result is cited.
