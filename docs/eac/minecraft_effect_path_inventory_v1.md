# Minecraft EAC effect-path inventory v1

This prospective inventory was completed before the Minecraft EAC adapter was
implemented. The Authority claim is deliberately narrower than every callable
in the repository: it covers only tools registered through `VillagerBench` in
an EAC-configured run. The shared `RuntimeAuthority` remains semantic authority;
`RuntimeTaskDAGStore` and `dual_dag_artifact.json` remain task state and a
read-only projection respectively.

| Caller/path | Action identity and effect arguments | Native entry | Existing EnvPre/logging | EAC mediation | Claim |
|---|---|---|---|---|---|
| `BaseAgent -> VillagerBench.step -> Agent.run/step -> registered LangChain tool` | Exact registered tool name and canonical tool kwargs | copied tool's original `func`, then `_minecraft_request` | bridge endpoint performs native Minecraft checks; `timeit` writes action log | `VillagerBench.guard_tool_actions` delegates to `MinecraftEACRuntime.mediate_tool` before the original callable | Included when the tool has a frozen classification entry |
| local-model `BaseAgent.local_step -> registered tool.func` and `iter_step -> Agent.step` | same | same | same | `Agent.step` selects only `self.tools`, the registered/guarded set | Included when classified |
| controller/planner selection | no direct native effect | registered tool path above | controller action barrier only | no planner-only gate is claimed | Included only through the wrapped tool path |
| `Agent` static/direct method call from arbitrary Python | tool kwargs | `_minecraft_request` | bridge checks and action log | not capability-isolated from trusted Python | Explicitly excluded |
| direct HTTP caller to Flask/FastAPI `/post_*` | raw endpoint payload | `env.minecraft_server*` handler -> `env_api`/Mineflayer | endpoint-specific checks/logging | outside Python registered-tool gateway | Explicitly excluded; bridge ports must remain inside the trusted runtime boundary |
| bridge handler / `env_api.py` / Mineflayer callback | endpoint-specific | Mineflayer bot methods | endpoint-specific | no second epistemic authority is constructed | Explicitly excluded as trusted native implementation below the gateway |
| private wrappers `_navigateTo`, `_lookAt` called inside a tool | coordinates/name | `_minecraft_request` | bridge checks | part of one already-mediated compound tool call | Included only as a child effect of that mediated tool; direct calls excluded |
| `VillagerBench.chat -> Agent.run` | free-form command | agent/tool execution | legacy logging | not a classified primary tool | Explicitly excluded and rejected by EAC experiment configuration |
| judgers, world initialization, admin commands, render/setup | task/world/admin payload | subprocess or bridge/Mineflayer | control-plane-specific | outside actor action authority | Explicitly excluded; no EAC non-bypassability claim |
| observation/read-only endpoints (`ping`, environment/status/read/find`) | query args | bridge query | sanitization/logging | evidence adapters may ingest sanitized results after return | No environment-mutation claim; evidence use remains classified |

## Lowest supported gateway

The lowest common supported point available without modifying the frozen bridge
and v4 runtime is the copied registered-tool callable in
`VillagerBench.guard_tool_actions`. In EAC mode it performs:

```text
typed tool kwargs -> Minecraft candidate adapter -> shared RuntimeAuthority
-> shared EffectGateway -> Minecraft EnvPre -> SecPre -> original tool callable
-> sanitized visible outcome ingestion
```

The bridge HTTP API is a trusted native boundary, not a public security
boundary. A caller with direct Python capability access or bridge network access
is outside this in-process Authority claim.

EAC experiment registration exposes only the frozen classified subset. Other
legacy tools remain available to non-EAC runs but are omitted from the EAC
agent tool list rather than represented as mediated Authority paths.
