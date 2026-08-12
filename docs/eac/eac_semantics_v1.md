# Epistemic–Action Consistency Semantic Contract, Version 1

Status: **frozen specification for Issues #509, #510, and #511**  
Parent: [#492](https://github.com/upiscium/VillagerAgent/issues/492)  
Semantic version: `eac-semantics/1`  
Primary support policy: [`eac-primary-support` version 1](support_policy_v1.json), validated by [`support_policy_schema_v1.json`](support_policy_schema_v1.json)

This document specifies semantics and test oracles. It does not implement Runtime
Authority, change benchmark semantics, or authorize an experiment.

## 1. Position and excluded claims

**EAC is a KoP-inspired runtime enforcement contract over declared epistemic
preconditions.** The Knowledge of Preconditions (KoP) principle motivates the
relationship between information and action, but this repository does not claim
to implement the formal runs-and-systems theorem or to originate that principle.

For actor `i`, authoritative actor-visible runtime state `r`, and proposition
`phi`, `J_i^r(phi)` denotes a runtime-admissible justification witness:

```text
J_i^r(phi) != K_i phi
```

EAC does not decide logical or factive knowledge; guarantee truth from
provenance; discover every necessary epistemic precondition; replace native
environment legality; or replace security, governance, role, permission, or
user-intent authorization. A valid witness can support a false proposition
because observations, tools, communication, extraction, and memory are
fallible. Hidden evaluator truth is not actor-visible authority.

The research object is exactly:

```text
declared EPre + fallible provenance-bearing evidence + runtime enforcement
```

The terminology follows Moses's KoP account [1], without adopting its theorem as
an implementation claim. Provenance follows the W3C PROV distinction between a
record of origin/derivation and an assurance of truth or trustworthiness [2].

## 2. Preconditions and their source

For action identity/version `a` and actor `i`:

```text
Pre(a) = EPre_i(a) union EnvPre(a) union SecPre(a)
```

- `EPre_i(a)` contains propositions on which the action specification requires
  actor `i` to rely epistemically when selecting or committing to `a`.
- `EnvPre(a)` contains objective effect-time conditions required for the native
  effect to be legal, defined, or valid.
- `SecPre(a)` contains permission, role, security, governance, and user-intent
  authorization conditions.

The sets may overlap. In particular, `EPre_i(a) intersect EnvPre(a)` is valid.
Passing one class never implies passing another.

### 2.1 Classification rule

Classification is semantic, not based on technical checkability.

**Epistemic-reliance test.** Assume `phi` is objectively true while actor `i`
has no admissible justification for it. If the action specification says the
actor must not proceed, `phi` is in `EPre_i(a)`.

**Environment-legality test.** Assume actor `i` has admissible justification for
`phi` while `phi` is objectively false at effect time. If the native effect must
reject or fail, `phi` is in `EnvPre(a)`.

If both tests hold, classify `phi` in both sets and audit both decisions
independently. Native checkability, membership in a legal-action list, physical
verification, or successful execution cannot erase an `EPre` obligation. An
environment result becomes epistemic evidence only through an explicit,
actor-visible evidence event admitted by the support policy.

Every Authority action family must refer to an independently auditable,
versioned classification definition. Missing classification fails closed.
Primary controlled benchmarks use fixture/schema-defined `EPre`; unconstrained
LLM discovery is a separate experiment. EAC guarantees only declared `EPre`, not
its completeness.

### 2.2 Required examples

| Class | Example and counterfactual result |
|---|---|
| `EPre` only | A coordinator sends a status summary whose specification requires it to identify the latest actor-visible report. The report may be stale in objective reality without making message emission natively illegal, but sending without admissible support violates required epistemic reliance. |
| `EnvPre` only | A best-effort telemetry sample may be requested without prior justification that the sensor is available; native invocation must still reject if the capability handle is absent at effect time. |
| `EPre intersect EnvPre` | Before placing a Minecraft block, an actor must be justified that the target is replaceable **and** the native environment must independently confirm replaceability at effect time. Missing witness blocks Authority even if placement would succeed; later occupancy makes the effect fail even with a valid witness. |
| `SecPre` | Deleting a shared artifact requires an authenticated role and user-granted deletion authority. Evidence that deletion is useful is neither the role nor user consent. |

## 3. Epistemic entities and justification witnesses

An implementation must preserve stable identities/versions for propositions
(including polarity and relevant scope), evidence, origins, actors, visibility,
support and derivation edges, defeat/conflict relations, supersession and
invalidation events, action definitions, `EPre` definitions, and policies.
Terms such as `reported`, `supported`, `contested`, `defeated`, `superseded`, and
`invalidated` do not imply objective truth.

A witness is a finite, auditable subgraph containing the claimed proposition,
evidence roots, derivation rules/edges, provenance, visibility, support
dependencies, conflict/defeat absence dependencies, and relevant versions.

```text
Valid_i^r(J, phi)
  = Scoped_i(J)
    AND Grounded(J)
    AND Fresh_r(J)
    AND NonDefeated_r(J)
    AND Supports(J, phi)
```

- `Scoped_i`: every dependency is available to actor `i` under the authoritative
  runtime visibility/scope contract. Evaluator-only state is forbidden.
- `Grounded`: every support branch is finite and reaches a policy-allowed
  evidence root. Self-support, unsupported cycles, and loops closed only by
  derived claims are invalid. A graph being acyclic is not sufficient.
- `Fresh_r`: all dependencies remain current in actor-visible authority under
  explicit supersession/freshness policy. Hidden world changes alone do not
  change freshness.
- `NonDefeated_r`: no current unresolved blocking conflict or defeat selected by
  policy applies. The dependency manifest must watch for introduction of a
  relevant defeater, not merely record existing positive support.
- `Supports`: explicit policy-allowed paths connect roots to `phi`; provenance
  alone is not support.

## 4. Frozen SupportPolicy v1

The normative machine-readable policy is [`support_policy_v1.json`](support_policy_v1.json):

```text
policy_id = eac-primary-support
policy_version = 1
```

Only actor-visible, current, non-defeated `direct_observation`,
`trusted_tool_result`, and `visible_action_outcome` roots are individually
sufficient. An `unverified_peer_report` alone is insufficient for a world-state
`EPre`; it may create a claim, trigger evidence gathering or clarification, or
be corroborated. Peer reports participate in a witness only when independently
supported by a sufficient root. Repetition from one source is not independent,
and peer-report-only multiplicity is never automatically promoted to truth.

Derived claims require a finite explicit allowed derivation whose every premise
grounds in allowed roots. A current unresolved blocking contradiction for the
same relevant proposition makes `NonDefeated` false. Explicit newer
actor-visible observations, tool results, or events may declare supersession of
older evidence for a tracked proposition; superseded evidence is excluded.

Version 1 has no unexplained wall-clock TTL. Freshness is lost only through
explicit invalidation/supersession, relevant scope or policy change, evidence
revision, or an adapter-defined explicit aging event. Hidden world change is
not omniscient invalidation.

The policy is immutable in confirmatory experiments and identical in Advisory
and Authority. Scenario-specific changes, outcome-informed threshold changes,
or mode-specific policies are forbidden. Any semantic change requires a new
policy version.

Canonical policy and dependency-manifest bytes use RFC 8785 JSON Canonicalization
Scheme (JCS) [3], rejecting duplicate object keys before canonicalization, and
SHA-256. Whitespace in the checked-in presentation is not semantic. The
canonical policy digest is pinned by repository static validation.
For the checked-in v1 value it is
`sha256:685b9e70976ea832f8e7d47d244d8cca4d510ef08b3d04c7c2557d56587e8ca6`.

Environment mapping is a separate frozen `SourceProfile` semantic dependency
conforming to [`source_profile_schema_v1.json`](source_profile_schema_v1.json),
not adapter discretion. It maps raw records to at most one root type and declares
tool trust, visibility, source/upstream lineage, ordered supersession streams,
authorized issuers, derivation rules, and explicit aging predicates. Its ID,
version, and detached canonical content digest are witness and permit dependencies and
must match across Advisory and Authority. Representative future mappings are:
CRAFT private views to actor-private `direct_observation`; C-WAH local
`ObservationRecord` values to direct observations and public messages to
`unverified_peer_report`; Minecraft tool results/outcomes only where #510
declares the exact trusted tool/event and actor-visible publication path.
Current records are not grandfathered as sufficient roots.
Mapping rules are matched by exact record namespace/type; the lowest unique
numeric priority wins. No match, a priority tie, absent lineage/upstream/
visibility data, an unknown tool/issuer/stream, or an invalid revision fails
closed. Issuer authentication and rule evaluation follow the versioned integrity
contract named by the profile; no heuristic fallback is permitted.
`detached_profile_sha256` is computed over RFC 8785 canonical profile content
after removing only the top-level `detached_profile_sha256` member, avoiding
self-reference. No nested integrity-contract field is excluded. A `trusted_tool_result`
mapping must name an exact tool identity/version present in `trusted_tools`, and
the proposition namespace must be allowed by that entry; all other root mappings
must set both tool-binding fields to null. Cross-reference failure is invalid.

## 5. Epistemic admissibility

```text
EAdm_i(a, r)
iff
for every phi in EPre_i(a),
there exists J_i^r(phi) such that Valid_i^r(J, phi)
```

`EAdm` is not objective truth, executable environment legality, or security
permission. An empty declared `EPre` is vacuously admissible only when the
versioned classification explicitly declares it empty; absence of a
classification is an error.

## 6. Evaluation and execution permits

The conceptual interface is:

```text
EvaluateEPre(actor, action, authoritative_state, support_policy)
  -> admissibility + witnesses + dependency_set + reasons + recoveries

IssuePermit(action, admissibility, dependency_fingerprint)
  -> single-use execution_permit

Execute(action, execution_permit)
  -> effect attempted only after atomic freshness validation
```

A permit certifies declared epistemic admissibility against specific dependency
state. It is not security authorization. Freshness validation and single-use
transition to `executing` must be atomic with respect to relevant authoritative
mutations; no stale or concurrently replayed permit may cross the supported
effect boundary.

### 6.1 Dependency manifest and fingerprint

The canonical manifest must bind semantic content and versions, not untrusted
labels alone:

- immutable candidate/attempt identity, action definition/version and semantics
  digest, and canonical concrete effect-bearing request arguments/target exactly
  as presented to the executor;
- actor identity and visibility/scope revision;
- declared `EPre` definition identity/version/content digest and proposition
  identities;
- every witness root, provenance dependency, derivation edge/rule, and revision;
- support, supersession, freshness, and relevant conflict/defeat absence watches;
- canonical SupportPolicy identity/version/content digest;
- canonical SourceProfile identity/version/content digest;
- relevant intentionally bound `EnvPre` or capability dependency;
- authority epoch/nonces sufficient to prevent ABA replacement and replay.

RFC 8785 JCS encoding and SHA-256 produce the dependency fingerprint.
An implementation may use finer-grained versions internally but must be able to
show this manifest. A global DAG revision alone is insufficient.

### 6.2 Invalidation and propagation

A dependent witness/permit loses freshness when supporting evidence is removed,
superseded, invalidated, or revised; a blocking conflict/defeat is introduced;
relevant visibility changes; action or declared `EPre` content/version changes;
SupportPolicy changes; or an intentionally bound environment/capability
dependency changes. Unrelated epistemic mutation does not invalidate a permit
when dependency-scoped irrelevance is proven. Evaluator-only truth changes do
not directly mutate Authority state.

Propagation is **hybrid**: canonical mutation should eagerly mark known affected
candidates and permits stale/revoked where feasible, while every effect path
must validate freshness immediately before effect. Lazy propagation is
conforming only if that final rejection occurs.

```text
No stale permit may cross the supported effect boundary.
```

## 7. Lifecycle and recovery

Candidate admissibility, permit lifecycle, execution attempt, and outcome
certainty are distinct records. The combined view below is a lossless projection
of those dimensions. Historical execution records are immutable. At most one
fresh permit exists per candidate version; issuance revokes an earlier unused
permit. Each retry creates a new attempt identity and requires a new permit.

Normative dimensions are: candidate `{proposed, waiting_for_evidence,
blocked_conflict, blocked_precondition, epistemically_admissible}`; permit
`{none, issued, stale, revoked, consumed}`; attempt `{none, precheck,
effect_admitted, completed}`; outcome `{none, pre_effect_rejected, succeeded,
effect_failed, effect_unknown}`; and enforcement `{authority, advisory_bypass}`.
Candidate reevaluation mutates
candidate plus (when needed) permit to stale/revoked atomically. Compare-and-
consume mutates permit `issued -> consumed` and attempt `none -> precheck`
atomically. Effect admission mutates attempt `precheck -> effect_admitted`;
completion mutates it to `completed` and sets exactly one outcome. `executing`,
`succeeded`, and `failed` below are display projections, not additional stores.
An `issued` permit requires an admissible candidate; an effect-admitted Authority
attempt requires a consumed permit; Advisory bypass requires no permit, must
carry the shadow non-admissibility result, and independently records the actual
terminal outcome.

| Source | Event | Guard | Target | Permit consequence | Required audit event |
|---|---|---|---|---|---|
| — | candidate created | versioned classification exists | `proposed` | none | proposal + definition identities |
| `proposed` | evaluation lacks support | no blocking conflict | `waiting_for_evidence` | none | missing `EPre`, reasons, recoveries |
| `proposed` / admissible | blocking conflict introduced | current unresolved policy-blocking conflict | `blocked_conflict` | current permit revoked/stale | conflict dependencies |
| any pre-execution candidate | classification/evaluation fails | malformed/missing definition or invalid witness not merely absent | `blocked_precondition` | none or revoked | failed validity dimension |
| waiting/blocked/proposed | evaluation succeeds | all declared `EPre` have valid witnesses | `epistemically_admissible` | eligible to issue | witnesses + dependency manifest |
| `epistemically_admissible` | permit issued | current fingerprint and no prior permit reuse | `permit_issued` | fresh, single-use | permit ID/fingerprint/mode |
| `permit_issued` | relevant dependency changes | dependency match affected | `permit_stale` | unusable | invalidating dependency |
| `permit_issued` | explicit administrative or candidate withdrawal | before effect | `permit_revoked` | unusable | revocation reason |
| `permit_issued` | execute requested | exact request matches; atomic freshness and compare-and-consume succeed | `permit_consumed` | consumed once | linearization record and fencing token |
| `permit_consumed` | native/security precheck rejects | no effect admitted | `failed` (`pre_effect_rejected`) | consumed; retry needs new permit | independent `EnvPre`/`SecPre` result |
| `permit_consumed` | prechecks pass and adapter admits effect | fencing token accepted | `executing` (`effect_started`) | consumed | effect-admission record |
| non-admissible/blocked | Advisory execute requested | run mode is immutably Advisory | `executing` with enforcement=`advisory_bypass` | no Authority permit; shadow result retained | `advisory-only`, `would_block`, exact request |
| `executing` | effect confirmed successful | outcome observed | `succeeded` | remains consumed | visible outcome/provenance |
| `executing` | effect confirmed failed | known result | `failed` (`effect_failed`) | remains consumed | native outcome |
| `executing` | crash/timeout leaves effect uncertain | effect may have started | `failed` (`effect_unknown`) | remains consumed; no blind retry | reconciliation requirement |
| stale/revoked/blocked | reevaluation succeeds | new current dependencies | `epistemically_admissible` | requires a new permit ID | reevaluation linkage |

The recovery classes are explicit products of non-admissibility, not evidence
that an LLM happened to reconsider:

- `request_clarification`: ask a relevant visible source about ambiguity;
- `gather_observation`: obtain an allowed direct observation or tool result;
- `wait_for_evidence`: await a declared event/source without busy execution;
- `communicate_evidence`: share actor-visible evidence under scope rules;
- `choose_alternative`: select a candidate whose declared requirements differ;
- `replan`: revise the action/task plan while preserving audit provenance.

### 7.1 Execution linearization and fencing

`validate_and_consume(exact_request, permit)` is the linearization point. It
compares the exact request and complete dependency manifest against one authority
epoch, consumes the permit by CAS, and returns an unforgeable single-attempt
fencing token. Relevant mutations and this CAS share one serialized authority
epoch/version domain. The lowest supported effect gateway rejects missing,
stale, replayed, mismatched, or older tokens before effect admission.
Out-of-process adapters carry the token to that gateway; planner/controller-only
checking is non-conforming.

After consumption, `EnvPre` and `SecPre` are checked independently. Rejection is
consumed/no-effect and retry requires reevaluation and a new permit. Immediately
before effect admission the epoch/fence is checked again; intervening relevant
mutation prevents effect. A crash after admission is `effect_unknown` until
reconciled and must not be blindly retried.

## 8. Advisory and Authority ablation

Both modes use identical evidence, actor scope, action/task semantics, declared
and classified preconditions, SupportPolicy v1 bytes, witness construction and
validity, `EAdm`, candidate generation, dependency manifests, and freshness
computation.

**Advisory** records the same pre-gate result (including shadow permits,
`would_block`, and stale status) but intentionally does not enforce the final
epistemic permit gate. Every bypass is labeled `advisory-only`. **Authority**
requires a fresh single-use permit on every supported effect path. Mode is fixed
for the run; there is no Authority-to-Advisory fallback. Enforcement is the only
controlled difference.

## 9. Normative invariants

| ID | Statement | Scope | Assumptions | Observable guarantee | Direct test oracle | Known limitation |
|---|---|---|---|---|---|---|
| EAC-1 | Declared support sufficiency: every declared `EPre` has a valid witness before `EAdm`. | All evaluated candidates | Complete input declaration is not guaranteed | Missing/invalid witness yields non-admissible | Remove one required witness; no Authority permit is issued | Cannot enforce omitted `EPre` |
| EAC-2 | Conflict/defeat safety: unresolved policy-blocking conflict defeats dependent witness. | Relevant proposition dependencies | Conflicts are represented in actor-visible authority | Candidate blocks and existing dependent permit stales/revokes | Introduce a current contradictory root after permit issuance; execution is rejected | Policy can be fallible or incomplete |
| EAC-3 | Witness grounding/provenance: each permit requirement has a finite explicit path to allowed roots. | Permit-bearing actions | Root and derivation identities are retained | Audit reconstructs all paths and provenance | Self-cycle or derived-only loop fails `Grounded`; finite allowed path passes | Provenance does not prove truth |
| EAC-4 | Invalidation closure: every relevant validity loss reaches dependent candidate/permit before effect. | Authoritative mutations and supported effects | Dependency and absence watches are complete for declared semantics | Relevant mutation stales/revokes; unrelated mutation does not | Mutate one support and one unrelated claim; only dependent permit rejects | Undeclared dependency cannot be tracked |
| EAC-5 | Runtime non-bypassability: Authority planner/controller output cannot directly effect a supported action. | Documented Authority effect paths | Integration enumerates and mediates every supported path | Each effect has a consumed fresh permit record | Attempt each effect entry point without a permit; all reject before effect | Unsupported/out-of-process paths are outside claim and must be disclosed |
| EAC-6 | Permit freshness: permit binds action, `EPre`, witness, scope, policy, defeat watches, and selected capability state. | Permit issue through effect boundary | Atomic compare-and-consume | Stale/replayed permit never crosses effect boundary | Change each dependency class and race/replay execution; reject atomically | Cannot infer hidden world changes |
| EAC-7 | Scope/visibility safety: invisible evidence cannot satisfy actor `EPre`. | Witness construction and evaluation | Visibility authority is correct | Witness contains only actor/runtime-visible dependencies | Substitute evaluator-only or other-private evidence; witness is invalid | Visibility metadata itself may be misconfigured |
| EAC-8 | Precondition-class integrity: epistemic, native legality, and security decisions remain independent; overlap is preserved. | Classification, evaluation, and effect audit | Versioned action definitions | Results for each applicable layer are separately observable | Exercise all truth/justification combinations for an overlap; neither layer substitutes | Classification completeness remains external |

## 10. Direct oracle matrix

| Case | Expected result |
|---|---|
| Missing `EPre` witness | non-admissible |
| Objectively true but actor-unjustified `EPre` | non-admissible |
| `EPre intersect EnvPre` | both layers checked independently |
| Ungrounded self-support | invalid witness |
| Blocking conflict | non-admissible |
| Support invalidated | dependent permit stale/revoked |
| Unrelated mutation | unaffected permit remains valid |
| SupportPolicy revision | affected permit stale |
| `EPre` definition revision | affected permit stale |
| Hidden evaluator truth change | no automatic Authority mutation |
| Actor-invisible evidence | cannot satisfy `EPre` |
| Stale permit execution | rejected before effect |
| `EnvPre` success but `EPre` missing | no Authority permit |
| Advisory vs Authority | identical pre-gate state; only Authority enforces |
| Same-source repeated peer reports | no independent corroboration |
| Concurrent permit replay | at most one atomic transition to effect |
| Exact-request substitution | permit rejected before effect |
| Valid actor-visible witness for objectively false `phi` | epistemically admissible; truth/`EnvPre` remains separate |
| Root-reaching path with missing provenance | invalid witness |

## 11. Existing repository correspondence

Graph presence or an `executable` label is not evidence of EAC compliance.

| Existing concept | EAC concept | Assessment |
|---|---|---|
| CRAFT `DualDAGRuntime.epistemic_nodes/edges` and private/public ingestion | actor-visible epistemic authority and provenance | **Reusable/partial**: canonical in CRAFT and visibility-aware; not SupportPolicy-v1 witness authority |
| CRAFT action candidates, support/conflict/required evidence, lifecycle | action candidates and recovery | **Reusable/partial**: consumed by Builder ordering/clarification, but confidence/physical verification/current `executable` do not equal `EAdm` |
| CRAFT serialized/task mappings and artifacts | audit projection | **Reusable** as sanitized projection; not a new permit boundary |
| C-WAH `CWAHDualDAGRuntime.update_observations` and public messages | actor-visible roots/reports | **Partial**: visibility and grounding fields exist; no witness/corroboration/supersession authority |
| C-WAH candidate precondition status and `currently_legal` | candidate plus `EnvPre` projection | **Partial**: native legality must not substitute for `EPre` |
| C-WAH `DecisionContext` and `record_action_outcome` | planner context and visible outcome | **Reusable/partial**: sanitized context and outcome metadata; no admissibility/permit/invalidation lifecycle |
| Minecraft `RuntimeTaskDAGStore` | canonical task dependency/lifecycle authority | **Reusable** for tasks only; explicitly not epistemic/action-permit authority |
| Minecraft controller assignment and tool registration/barrier | scheduling and effect integration points | **Partial**: assignment/tool constraints exist; no canonical EAC gate |
| `runtime_dual_dag_snapshot.json` | runtime audit state | **Reusable** only as task-DAG snapshot despite legacy filename |
| `dual_dag_artifact.json`, `decision_support.json` | read-only EAC projection candidates | **Reusable** for post-hoc analysis only; never runtime authority |
| Minecraft action log and tool-specific checks | provenance and `EnvPre` evidence | **Partial**: action records/selected checks exist; authoritative action lifecycle and permits are missing |

### 11.1 EAC-1 through EAC-8 status

`Existing` means the current code directly provides the stated limited semantic
facility, not complete EAC conformance.

| Environment | EAC-1 | EAC-2 | EAC-3 | EAC-4 | EAC-5 | EAC-6 | EAC-7 | EAC-8 |
|---|---|---|---|---|---|---|---|---|
| CRAFT | partial | partial | partial | partial | missing | missing | partial | missing |
| C-WAH | partial | missing | partial | missing | missing | missing | existing/partial | partial |
| Minecraft | missing | missing | partial (post-hoc) | missing | missing | missing/unknown (actor evidence) | missing | partial (`EnvPre` checks only) |

CRAFT's in-tree runtime is the audited reference; the `external/CRAFT` submodule
was not initialized during this audit. C-WAH's implementation supersedes a stale
statement in `docs/dual_dag/current_implementation_map.md` that calls its adapter
missing. These qualifications must remain visible until a later implementation
audit updates them.

## 12. Downstream implementation map

- **#509 Shared Runtime Authority:** implement authoritative witness evaluation,
  dependency manifests/fingerprints, atomic single-use permits, hybrid
  invalidation, non-bypassability contracts, lifecycle audit, and common test
  oracles without changing this policy.
- **#510 Minecraft integration:** define audited action-family classifications,
  create actor-visible epistemic/action authority, mediate every supported tool
  effect, preserve independent `EnvPre`/`SecPre` checks, and keep post-hoc
  artifacts projections.
- **#511 experiment:** preregister immutable policy bytes and action definitions;
  compare Advisory/Authority with identical pre-gate state; separately measure
  runtime integrity, epistemic adequacy against evaluator-held truth, and task
  utility.
- **Later CRAFT/C-WAH work:** compatibility-map existing states; do not silently
  reinterpret persisted `executable` or legality fields as permits.

### 12.1 Multi-agent extension boundary

Version 1 evaluates actor-scoped single-action requirements. A coalition witness
is not a union of members' private evidence. A future version must declare the
intended operator—individual, distributed, everyone/shared, or bounded mutual
acknowledgement—and its communication/visibility semantics. Unrestricted common
knowledge is outside version 1.

## 13. Boundary with #507

```text
EAC Runtime Authority
    |
    | fresh execution permit
    v
Minecraft/native executor
    |
    v
#507 execution isolation / reproducibility layer
```

#507 owns namespace and managed-Docker ownership, child supervision, one-shot
lifecycle, cleanup, and reproducibility. It does not evaluate `EPre`, validate
witnesses, decide `EAdm`, or issue execution permits. The operational ordering
is run ownership/supervision, then per-action EAC gate, then independent native
legality/security checks and effect. A frozen runtime supervised by #507 does
not become EAC Authority unless every supported effect path is separately
mediated by #509/#510. #510 therefore requires a new authenticated execution
identity and premanifest; the frozen #507 v4 identity cannot gain EAC mediation
while remaining the same identity.

#510 must inventory every supported Minecraft effect entry point, including
controllers, registered tools, direct `Agent` methods, and bridge endpoints;
mediate supported paths at the lowest common effect gateway; and explicitly
classify excluded direct-import or bridge paths.

## References

1. Yoram Moses, “Relating Knowledge and Coordinated Action: The Knowledge of
   Preconditions Principle,” EPTCS 215 (2016), pp. 231–245,
   <https://doi.org/10.4204/EPTCS.215.17>.
2. W3C, “PROV-DM: The PROV Data Model,” W3C Recommendation, 30 April 2013,
   <https://www.w3.org/TR/2013/REC-prov-dm-20130430/>.
3. A. Rundgren, B. Jordan, and S. Erdtman, “JSON Canonicalization Scheme
   (JCS),” RFC 8785, June 2020, <https://www.rfc-editor.org/rfc/rfc8785>.
