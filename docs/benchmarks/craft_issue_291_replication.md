# CRAFT Issue 291 Replication Plan

<!-- benchmark-result: craft-issue291-upstream-smoke-v1 -->
<!-- benchmark-result: craft-issue291-v0-diagnostic-v1 -->
<!-- benchmark-result: craft-issue291-v1-diagnostic-v1 -->
<!-- benchmark-result: craft-issue291-v4-diagnostic-v1 -->
<!-- benchmark-result: craft-issue291-analysis-diagnostic-v1 -->

The replication matrix is declared in
`configs/craft/experiments/gemma4_12b_clarify_policy_official.yaml`. It includes
the full upstream CRAFT runner, the VillagerAgent baseline, Clarify-disabled
Dual-DAG, and the existing Clarify policy variants under matched structures,
seeds, `oracle_n=5`, no builder tools, and 20 turns. Smoke runs are diagnostic
only and must not be reported as performance evidence.

Issue #291 is not accepted or complete. The full matched matrix has not run,
natural retrieval activation was not observed in the bounded model run, and
there is not yet evidence for the required Clarify recommendation. The bounded
results below are diagnostic only. A controlled public-history probe establishes
that retrieval can activate and influence ranking, but it is not a performance
evaluation and does not establish activation in normal CRAFT episodes.

The two-turn upstream integration smoke is retained as immutable release
`benchmark-craft-issue291-smoke-v1`. Its sanitized archive and checksums are
registered in `docs/benchmark_archives.json`; it is integration evidence only,
not a performance result.

## Bounded Matched Diagnostic

The bounded run used repository commit
`367b0801f1e42691b02996a95f617b04b747593f`, CRAFT commit
`0630f1b3350ce2ae9fef676c8271c35963a09b45`, dataset SHA-256
`c7a57048ec0d2e92c25bde8aa7936c911919accdcdcedc078e9ccf1e0a2c9e3a`,
and Ollama `gemma4:12b` digest
`4eb23ef187e2c5462566d6a1d3bbbc2f1346d0b4327cbb66d58fffbcc9b2b05c`.
All conditions used structure 0, seed 3, three turns, `oracle_n=5`, no builder
tools, three VillagerAgent directors, partial observations, no common ground,
director temperature 0.2, builder temperature 0.0, `think=false`, and a 4096
token limit. The VillagerAgent run used the project CPython 3.10 environment;
the separately labeled full-upstream smoke used the required isolated CPython
3.12 environment at `/tmp/opencode/issue-291-craft-py312-venv`.

| Condition | Final progress | Physical actions | Clarify / wait | Retrieval nodes | Retrieval used in top action | Retrieval changed top action |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| V0 VillagerAgent baseline | 0.1138486312 | 3 | 0 / 0 | 0 | 0 | 0 |
| V1 Dual-DAG, Clarify disabled | 0.1305152979 | 3 | 0 / 0 | 0 | 0 | 0 |
| V4 value-of-information | 0.1305152979 | 3 | 0 / 0 | 0 | 0 | 0 |

All three runs completed with no builder fallback, no invalid action, and no
normalized leakage violation. The V4 gate was not invoked in these three turns,
so this run provides no evidence about Clarify policy behavior. The observed
progress values are retained solely to construct matched diagnostic records;
one structure/seed pair cannot support a performance claim or recommendation.

The checked-in #298 contracts are
`docs/benchmarks/evidence/craft_issue_291/v0_v1_comparison_input.json` and
`docs/benchmarks/evidence/craft_issue_291/v0_v4_comparison_input.json`, with
corresponding `*_comparison_report.json` outputs in the same directory. Each
has one matched `(structure_id=0, seed=3)` pair,
requests only a `diagnostic` claim, is marked non-prespecified and bounded, and
uses a two-comparison Bonferroni family. Both reports grant only `diagnostic`;
their performance gates reject the evidence as not full, not prespecified, and
insufficient in pairs, seeds, and comparison units. The numerical paired
difference is recorded by the contract but must not be interpreted as an
effect estimate from an adequate sample.

## Controlled Retrieval Probe

A deterministic probe exercised `DualDAGRuntime.current_turn_decision_support`
with only one prior public `reported_claim` ("Place a red block at bottom left")
and two public action candidates. It supplied no private view, target structure,
or oracle candidates. Without retrieval, candidate A ranked first at confidence
0.70. Retrieval found one supporting public claim for candidate B, raised its
confidence from 0.68 to 0.73, and changed the recommendation from A to B:

| Metric | Value |
| --- | ---: |
| Retrieved claims | 1 |
| Retrieved actions | 0 |
| Retrieval used in top action | true |
| Retrieval changed top action | true |

This controlled mechanism check does not override the zero retrieval counts in
the matched model run. An initial probe encoded positions as lists, while the
runtime action schema requires coordinate strings such as `"(0,0)"`; it
correctly retrieved zero nodes and was rerun with valid public action inputs.
The reproducible public-only input is
`benchmarks/craft/fixtures/issue_291_retrieval_probe_input.json`; its checked-in
machine-readable output is
`docs/benchmarks/evidence/craft_issue_291/retrieval_probe_output.json`. The CLI
rejects hidden-state and underscore-prefixed keys before constructing runtime
state.

## Diagnostic Releases

The three normalized artifact sets passed `benchmarks.craft.artifact_validator`.
Each source was then sanitized, validated as a 21-artifact public bundle with
one completed run, and published under a new release whose immutable property
was checked by the publication CLI.

| Condition | Immutable archive | Archive SHA-256 | Manifest SHA-256 |
| --- | --- | --- | --- |
| V0 | [craft-issue291-v0-diagnostic-v1.zip](https://github.com/upiscium/VillagerAgent/releases/download/benchmark-craft-issue291-v0-diagnostic-v1/craft-issue291-v0-diagnostic-v1.zip) | `c70b293d3e995a8c5cf5ee1ca2496ca1e95337233750fcf788ade35295cc13e4` | `966870f3343cd84fcc096b03b7f95d7663511ce5436e3500aaf72e545ef8621c` |
| V1 | [craft-issue291-v1-diagnostic-v1.zip](https://github.com/upiscium/VillagerAgent/releases/download/benchmark-craft-issue291-v1-diagnostic-v1/craft-issue291-v1-diagnostic-v1.zip) | `2b52cb5a8c4521151ee67e71b8900ed278ef8c646125013cbdc7832d765c7d03` | `28a0e8cc38ce52327eb9c3bc30ae9b933d45c4d6dbda1ed5c851983b77564ad8` |
| V4 | [craft-issue291-v4-diagnostic-v1.zip](https://github.com/upiscium/VillagerAgent/releases/download/benchmark-craft-issue291-v4-diagnostic-v1/craft-issue291-v4-diagnostic-v1.zip) | `a92193a688830a3757df6aa89a0e566327fc17af947ab448c783a3e3158e4fd2` | `45213f6747f174419dc05aada7bb09a642fbc95339ddddc6213dc3930c6b74b2` |
| Analysis and retrieval probe | [craft-issue291-analysis-diagnostic-v1.zip](https://github.com/upiscium/VillagerAgent/releases/download/benchmark-craft-issue291-analysis-diagnostic-v1/craft-issue291-analysis-diagnostic-v1.zip) | `be2c5378eebf3a2a8bf48e505a0dc32ae51122f9e7c65165ce27ffd4672cb119` | `65e78f77bdf9fb3add729a5d5d46f87a7c245334710778c6ffc187587ce09cce` |

The analysis archive is a separate managed diagnostic bundle. It contains both
#298 inputs and reports, the retrieval probe input and output, explicit V0/V1/V4
release URLs and archive/manifest checksums, provenance, resolved non-secret
config, summary, metrics, publication-source accounting, and an exact artifact
manifest. It contains no additional episode sample and does not change the
claim level.

## Executed Commands

```bash
python -m benchmarks.craft.experiment \
  --config configs/craft/experiments/issue_291_matched_mini.yaml --dry-run
python -m benchmarks.craft.experiment \
  --config configs/craft/experiments/issue_291_matched_mini.yaml --overwrite
python -m benchmarks.craft.artifact_validator \
  --runs craft_eval_gemma4_12b_ollama_issue291_v0_oracle5_turns3_seed3 \
    craft_eval_gemma4_12b_ollama_dual_dag_retrieval_issue291_v1_oracle5_turns3_seed3 \
    craft_eval_gemma4_12b_ollama_dual_dag_value_of_information_issue291_v4_oracle5_turns3_seed3 \
  --result-root result/craft \
  --output /tmp/opencode/issue-291-matched-validation.json
python -m benchmarks.common.analysis \
  docs/benchmarks/evidence/craft_issue_291/v0_v1_comparison_input.json \
  --output docs/benchmarks/evidence/craft_issue_291/v0_v1_comparison_report.json
python -m benchmarks.common.analysis \
  docs/benchmarks/evidence/craft_issue_291/v0_v4_comparison_input.json \
  --output docs/benchmarks/evidence/craft_issue_291/v0_v4_comparison_report.json
python -m benchmarks.craft.retrieval_probe \
  --input benchmarks/craft/fixtures/issue_291_retrieval_probe_input.json \
  --output docs/benchmarks/evidence/craft_issue_291/retrieval_probe_output.json
python -m benchmarks.craft.diagnostic_bundle \
  --config configs/craft/diagnostics/issue_291_analysis_bundle.json \
  --output result/craft/craft_issue291_analysis_diagnostic_v1
python -m benchmarks.common.publish_bundle sanitize \
  result/craft/craft_issue291_analysis_diagnostic_v1 \
  --output /tmp/opencode/craft-issue291-analysis-diagnostic-v1-public
python -m benchmarks.common.publish_bundle validate \
  /tmp/opencode/craft-issue291-analysis-diagnostic-v1-public
python -m benchmarks.common.publish_bundle publish \
  /tmp/opencode/craft-issue291-analysis-diagnostic-v1-public \
  --output /tmp/opencode/craft-issue291-analysis-diagnostic-v1.zip \
  --publisher github --repository upiscium/VillagerAgent \
  --tag benchmark-craft-issue291-analysis-diagnostic-v1
python -m benchmarks.common.publish_bundle sanitize \
  result/craft/craft_eval_gemma4_12b_ollama_issue291_v0_oracle5_turns3_seed3 \
  --output /tmp/opencode/issue-291-v0-public
python -m benchmarks.common.publish_bundle sanitize \
  result/craft/craft_eval_gemma4_12b_ollama_dual_dag_retrieval_issue291_v1_oracle5_turns3_seed3 \
  --output /tmp/opencode/issue-291-v1-public
python -m benchmarks.common.publish_bundle sanitize \
  result/craft/craft_eval_gemma4_12b_ollama_dual_dag_value_of_information_issue291_v4_oracle5_turns3_seed3 \
  --output /tmp/opencode/issue-291-v4-public
python -m benchmarks.common.publish_bundle validate /tmp/opencode/issue-291-v0-public
python -m benchmarks.common.publish_bundle validate /tmp/opencode/issue-291-v1-public
python -m benchmarks.common.publish_bundle validate /tmp/opencode/issue-291-v4-public
python -m benchmarks.common.publish_bundle publish /tmp/opencode/issue-291-v0-public \
  --output /tmp/opencode/craft-issue291-v0-diagnostic-v1.zip \
  --publisher github --repository upiscium/VillagerAgent \
  --tag benchmark-craft-issue291-v0-diagnostic-v1
python -m benchmarks.common.publish_bundle publish /tmp/opencode/issue-291-v1-public \
  --output /tmp/opencode/craft-issue291-v1-diagnostic-v1.zip \
  --publisher github --repository upiscium/VillagerAgent \
  --tag benchmark-craft-issue291-v1-diagnostic-v1
python -m benchmarks.common.publish_bundle publish /tmp/opencode/issue-291-v4-public \
  --output /tmp/opencode/craft-issue291-v4-diagnostic-v1.zip \
  --publisher github --repository upiscium/VillagerAgent \
  --tag benchmark-craft-issue291-v4-diagnostic-v1
```

The dry-run created managed placeholders at the intended output names; the
subsequent `--overwrite` replaced only those placeholders. No evaluation run
failed. Local inspection attempts using `jq` failed because `jq` is not
installed; artifact inspection continued with repository tools. No second
seed/structure was launched because the requested bounded start was stable but
did not naturally activate retrieval, and the controlled probe isolated that
mechanism at substantially lower runtime without claiming episode performance.

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
