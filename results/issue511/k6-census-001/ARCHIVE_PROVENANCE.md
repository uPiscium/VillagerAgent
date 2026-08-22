# Archive provenance: Issue #511 K8 census evidence

## Identity

- Repository: `upiscium/VillagerAgent`
- Issue: [#511](https://github.com/upiscium/VillagerAgent/issues/511)
- Run ID: `issue511-k6-census-001`
- Execution revision: `ecb553323487bff69be2cfa375caea8dd02eada5`
- Original source path: `/tmp/opencode/VillagerAgent-k6-census-results/issue511-k6-census-001`
- Archive date (UTC): `2026-08-22`

## Workflow references

- K7e authorization: https://github.com/upiscium/VillagerAgent/issues/511#issuecomment-5377840361
- K8 execution: https://github.com/upiscium/VillagerAgent/issues/511#issuecomment-5377887100
- K9 audit: https://github.com/upiscium/VillagerAgent/issues/511#issuecomment-5377966745

## Frozen bindings

- Runner contract digest: `165cf4864079a73e0ba932b78fcc5f8ca8bc2bab7ec1e1ba94fa9be05e718f96`
- Runner implementation digest: `e181c50c2e0a3925211e06b3a83580f1db420a87be472f6b4fc0ef8d50acfa6d`
- K6 protocol digest: `b03f1257a71f6ade18dca8191fae7d42ba8558831ab36b91a03e1620f05f20bb`
- K6 inventory digest: `81f40e6ca937321536ca081ab61cde5949c16af7ab540b7d5bd92af00ec7f15c`
- K6 result-schema digest: `3fbde9b456c28a3a954b97819435314e4bc2bf1c1fec47ca2a151aaa5f32a02f`

## Authoritative hashes

- `run_manifest.json`: `46c363cd778e7b92f0534577b946653adbb39a801f983767d37524ca8c6a0186`
- `final/final_manifest.json`: `54226e26035d2d1f0a4f0530a21882b5f5440aaf0f51c1d42de228eb0ff0c3c4`
- `final/aggregate.json`: `815c7a3f0ba675675dca93d742b5c4899c25da7e206a2db208e40ede2f5f15fc`

## Preservation statement

All 63 historical K8 run files listed in `SHA256SUMS`, including all 60 numbered cell traces, are byte-identical copies of the authoritative source directory. They were copied without JSON reserialization, normalization, whitespace changes, field reordering, or content regeneration.

This run is a **frozen post-development census with disclosed engineering pre-exposure**.

This archive preserves evidence; it is not a new execution. No census runner or `K6Trial.submit()` call was made, and archival created no additional scientific exposure.

`ARCHIVE_PROVENANCE.md` and `SHA256SUMS` are new archival metadata. They are not part of the original K8 run, are excluded from its historical scientific hash semantics, and must not be interpreted as generated K8 result artifacts.
