# Minecraft Common-Protocol Adapter

Issue: #227

`benchmarks.minecraft.adapter.MinecraftBenchmarkMetadataAdapter` is a read-only metadata adapter for Minecraft/VillagerBench artifacts. It does not replace `benchmarks.minecraft.experiment`, does not execute the controller, and does not mutate `VillagerBench` state.

The adapter exposes common protocol dataclasses where they are useful:

- `EpisodeContext` for benchmark name, episode id, seed, agent ids, and sanitized task metadata.
- `AgentCapabilities` from public action-log action types.
- `ObservationRecord` from sanitized action-log entries visible to the requested agent.
- `DecisionContext` with sanitized observations, task metadata, and legal action specs.
- Final numeric metrics for report-facing consumers after a run.

Agent-facing adapter outputs drop credentials, underscore-prefixed fields, evaluator progress/score fields, artifact summaries, hidden state, and private observations from other agents. Other-agent action-log entries are visible only when they are explicitly public or are `talkTo` messages involving the requested agent.

Use `MinecraftBenchmarkMetadataAdapter.from_run_dir(path)` to inspect artifacts produced by `benchmarks.minecraft.experiment` or construct it directly from sanitized launch config and action-log data in tests.
