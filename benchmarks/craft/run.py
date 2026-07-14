import argparse
from pathlib import Path

from benchmarks.common.run_artifacts import finalize_run_directory, prepare_run_directory
from benchmarks.craft.config import (
    InvalidConfigError,
    condition_from_config,
    load_config,
    output_dir_for_config,
    save_resolved_config,
)
from benchmarks.craft.adapters.ollama_preflight import preflight_ollama_model
from benchmarks.craft.craft_env_adapter import CraftEnvAdapter
from benchmarks.craft.result_converter import normalize_results
from benchmarks.experiment_provenance import write_provenance


CONDITIONS = {"official_baseline", "villageragent_directors", "single_director_ablation"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run VillagerAgent on CRAFT.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--structure", default=None)
    parser.add_argument("--turns", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--condition", choices=sorted(CONDITIONS), default=None)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def _structure_override(value: str | None) -> list[int] | None:
    if value is None:
        return None
    return [int(part) for part in value.split(",") if part.strip()]

def _print_dry_run(config: dict, condition: str, output_dir: Path) -> None:
    print("Benchmark: CRAFT")
    print(f"Condition: {condition}")
    print(f"Structures: {config['run'].get('structures')}")
    print(f"Turns: {config['run'].get('turns')}")
    print(f"Seed: {config['run'].get('seed')}")
    print(f"CRAFT repo: {config['craft'].get('repo_path')}")
    print(f"Director model: {config['models']['director'].get('model')}")
    print(f"Builder model: {config['models']['builder'].get('model')}")
    print("Partial information guard: enabled")
    print(f"Output: {output_dir}")


def _require_runtime_api_keys(config: dict) -> None:
    for model_name in ("director", "builder"):
        model_config = config.get("models", {}).get(model_name, {})
        if model_config.get("api_key_env") and not model_config.get("api_key"):
            raise InvalidConfigError(
                f"Environment variable {model_config['api_key_env']} is not set"
            )


def _preflight_ollama_models(config: dict) -> list[dict]:
    results = []
    seen = set()
    for model_name in ("director", "builder"):
        model_config = config.get("models", {}).get(model_name, {})
        provider = model_config.get("provider")
        base_url = model_config.get("base_url", "")
        model = model_config.get("model", "")
        if not _is_ollama_model_config(model_config):
            continue
        if not base_url or not model:
            continue
        key = (base_url.rstrip("/"), model)
        if key in seen:
            continue
        seen.add(key)
        results.append(preflight_ollama_model(base_url=base_url, model=model))
    return results


def _is_ollama_model_config(model_config: dict) -> bool:
    provider = model_config.get("provider")
    base_url = model_config.get("base_url", "")
    return provider in {"ollama", "ollama_native"} or "ollama" in base_url.lower()


def run_config(
    config_path: str,
    *,
    dry_run: bool = False,
    overrides: dict | None = None,
    command_text: str | None = None,
    overwrite: bool = False,
) -> Path:
    config = load_config(config_path, overrides=overrides, require_api_keys=False)
    condition = (overrides or {}).get("condition") or condition_from_config(config)
    output_dir = output_dir_for_config(config)
    attempt_id = prepare_run_directory(
        output_dir,
        producer="benchmarks.craft.run",
        overwrite=overwrite,
    )
    config.setdefault("_meta", {})["attempt_id"] = attempt_id
    try:
        if not dry_run and condition != "official_baseline":
            _require_runtime_api_keys(config)
        save_resolved_config(config, output_dir)
        write_provenance(
            output_dir,
            benchmark="craft",
            command=command_text or _default_command_text(
                config_path,
                dry_run=dry_run,
                overrides=overrides,
                overwrite=overwrite,
            ),
            resolved_config=config,
            environment_notes=f"condition={condition}",
        )
        (output_dir / "logs").mkdir(parents=True, exist_ok=True)
        (output_dir / "raw").mkdir(parents=True, exist_ok=True)
        (output_dir / "normalized").mkdir(parents=True, exist_ok=True)
        if dry_run:
            _print_dry_run(config, condition, output_dir)
        else:
            if condition != "official_baseline":
                _preflight_ollama_models(config)
            adapter = CraftEnvAdapter(config, output_dir)
            raw_result = adapter.run(condition)
            normalize_results(
                config=config,
                condition=condition,
                raw_result=raw_result,
                output_dir=output_dir,
            )
    except Exception:
        finalize_run_directory(
            output_dir,
            attempt_id=attempt_id,
            producer="benchmarks.craft.run",
            status="failed",
        )
        raise
    finalize_run_directory(
        output_dir,
        attempt_id=attempt_id,
        producer="benchmarks.craft.run",
        status="completed",
    )
    return output_dir


def _default_command_text(
    config_path: str,
    *,
    dry_run: bool,
    overrides: dict | None,
    overwrite: bool = False,
) -> str:
    command = "python -m benchmarks.craft.run --config " + config_path
    if dry_run:
        command += " --dry-run"
    overrides = overrides or {}
    if overrides.get("structures") is not None:
        command += " --structure " + ",".join(str(item) for item in overrides["structures"])
    if overrides.get("turns") is not None:
        command += f" --turns {overrides['turns']}"
    if overrides.get("seed") is not None:
        command += f" --seed {overrides['seed']}"
    if overrides.get("condition") is not None:
        command += f" --condition {overrides['condition']}"
    if overwrite:
        command += " --overwrite"
    return command


def main() -> None:
    args = parse_args()
    overrides = {
        "structures": _structure_override(args.structure),
        "turns": args.turns,
        "seed": args.seed,
        "condition": args.condition,
    }
    command = "python -m benchmarks.craft.run --config " + args.config
    if args.dry_run:
        command += " --dry-run"
    if args.structure is not None:
        command += f" --structure {args.structure}"
    if args.turns is not None:
        command += f" --turns {args.turns}"
    if args.seed is not None:
        command += f" --seed {args.seed}"
    if args.condition is not None:
        command += f" --condition {args.condition}"
    if args.overwrite:
        command += " --overwrite"
    run_config(
        args.config,
        dry_run=args.dry_run,
        overrides=overrides,
        command_text=command,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
