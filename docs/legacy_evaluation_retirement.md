# Legacy Evaluation Retirement Record

On 2026-07-15, the repository owner confirmed that no source bundle or backup
could be recovered for the fixed pre-publication-policy evaluation inventory.
Aggregate values copied into the repository cannot reconstruct attempt IDs,
manifests, resolved configurations, per-run statuses, or provenance. They must
not be used to fabricate replacement archives.

The exhausted inventory is:

| Result ID | Expected runs | Disposition |
| --- | ---: | --- |
| `cwah-goal-policy-diagnostics` | 66 | Permanently retired; all runs missing |
| `cwah-bounded-baseline-diagnostic` | 6 | Permanently retired; all runs missing |
| `craft-qwen-final-diagnostic` | 4 | Permanently retired; all runs missing |
| `craft-dual-dag-ablation-diagnostic` | 21 | Permanently retired; all runs missing |
| `craft-v5-action-selection-diagnostic` | 3 | Permanently retired; all runs missing |
| `craft-clarification-policy-evaluations` | 78 | Permanently retired; all runs missing |

All 178 expected runs remain explicitly accounted as missing. These entries are
historical inventory only, with `publication_satisfied=false`,
`claim_eligible=false`, and `paper_facing=false`. Their reports preserve context
but are not benchmark or paper evidence. Any future rerun is a new evaluation
with a new result ID, provenance, manifest, and immutable archive; it does not
recover or replace the historical bytes.

Issue #297 is resolved by an owner-approved permanent-retirement disposition,
not by satisfying archival recovery for these six records. The publication
workflow, archive validation, and immutable-reference requirements remain
mandatory for every new benchmark-facing or paper-facing result.
