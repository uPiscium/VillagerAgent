from __future__ import annotations

import argparse
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from openai import APITimeoutError

from benchmarks.craft.adapters.ollama_preflight import preflight_ollama_model
from benchmarks.cwah.coela_env import resolve_coela_executable
from benchmarks.experiment_provenance import file_identity, git_identity, model_identity


def is_provider_timeout(error: BaseException) -> bool:
    return isinstance(error, (TimeoutError, APITimeoutError))


def model_provider(base_url: str) -> str:
    host = (urlparse(base_url).hostname or "").lower()
    return (
        "ollama"
        if "ollama" in host or host in {"localhost", "127.0.0.1", "::1"}
        else "openai_compatible"
    )


@lru_cache(maxsize=None)
def model_metadata(base_url: str, model: str) -> dict:
    if model_provider(base_url) != "ollama":
        return {}
    return preflight_ollama_model(base_url=base_url, model=model)


def resolved_external_paths(args: argparse.Namespace, *, root: Path | None = None) -> dict[str, str]:
    root = (root or Path.cwd()).resolve()
    coela_cwah = Path(
        args.coela_cwah_path or root / "external" / "CoELA" / "cwah"
    ).expanduser().resolve()
    dataset_path = Path(
        args.dataset_path or coela_cwah / "dataset" / "test_env_set_help.pik"
    ).expanduser().resolve()
    executable_file = resolve_coela_executable(
        root=root,
        coela_cwah=coela_cwah,
        explicit_path=args.executable_file,
    )
    return {
        "coela_cwah_path": str(coela_cwah),
        "dataset_path": str(dataset_path),
        "executable_file": str(executable_file),
    }


def provenance_assets(
    args: argparse.Namespace,
    *,
    metadata: dict | None = None,
    root: Path | None = None,
) -> list[dict]:
    paths = resolved_external_paths(args, root=root)
    coela_required = args.env == "coela"
    return [
        git_identity(
            paths["coela_cwah_path"],
            required=coela_required,
            name="coela_cwah",
            kind="submodule",
        ),
        file_identity(
            paths["dataset_path"],
            name="cwah_dataset",
            kind="dataset",
            required=coela_required,
        ),
        file_identity(
            paths["executable_file"],
            name="virtualhome_executable",
            kind="executable",
            required=coela_required,
        ),
        model_identity(
            name="policy_model",
            provider=model_provider(args.base_url),
            model=args.model,
            metadata=metadata,
            required=True,
        ),
    ]
