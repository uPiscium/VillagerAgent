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
    return manifest


def run_experiment(manifest_path: str, *, dry_run: bool = False) -> list[dict]:
    root = repo_root()
    manifest = load_experiment(manifest_path)
    experiment = manifest["experiment"]
    command = f"python -m benchmarks.craft.experiment --config {manifest_path}"
    if dry_run:
        command += " --dry-run"

    run_names = []
    for config_path in experiment["runs"]:
        config = load_config(config_path)
        output_dir = output_dir_for_config(config)
        run_names.append(output_dir.name)
        run_config(config_path, dry_run=dry_run, command_text=command)

    if dry_run:
        return []

    result_root = Path(experiment.get("result_root", "result/craft"))
    if not result_root.is_absolute():
        result_root = root / result_root
    rows = build_comparison_report(run_names, result_root=result_root)
    report = experiment.get("report", {})
    output = Path(report.get("output", "result/craft/comparison_summary.csv"))
    if not output.is_absolute():
        output = root / output
    write_csv_report(rows, output)
    json_output = report.get("json_output")
    if json_output:
        json_path = Path(json_output)
        if not json_path.is_absolute():
            json_path = root / json_path
        write_json_report(rows, json_path)
    print(f"Wrote CRAFT experiment report: {output}")
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a CRAFT experiment manifest.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_experiment(args.config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
