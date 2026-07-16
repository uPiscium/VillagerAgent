# Benchmark Artifact Retention And Publication

This policy applies equally to CRAFT, C-WAH, and Minecraft/VillagerBench. A local path is optional provenance, never the durable reference for a paper-facing or benchmark-facing result.

## Retention Classes

| Class | Contents | Location and access | Minimum retention | Deletion |
| --- | --- | --- | --- | --- |
| Raw/private | Prompts, private observations, hidden evaluator state, credentials, runtime checkpoints, and unsanitized logs | Restricted project storage; benchmark owners and designated auditors only | Until the associated publication is withdrawn plus 12 months, subject to dataset/license limits | Two-person owner approval; record the artifact ID, reason, date, and deletion scope |
| Sanitized-public | Completed managed bundle with provenance, resolved non-secret config, per-run status/metrics, aggregate tables, and checksummed manifest | Immutable GitHub release or configured archival repository | Indefinite | Never replace or delete a cited object; publish a superseding archive and migration record |
| Aggregate | Tables and reports derived from sanitized-public runs | In the same immutable archive as source manifests | Indefinite | Same as sanitized-public |
| Failed-run | Finalized failed run artifacts and partial data safe to retain | Public when sanitized and manifested; otherwise restricted raw/private storage | Same duration as the analysis that includes the run | Deletion requires explicit aggregate accounting (`failed` or `missing`) and the raw/private deletion record |

An interrupted attempt without a finalized manifest is private and is not publishable. A finalized failed child may be included in a completed matrix bundle; its failed status must remain in the manifest and aggregate accounting. Missing, failed, and completed counts must sum to the expected run count.

## Validate And Archive

The publication CLI validates existing attempt IDs, checksums, terminal status, provenance, and resolved config. It rejects credential values, private observations, hidden evaluator state, symlinks, hidden/runtime/raw paths, unexpected file types, files over 50 MiB, and bundles over 500 MiB. JSON, JSONL, CSV, YAML, and text key/value records are scanned structurally where applicable.

```bash
python -m benchmarks.common.publish_bundle sanitize result/craft/source-run \
  --output result/public/craft-evaluation
python -m benchmarks.common.publish_bundle validate result/public/craft-evaluation
python -m benchmarks.common.publish_bundle archive result/public/craft-evaluation \
  --output dist/craft-evaluation-v1.zip
```

`sanitize` consumes a finalized completed or failed #296 bundle, excludes raw/private/hidden/runtime paths, recursively removes hidden-state fields, redacts credential fields, and writes a new exact artifact manifest. `publication_source.json` and the derivative provenance retain the source attempt ID, status, producer, manifest SHA-256, and completed/failed accounting for nested attempts. The source bundle is never modified.

ZIP member order, timestamps, permissions, and compression settings are fixed. The adjacent `.metadata.json` records the archive SHA-256, source manifest SHA-256, attempt ID, benchmark, size, and completed/failed run counts. Rebuilding unchanged input therefore produces the same bytes and checksum.

## Immutable Publication

Publish to a new GitHub release tag, never an existing release:

```bash
python -m benchmarks.common.publish_bundle publish result/public/craft-evaluation \
  --output dist/craft-evaluation-v1.zip \
  --publisher github --repository OWNER/REPOSITORY --tag benchmark-craft-v1
```

The command verifies GitHub's repository immutable-releases endpoint, checks that the release does not exist before invoking `gh release create`, and verifies the created release's `immutable` property before returning a reference. Publication fails unless both checks succeed. The caller needs `gh` authentication with release/content write permission and admin read permission for the immutable-release check; in CI this is normally a fine-grained token exposed to `gh` as `GH_TOKEN`.

Local output is staging only, not immutable publication. Use `--publisher local-staging --archive-root PATH --stable-id ID`; the destination ID must be one safe path component, the root cannot traverse symlinks, and the destination must not exist. Upload the staged files to an immutable archive before declaring a paper result. Library integrations can implement the `Publisher` protocol for DOI, institutional, or object-lock archives and must return a stable identifier, immutable URL, and the exact archive checksum.

## Stable References And Documentation

Paper-facing result documents declare an ID using an HTML comment in the form `paper-result: {ID}`. Add that ID to `docs/benchmark_archives.json` with:

- benchmark (`craft`, `cwah`, or `minecraft`)
- immutable archive URL and SHA-256
- path and SHA-256 of the archived `artifact_manifest.json`
- complete expected/completed/failed/missing run accounting

`just validate` scans reported-evaluation signatures in both `docs/` and `benchmarks/` and rejects a report without a declaration. Archived declarations are fetched with their metadata; archive, metadata, and embedded manifest checksums must agree, and registry completed/failed counts must match metadata `run_statuses`. GitHub references must use a release asset URL with a dedicated versioned tag, not a branch URL, latest-release URL, workflow artifact, or local path.

The fixed set of historical diagnostics recorded before this policy is classified `legacy-diagnostic-unarchived`, with every expected run accounted as missing and `publication_satisfied` and `claim_eligible` set to false. When recovery has been exhausted, the entry also records `retired=true`, `paper_facing=false`, `recovery_status=exhausted`, and the retirement date. This is only an explicit historical inventory: it does not satisfy publication, cannot support paper or performance claims, and cannot be assigned to new result IDs.

Retired notes use `historical-result`, not `benchmark-result` or `paper-result`. The documentation check permits that declaration only for the fixed pre-policy inventory and rejects any retired entry used as benchmark-facing or paper-facing evidence. Closing a recovery issue means the inventory, policy, and exclusion are complete; it does not mean that missing artifacts were reconstructed or archived. Every new `benchmark-result` or `paper-result` declaration after adoption of this policy must resolve to a fetched, checksum-verified archive.

## Recovery

1. Retrieve the archive and metadata from the stable reference.
2. Verify `sha256sum ARCHIVE` against `archive_sha256` before extraction.
3. Extract into a new directory and verify `artifact_manifest.json` membership, sizes, hashes, attempt ID, and `_COMPLETED` with the validation CLI.
4. Use `provenance.json`, resolved config, per-run summaries/metrics, and aggregate tables to reconstruct the report. Treat `environment_unverifiable` as a reproducibility limitation.
5. If the public archive is unavailable, restore the exact object from archival replication under the same stable ID. Never rebuild different bytes under an existing citation.

## Schema Migration

Published archives are immutable and interpreted with their recorded schema and producer commit. Migrations create a new managed bundle, archive checksum, stable ID, and registry entry. Preserve the original archive and add machine-readable source archive/checksum and migration notes; do not rewrite old manifests. Additive readers may support older schemas, but a lossy or semantic migration requires regenerated aggregate tables and a new paper-result declaration. Restricted source data used for migration remains under the raw/private policy.
