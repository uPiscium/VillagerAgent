# Optional Minecraft World View Feasibility

## Decision

VillagerAgent Visualizer integrates an externally managed, read-only world viewer by URL. It does not install or start `prismarine-viewer` inside the Python runtime, and it does not attach a second controlling bot automatically.

This split is intentional. As of the reviewed `prismarine-viewer` 1.33.0 documentation, mineflayer mode receives a live bot object and starts its own web server, while standalone mode renders a supplied/generated world rather than automatically mirroring an existing server. Proxy and core APIs can support a dedicated adapter, but require deployment-specific Minecraft/world access. Directly coupling the package to VillagerAgent's agent objects would make visualization failure, Node dependencies, chunk load, and viewer lifecycle part of runtime execution.

Sources reviewed:

- [PrismarineJS/prismarine-viewer](https://github.com/PrismarineJS/prismarine-viewer)
- [prismarine-viewer npm package](https://www.npmjs.com/package/prismarine-viewer)

## Minimal Integration

Start a compatible viewer or deployment-specific read-only adapter separately on an explicit port, then opt in:

```bash
uv run --project visualizer/backend python -m villageragent_visualizer \
  --result-root result \
  --frontend-dist visualizer/frontend/dist \
  --world-view-url http://127.0.0.1:3007
```

The World tab embeds the configured URL in a sandboxed iframe. Selected Timeline actions are linked through the shared `entity` query parameter. The frontend adds declarative `va_agent`, `va_action`, and `va_target` query parameters so an external adapter can choose a camera and highlight recorded target context. These parameters are context only; no Minecraft command or bot-control channel exists.

The dependency remains graceful:

- Without `--world-view-url`, the route explains that the integration is disabled.
- A stopped or unavailable viewer produces only a blank/reloadable frame; VillagerAgent and the Visualizer API continue.
- The Visualizer does not import `prismarine-viewer`, Mineflayer, canvas, WebGL/headless rendering, or native codecs.
- The viewer URL must be HTTP(S). Non-loopback hosts are rejected unless `--allow-remote-world-view` is also supplied.

## Security

Keep the viewer on loopback. Prismarine viewers are web servers and are not an authenticated VillagerAgent control plane. Remote exposure can reveal world geometry, agent location, action targets, and server activity. The explicit remote flag records operator intent but does not add authentication.

The iframe uses `sandbox="allow-scripts allow-same-origin"` and `no-referrer`. It receives no evaluator/private state, inventory mutation API, teleport, block placement, or command endpoint from this repository.

## Known Limitations

- Agent camera switching depends on the external adapter honoring `va_agent`; stock deployments may ignore it.
- Target highlighting is limited to coordinates/target data already present in sanitized action arguments.
- A real world mirror requires an existing bot, proxy, or world provider and can add Minecraft server/chunk load.
- Headless/video examples may require browser, WebGL, canvas, ffmpeg, or other native/system dependencies.
- The Visualizer cannot determine iframe health cross-origin, so it provides an explicit reload and failure guidance rather than supervising the process.
- Offline runs have recorded action context but no historical voxel snapshot unless an external viewer provides one.

This is a read-only visualization boundary, not a bot-control integration.
