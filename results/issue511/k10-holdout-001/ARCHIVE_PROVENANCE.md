# Archive provenance: Issue #511 K10 prospective holdout evidence

## Identity

- Study: K10 prospective holdout
- Repository: `upiscium/VillagerAgent`
- Issue: [#511](https://github.com/upiscium/VillagerAgent/issues/511)
- Run ID: `issue511-k10-holdout-001`
- Execution revision: `06267beb634bc75800385b37f2d431d3ceeaf473`
- Source output root: `/home/upiscium/VillagerAgent-k10-holdout-results`
- Source run directory: `/home/upiscium/VillagerAgent-k10-holdout-results/issue511-k10-holdout-001`
- Exposure-marker source path: `/home/upiscium/VillagerAgent-k10-holdout-results/.issue511-k10-effect-boundary-started.json`
- Archive destination: `results/issue511/k10-holdout-001/`
- Archive date (UTC): `2026-08-22`
- Scientific framing: **prospectively frozen, previously effect-boundary-unsubmitted within-construct holdout replication**
- K10e verdict: `HOLDOUT_REPLICATION_GO`

## Workflow references

- K10c authorization: https://github.com/upiscium/VillagerAgent/issues/511#issuecomment-5378952646
- K10d execution: https://github.com/upiscium/VillagerAgent/issues/511#issuecomment-5378977786
- K10e replication audit: https://github.com/upiscium/VillagerAgent/issues/511#issuecomment-5379057981

## Frozen bindings

- Runner contract digest: `c03ce1e55dbb9ff2f1afb5e8ce6b6aa7e30508f9fc1ef9b63a45d03b552f0a03`
- Runner implementation digest: `2ede1bd6d6d0068d5c8960ee0fdcd15fef24ea9751cb573bb0f514b355bedab2`
- Candidate-pool digest: `5e95279edfbd45ee258932ebfaa33ef2c1dc563273afa0b4878eb86eaf7bb2ff`
- Selection-manifest digest: `ce92e9426a10486b17dd0551a4dfce5a46cfcb4524fb67f3c2404b50df7f8480`
- Selected-inventory digest: `51ba4d0e8e387fd06450367f48d3049c07fc01665be888519acec556d5e3e58a`
- Result-schema digest: `6cd3a1919a3278aca80646a82233c54834c408f91bdb5a43aaf4f7587aa9c9b1`
- Protocol digest: `94b70d7d746863f7febb5d79169cf91b37bb78b79e73c7733fe58490ec81424e`
- Historical unseen-audit digest: `d17a37df879a71c0227e8c263202da2d716b98093a8c2a4c584e14f1d57af796`

## Authoritative hashes

- Exposure marker: `c3d650fcd50db936ef16da9b8734bef1ca1e4b77a2da5ef6508227c3a8c0180a`
- `run_manifest.json`: `d99f95cb89338b286afb7e76d9848e552a1dfca89aae3ba21459f3d2531fe7db`
- `final/final_manifest.json`: `3f16717f7526c422744bd255b7ef664adabcbe338ef34604f965fbd1ec64d945`
- `final/aggregate.json`: `906ae96c6f01b50baddb8b7810dacafa2f8ef99cbc48c09442307cfdaa933646`

## Inventory and size

- Historical artifact count: `124`
- Raw trace count: `120`
- Total historical bytes: `1,355,994`
- Largest historical file: `final/aggregate.json` (`154,849` bytes)

The 124 historical files listed in `SHA256SUMS` are byte-identical copies of the authoritative source evidence. This includes the output-root exposure marker, mapped to the archive root under its exact original filename. No historical JSON was parsed and rewritten, reserialized, normalized, reformatted, or regenerated.

`ARCHIVE_PROVENANCE.md` and `SHA256SUMS` are archival metadata. They were not part of the original K10 run and are excluded from the original scientific artifact and hash semantics.

## Preservation statement

This archival phase did not rerun K10, submit a K6 or K10 trial, call `execute_prepared()`, create a run ID, use another output root, create or replace an aggregate, modify a historical byte, pool K8 with K10, or change a scientific claim. It preserves existing evidence and creates no new scientific evidence.
