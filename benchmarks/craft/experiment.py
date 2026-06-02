import argparse
from pathlib import Path

import yaml

from benchmarks.craft.config import load_config, output_dir_for_config, repo_root
from benchmarks.craft.report import build_comparison_report, write_csv_report, write_json_report
from benchmarks.craft.run import run_config


class ExperimentConfigError(ValueError):
    """Raised when a CRAFT experiment manifest is invalid."""


def load_experiment(path: str) -> dict:
    root = repo_root()
    manifest_path = Path(path)
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    if not manifest_path.exists():
        raise FileNotFoundError(f"CRAFT experiment manifest not found: {manifest_path}")

    with manifest_path.open("r", encoding="utf-8") as f:
        manifest = yaml.safe_load(f) or {}
    experiment = manifest.get("experiment")
    if not isinstance(experiment, dict):
        raise ExperimentConfigError("experiment manifest must contain an experiment mapping.")
    runs = experiment.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ExperimentConfigError("experiment.runs must be a non-empty list.")
    if not all(isinstance(run, str) for run in runs):
        raise ExperimentConfigError("experiment.runs entries must be config paths.")
    overrides = experiment.get("overrides", {})
    if overrides and not isinstance(overrides, dict):
        raise ExperimentConfigError("experiment.overrides must be a mapping when provided.")
    return manifest


def run_experiment(
    manifest_path: str,
    *,
    dry_run: bool = False,
    overrides: dict | None = None,
) -> list[dict]:
    root = repo_root()
    manifest = load_experiment(manifest_path)
    experiment = manifest["experiment"]
    run_overrides = _experiment_overrides(experiment, overrides)
    command = _command_text(manifest_path, dry_run=dry_run, overrides=overrides)

    run_names = []
    for config_path in experiment["runs"]:
        config = load_config(config_path, overrides=run_overrides)
        output_dir = output_dir_for_config(config)
        run_names.append(output_dir.name)
        run_config(
            config_path,
            dry_run=dry_run,
            overrides=run_overrides,
            command_text=command,
        )

    if dry_run:
        return []

    result_root = Path(experiment.get("result_root", "result/craft"))
    if not result_root.is_absolute():
        result_root = root / result_root
    rows = build_comparison_report(run_names, result_root=result_root)
    report = experiment.get("report", {})
    output = _report_path(
        report.get("output", "result/craft/comparison_summary.csv"),
        run_overrides,
    )
    if not output.is_absolute():
        output = root / output
    write_csv_report(rows, output)
    json_output = report.get("json_output")
    if json_output:
        json_path = _report_path(json_output, run_overrides)
        if not json_path.is_absolute():
            json_path = root / json_path
        write_json_report(rows, json_path)
    print(f"Wrote CRAFT experiment report: {output}")
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a CRAFT experiment manifest.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--structure", default=None)
    parser.add_argument("--turns", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--run-name-suffix",
        default=None,
        help="Append a suffix to each run name, useful for smoke experiments.",
    )
    return parser.parse_args()


def _structure_override(value: str | None) -> list[int] | None:
    if value is None:
        return None
    return [int(part) for part in value.split(",") if part.strip()]


def _experiment_overrides(experiment: dict, cli_overrides: dict | None) -> dict:
    manifest_overrides = dict(experiment.get("overrides", {}) or {})
    for key, value in (cli_overrides or {}).items():
        if value is not None:
            manifest_overrides[key] = value
    return manifest_overrides


def _command_text(manifest_path: str, *, dry_run: bool, overrides: dict | None) -> str:
    command = f"python -m benchmarks.craft.experiment --config {manifest_path}"
    if dry_run:
        command += " --dry-run"
    if not overrides:
        return command
    if overrides.get("structures") is not None:
        command += " --structure " + ",".join(str(item) for item in overrides["structures"])
    if overrides.get("turns") is not None:
        command += f" --turns {overrides['turns']}"
    if overrides.get("seed") is not None:
        command += f" --seed {overrides['seed']}"
    if overrides.get("run_name_suffix"):
        command += f" --run-name-suffix {overrides['run_name_suffix']}"
    return command


def _report_path(path: str, overrides: dict) -> Path:
    report_path = Path(path)
    suffix = overrides.get("run_name_suffix")
    if suffix:
        report_path = report_path.with_name(f"{report_path.stem}{suffix}{report_path.suffix}")
    return report_path


def main() -> None:
    args = parse_args()
    run_experiment(
        args.config,
        dry_run=args.dry_run,
        overrides={
            "structures": _structure_override(args.structure),
            "turns": args.turns,
            "seed": args.seed,
            "run_name_suffix": args.run_name_suffix,
        },
    )


if __name__ == "__main__":
    main()
