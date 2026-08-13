# EAC Runtime Authority v1

Status: implementation mapping for frozen `eac-semantics/1` (Issues #509 and
#510). This document describes the #509 shared runtime authority boundary; it
does not change the frozen EAC-1..8 contract, SupportPolicy v1, SourceProfile
v1, benchmark semantics, or the #507 run envelope.

## 1. Authority responsibility and trust boundary

The Runtime Authority is the canonical in-process authority for actor-visible
epistemic state, witness evaluation, candidate/permit lifecycle, dependency
freshness, and the final EAC gate. It is the reference implementation and
reference trust boundary for v1. Planner, controller, evaluator, projections,
visualizers, and ordinary task-DAG stores are clients or derived views; their
labels, confidence values, legality values, and serialized artifacts are not
authority and cannot authorize an effect.

The reference authority is deliberately in-process. A caller submits an exact
action request and receives an evaluation, permit, or rejection from the same
authority instance that serializes relevant mutations. The caller must not
modify authority records, forge permits, select a different dependency
manifest, or call a native effect while bypassing the authority protocol.
Integrations that cannot establish this boundary are outside the v1 Authority
claim and must be disclosed rather than silently treated as compliant.

## 2. Frozen EAC-1..8 implementation mapping

Each mapping is an implementation obligation, not a new semantic rule.

| Frozen invariant | Runtime Authority mapping | Required observable check |
|---|---|---|
| **EAC-1** declared support sufficiency | Evaluate every classified `EPre`; construct a finite valid witness for each proposition before marking a candidate admissible or issuing a permit. | Missing, invisible, invalid, or ungrounded support produces no Authority permit. |
| **EAC-2** conflict/defeat safety | Store conflict/defeat and absence watches in the dependency manifest; relevant mutation marks dependent candidates/permits blocked, stale, or revoked. | A current policy-blocking defeater rejects execution. |
| **EAC-3** grounding/provenance | Persist root, derivation-rule, edge, provenance, visibility, and revision identities needed to reconstruct every finite witness path. | Derived-only loops and self-support fail; the audit can replay root-reaching paths. |
| **EAC-4** invalidation closure | Route canonical mutations through shared-store hooks and perform a final dependency freshness check immediately before effect admission. | A relevant mutation invalidates its dependents; unrelated mutation does not. |
| **EAC-5** non-bypassability | Require a consumed, fresh permit at the lowest supported effect gateway; planner/controller output is never an effect authorization. | Every inventoried supported entry point rejects an unpermitted Authority effect. |
| **EAC-6** permit freshness | Bind the complete canonical dependency manifest and authority epoch to a single-use permit; validate and consume atomically. | Stale, replayed, substituted, or mismatched requests cannot cross the gateway. |
| **EAC-7** scope/visibility safety | Evaluate witnesses against authoritative actor scope and reject evaluator-only or other-private dependencies. | Witnesses contain only dependencies visible under the actor/runtime scope. |
| **EAC-8** precondition-class integrity | Record `EPre`, `EnvPre`, and `SecPre` as independently classified and evaluated layers, preserving overlap. | Native legality or security success never substitutes for epistemic admissibility. |

The source of truth for definitions, validity predicates, lifecycle states, and
oracle wording remains `docs/eac/eac_semantics_v1.md` and its versioned JSON
artifacts.

## 3. Shared store protocol and hooks

The shared store owns canonical records and the serialized authority epoch. Its
protocol exposes operations equivalent to:

1. ingest actor-visible evidence, claims, conflicts, supersession, invalidation,
   scope/policy/profile changes, and action definitions;
2. evaluate a candidate and return witnesses, reasons, recoveries, and a
   canonical dependency manifest;
3. issue at most one fresh permit for a candidate version;
4. `validate_and_consume(exact_request, permit)` atomically validate freshness,
   exact request identity, and single-use state, then return a fencing token;
5. record independent `EnvPre`/`SecPre` results, effect admission, and exactly
   one terminal outcome; and
6. expose bounded, sanitized audit records and immutable historical attempts.

Mutation hooks must update the authority epoch, identify affected dependency
manifests, eagerly mark known dependents stale/revoked where feasible, and
notify subscribers without allowing subscribers to become a second authority.
Every effect path repeats the freshness/fence check even when eager
invalidation has run. Hooks must preserve stable proposition identity and use
explicit supersession/invalidation semantics; hidden evaluator truth changes
are not authority mutations.

## 4. SourceProfile and executable-rule registry

Every executable action family must register a versioned, independently
auditable `SourceProfile` and action classification before it can be used by
Authority. The registry entry names the profile ID/version, detached canonical
content digest, integrity contract, actor-visible source mappings, authorized
issuers, tool bindings, ordered supersession streams, derivation rules, explicit
aging predicates, and the action family's declared `EPre`/`EnvPre`/`SecPre`.
Unknown, missing, ambiguous, or invalid rules fail closed. Heuristic mapping,
an `executable` label, native legality, or a current record cannot satisfy this
obligation. The profile identity/version/digest is included in every witness
and permit dependency manifest and must match between Advisory and Authority.

## 5. Dependency manifest, locking, and fencing

The manifest is canonical RFC 8785 JSON (duplicate keys rejected) and is
fingerprinted with SHA-256. It binds, at minimum: immutable candidate/attempt
identity; action definition/version and semantics digest; exact concrete
effect-bearing request and target; actor and scope revision; classified EPre
and proposition identities; every witness root, provenance dependency,
derivation rule/edge, revision, support and defeat/absence watch; SupportPolicy
identity/version/digest; SourceProfile identity/version/digest; intentionally
bound environment/capability dependencies; and authority epoch/nonces needed
to prevent ABA replacement and replay.

`validate_and_consume` is the linearization point. Under the shared authority
lock (or an equivalent serialized compare-and-swap domain), it compares the
exact request and complete manifest against one epoch and changes the permit
from `issued` to `consumed` once. It returns an unforgeable single-attempt
fencing token. Relevant mutation and permit CAS share this epoch domain. The
effect gateway rejects missing, old, replayed, or mismatched tokens, including
tokens invalidated after the initial validation.

## 6. Effect gateway and independent checks

Authority effects flow through the lowest common supported effect gateway:

```text
exact request -> validate_and_consume -> EnvPre/SecPre -> final fence check
             -> native effect -> visible outcome / effect_unknown
```

`EnvPre` and `SecPre` run after permit consumption and remain independent. A
pre-effect rejection consumes the permit and requires reevaluation for retry.
The gateway records effect admission before the native call and records
success, known failure, or `effect_unknown`; an uncertain effect must not be
blindly retried. All supported controllers, registered tools, direct action
methods, and bridge endpoints must be inventoried and either mediated here or
explicitly excluded from the Authority claim.

## 7. Advisory/Authority equivalence and invalidation

Advisory and Authority use the same immutable run mode inputs, actor scope,
candidate generation, classifications, evidence, witness construction,
SupportPolicy bytes, SourceProfile bytes, dependency manifests, and freshness
calculation. Both execute the same pre-gate. Advisory records the shadow result
(`would_block`, stale status, and the exact request) and may bypass only the
final epistemic permit enforcement, labeled `advisory-only`. Authority requires
the fresh single-use permit; it never falls back to Advisory.

Relevant support removal, supersession, invalidation or revision; new blocking
defeat; visibility, action, EPre, policy, profile, or intentionally bound
capability changes invalidate dependent candidates/permits. Dependency-scoped
irrelevance preserves unaffected permits. Eager hook propagation is an
optimization; the final gateway check is mandatory.

## 8. Bounded audit and boundary with #507

Audit records are bounded, structured, and append-oriented: IDs and versions,
mode, exact request digest, classification and policy/profile identities,
witness/dependency fingerprint, reasons/recoveries, invalidation cause,
linearization epoch/fence, independent precheck results, effect admission, and
terminal outcome. Large evidence payloads are referenced by stable IDs and
bounded summaries; audit output is a sanitized projection, not a writable
authority store. Historical attempts remain immutable.

Issue **#507** owns the outer run envelope: run ownership/lifecycle, namespace,
managed-Docker ownership, child supervision, cleanup, and reproducibility. #509
owns per-action EAC admissibility, dependency freshness, permits, and the effect
gate inside that supervised runtime. #507 does not evaluate EPre, validate
witnesses, or issue permits; a #507-supervised runtime is not thereby Authority.

## 9. Known limitations

Version 1 provides no environment integrations, no out-of-process fencing, and
no durability guarantee. It cannot discover omitted `EPre`, infer hidden world
changes, repair a misconfigured visibility/source profile, or claim mediation of
unsupported direct-import or bridge paths. Crash recovery and `effect_unknown`
reconciliation therefore require an integration-specific boundary and must not
be represented as stronger runtime authority than this document supports.

The reference in-memory authority owns canonical mutations. A custom
`AuthorityStateReader` is conforming only when its immutable snapshot includes
every dependency revision and its store serializes those revisions with
Authority mutation hooks; otherwise it is a read-only compatibility fixture and
cannot support an Authority claim. The in-process gateway holds the authority
mutation lock while crossing into the native callable. This is intentionally
conservative and does not generalize to remote processes.
