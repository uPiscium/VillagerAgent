# CoELA Runtime Setup

This guide sets up the local runtime assets needed for real C-WAH / CoELA smoke runs. The repository tracks the pinned `external/CoELA` submodule only. The VirtualHome API checkout, simulator executable, generated logs, and port files are local runtime assets and must not be committed.

## License Warning

Before downloading or running CoELA runtime assets, review and accept the upstream license terms for your use case:

- CoELA C-WAH license: `external/CoELA/cwah/LICENSE.md`
- License: Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International
- Practical constraint: use is conditional on non-commercial research use with attribution and share-alike obligations.

Do not proceed if these terms are not acceptable for your use case.

## Expected Layout

After setup, the local tree should look like this:

```text
external/CoELA/
  cwah/
  virtualhome/
  executable/
    linux_exec.v2.3.0.x86_64
    linux_exec.v3_Data/
```

These paths are ignored by git:

- `external/CoELA/virtualhome/`
- `external/CoELA/executable/`
- `external/CoELA/executable.zip`
- `Player_*.log`
- `port_*.txt`

## Setup Commands

Run from the repository root.

```bash
git submodule update --init --recursive external/CoELA
```

Clone the modified VirtualHome API branch expected by CoELA:

```bash
git -C external/CoELA clone --branch wah https://github.com/xavierpuigf/virtualhome.git
```

Download and extract the CoELA-provided Linux executable:

```bash
nix develop --command gdown "https://drive.google.com/uc?id=1L79SxE07Jt-8-_uCvNnkwz5Kf6AjtaGp" --output external/CoELA/executable.zip
nix develop --command unzip -o external/CoELA/executable.zip -d external/CoELA
chmod +x external/CoELA/executable/linux_exec.v2.3.0.x86_64
```

## Smoke Run

Set non-placeholder LLM credentials through the environment. C-WAH runners intentionally do not accept API keys on the command line because commands and process arguments may be recorded:

```bash
export CWAH_LLM_API_KEY="..."
```

Run a bounded real CoELA smoke from the repository root:

```bash
nix develop --command python -m benchmarks.cwah.llm_smoke \
  --env coela \
  --full-episode \
  --task-id 0 \
  --seed 0 \
  --max-steps 25 \
  --artifact-dir /tmp/opencode/cwah-coela-smoke-normalized \
  --output /tmp/opencode/cwah-coela-smoke-raw.json
```

If the runtime assets are outside the default layout, pass explicit paths:

```bash
nix develop --command python -m benchmarks.cwah.llm_smoke \
  --env coela \
  --full-episode \
  --task-id 0 \
  --seed 0 \
  --max-steps 25 \
  --coela-cwah-path /path/to/CoELA/cwah \
  --executable-file /path/to/CoELA/executable/linux_exec.v2.3.0.x86_64 \
  --artifact-dir /tmp/opencode/cwah-coela-smoke-normalized \
  --output /tmp/opencode/cwah-coela-smoke-raw.json
```

## Matrix Smoke

Run a small bounded matrix after a single smoke succeeds:

```bash
nix develop --command python -m benchmarks.cwah.matrix \
  --env coela \
  --tasks 0,1,2 \
  --seeds 0,1 \
  --full-episode \
  --max-steps 25 \
  --prefer-physical-after-steps 0 \
  --output-dir /tmp/opencode/cwah-real-matrix
```

The matrix runner writes per-run artifacts plus:

- `matrix_summary.json`
- `matrix_metrics.csv`

Run and matrix directories must be new or empty. Use `--overwrite` only when the complete previous bundle should be replaced with a new attempt ID.

## Troubleshooting

- `C-WAH executable not found`: verify `external/CoELA/executable/linux_exec.v2.3.0.x86_64` exists and is executable.
- `ModuleNotFoundError` for CoELA dependencies: run commands through `nix develop --command ...` so the dev shell Python dependencies are available.
- VirtualHome connection issues: remove stale `port_*.txt` files and retry with a different `--base-port`.
- Git shows runtime assets as untracked inside the submodule: add local submodule excludes with `git -C external/CoELA config --local status.showUntrackedFiles no`, or ensure `/executable/`, `/executable.zip`, and `/virtualhome/` are in the submodule's local `.git/info/exclude`.
