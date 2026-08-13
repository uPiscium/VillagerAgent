# Minecraft precondition classification v1

The normative artifact is `minecraft_preconditions_v1.json`. It is prospective,
versioned, and identical in Advisory and Authority. Classification follows the
counterfactual actor-reliance rule in `eac-semantics/1`, not whether Mineflayer
can technically check a condition.

The primary reviewed mutation family is `MineBlock`. Its stable
`minecraft.target_block_present(x,y,z)` proposition is deliberately
dual-classified:

- **EPre:** an actor lacking an admissible visible observation of the target
  must not rely on the target being the intended block;
- **EnvPre:** the target must still be legal/reachable/mineable at native effect
  time.

For other families the artifact records distinct stable `env_preconditions`
condition identities (for example target replaceability, item availability,
and recipient availability) rather than relabeling actor-visible observation
as native legality. Observation and communication actions are
evidence-producing but do not promote peer reports to world-state truth.
Unlisted tools are not part of the Minecraft EAC Authority claim.
