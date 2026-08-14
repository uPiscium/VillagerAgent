# Minecraft EAC runtime v1

Minecraft adapters feed actor-visible records and classified candidates into
the merged `benchmarks.common.eac.RuntimeAuthority`. They do not reimplement
witness, SupportPolicy, permit, replay, or fencing semantics.

The runtime mode is immutable: `dual_dag_advisory` and
`dual_dag_authority` use identical evidence, classification, frozen
`eac-primary-support` v1 bytes, SourceProfile bytes, witnesses, EAdm, manifests,
and candidate generation. Only the final epistemic enforcement differs.

`RuntimeTaskDAGStore` remains task dependency/lifecycle authority. The
Minecraft Dual-DAG artifact and the EAC audit artifact are sanitized read-only
projections and cannot mutate either authority.

Actor evidence is ingested at runtime from sanitized observations, trusted
visible tool results, visible action outcomes, peer reports, and explicit
visible supersession/invalidation. Score, progress, evaluator snapshots,
meta-judger private state, simulator-only truth, and post-hoc judged artifacts
are rejected as evidence origins. Hidden world change without actor-visible
evidence does not mutate witness freshness.

The real `VillagerBench.get_init_state()` block snapshot returned to
`DataManager` is sanitized by the authenticated observation adapter and
ingested actor-by-actor into the same current-fluent stream. Positive visible
block observations and negative successful MineBlock outcomes supersede the
previous polarity for that actor and proposition key while retaining the old
root as non-current audit history; a later visible replacement can therefore
restore admissibility.

The Authority claim covers classified tools registered through an
EAC-configured `VillagerBench`. Direct Python calls to `Agent`, direct bridge
HTTP calls, bridge internals, judgers, setup/admin paths, and unclassified tools
are excluded. The bridge and native callable remain trusted in-process/network
capabilities; this is not an OS or hostile-plugin sandbox.

The non-judged execution fixture is
`docs/eac/minecraft_eac_nonjudged_fixture_v1.json`. It binds the independently
verifiable `docs/eac/minecraft_eac_premanifest_v1.json` and immutable Git
revision `eb1e771ccc7a1d3043bd203a8bd0c4598809e0a7`. The premanifest was generated
from `RuntimeExecution.resolve()` over that detached revision (152 assets;
manifest `506f3f5fe29b9ac4e7d200f095dfd33ac8ca47e91e0181254876982872af005e`). Both values are carried from
the launch configuration to startup; ambient environment variables cannot
select a different admitted revision. This fixture is an integration/admission
artifact only and does not authorize judged, Gate A/B/C, or production runs.
The Advisory fixture binds the same premanifest and enters the same startup
path; only final permit enforcement differs.
