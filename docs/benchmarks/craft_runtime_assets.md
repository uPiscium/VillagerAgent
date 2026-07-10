# CRAFT Runtime Assets

CRAFT runtime assets are local-only and are not tracked by this repository. Real CRAFT runs still require the structures dataset at the configured `craft.dataset_path`, commonly:

```text
external/CRAFT/data/structures_dataset_20.json
```

`benchmarks.craft.config.load_config()` validates runtime asset paths by default so real benchmark commands fail early with a clear `InvalidConfigError` when assets are missing.

Tests that only inspect CRAFT config metadata or parity can call `load_config(..., validate_runtime_assets=False)` or the parity CLI flag `--skip-runtime-asset-validation`. Runtime-adapter tests use small temporary dataset fixtures instead of relying on unmanaged local assets.

To run asset-dependent CRAFT commands locally, install or link the upstream CRAFT assets into the configured path, then run the desired benchmark command or `just test`.
