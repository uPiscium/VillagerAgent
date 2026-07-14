from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from benchmarks.common.report import summarize_inputs, write_csv_report, write_json_report
from benchmarks.common.run_artifacts import (
    finalize_run_directory,
    prepare_run_directory,
    read_attempt_id,
    validate_run_attempt,
)
from benchmarks.common.sanitization import redact_text, sanitize_command


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    report_dir = Path(args.report_dir or output_dir / "common_report")
    output_resolved = output_dir.resolve()
    report_resolved = report_dir.resolve()
    if output_resolved == report_resolved or output_resolved.is_relative_to(report_resolved):
        raise ValueError("C-WAH report directory must not equal or contain the matrix output directory")

    command = build_matrix_command(args=args, output_dir=output_dir)
    completed = subprocess.run(command, cwd=Path.cwd(), text=True, capture_output=True, check=False)
    secret_values = (os.environ.get("CWAH_LLM_API_KEY", ""),)
    report_attempt_id = prepare_run_directory(
        report_dir,
        producer="benchmarks.cwah.baseline",
        overwrite=args.overwrite,
    )

    rows = []
    matrix_attempt_id = None
    csv_path = report_dir / "common_report.csv"
    json_path = report_dir / "common_report.json"
    if completed.returncode == 0 and (output_dir / "attempt.json").exists():
        matrix_attempt_id = read_attempt_id(output_dir)
        validate_run_attempt(output_dir, attempt_id=matrix_attempt_id)
    if matrix_attempt_id and (output_dir / "matrix_summary.json").exists():
        rows = summarize_inputs([output_dir])
        write_csv_report(rows, csv_path)
        write_json_report(rows, json_path)

    manifest = build_manifest(
        args=args,
        command=command,
        matrix_returncode=completed.returncode,
        matrix_stdout=redact_text(completed.stdout.strip(), secret_values=secret_values),
        matrix_stderr=redact_text(completed.stderr.strip(), secret_values=secret_values),
        output_dir=output_dir,
        report_dir=report_dir,
        common_rows=rows,
        attempt_id=report_attempt_id,
        matrix_attempt_id=matrix_attempt_id,
    )
    manifest_path = report_dir / "baseline_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    finalize_run_directory(
        report_dir,
        attempt_id=report_attempt_id,
        producer="benchmarks.cwah.baseline",
        status="completed" if completed.returncode == 0 and matrix_attempt_id else "failed",
    )
    if matrix_attempt_id:
        finalize_run_directory(
            output_dir,
            attempt_id=matrix_attempt_id,
            producer="benchmarks.cwah.matrix",
            status="completed",
            stamp_nested=False,
        )
    print(json.dumps({"passed": completed.returncode == 0, "manifest": str(manifest_path), "runs": len(rows)}, sort_keys=True))
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def build_matrix_command(*, args: argparse.Namespace, output_dir: Path) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "benchmarks.cwah.matrix",
        "--env",
        args.env,
        "--tasks",
        args.tasks,
        "--seeds",
        args.seeds,
        "--output-dir",
        str(output_dir),
        "--max-steps",
        str(args.max_steps),
        "--max-policy-steps",
        str(args.max_policy_steps),
        "--prefer-physical-after-steps",
        str(args.prefer_physical_after_steps),
        "--navigation-loop-threshold",
        str(args.navigation_loop_threshold),
        "--base-url",
        args.base_url,
        "--model",
        args.model,
        "--base-port",
        str(args.base_port),
        "--port-stride",
        str(args.port_stride),
    ]
    if args.full_episode:
        command.append("--full-episode")
    if args.coela_cwah_path:
        command.extend(["--coela-cwah-path", args.coela_cwah_path])
    if args.dataset_path:
        command.extend(["--dataset-path", args.dataset_path])
    if args.executable_file:
        command.extend(["--executable-file", args.executable_file])
    if getattr(args, "overwrite", False):
        command.append("--overwrite")
    return command


def build_manifest(
    *,
    args: argparse.Namespace,
    command: list[str],
    matrix_returncode: int,
    matrix_stdout: str,
    matrix_stderr: str,
    output_dir: Path,
    report_dir: Path,
    common_rows: list[dict],
    attempt_id: str | None = None,
    matrix_attempt_id: str | None = None,
) -> dict:
    secret_values = (
        getattr(args, "api_key", ""),
        os.environ.get("CWAH_LLM_API_KEY", ""),
    )
    return {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "matrix_attempt_id": matrix_attempt_id,
        "benchmark": "cwah",
        "baseline_type": "real_coela" if args.env == "coela" else "mock_validation",
        "performance_claim": False,
        "caveat": "Bounded baseline for the current policy; not a benchmark-performance claim.",
        "config": {
            "env": args.env,
            "tasks": args.tasks,
            "seeds": args.seeds,
            "max_steps": args.max_steps,
            "max_policy_steps": args.max_policy_steps,
            "full_episode": args.full_episode,
            "prefer_physical_after_steps": args.prefer_physical_after_steps,
            "navigation_loop_threshold": args.navigation_loop_threshold,
            "model": args.model,
            "base_port": args.base_port,
            "port_stride": args.port_stride,
        },
        "command": sanitize_command(command, secret_values=secret_values),
        "matrix_returncode": matrix_returncode,
        "matrix_stdout": redact_text(matrix_stdout, secret_values=secret_values),
        "matrix_stderr": redact_text(matrix_stderr, secret_values=secret_values),
        "outputs": {
            "matrix_dir": str(output_dir),
            "matrix_summary": str(output_dir / "matrix_summary.json"),
            "matrix_metrics": str(output_dir / "matrix_metrics.csv"),
            "common_report_dir": str(report_dir),
            "common_report_csv": str(report_dir / "common_report.csv"),
            "common_report_json": str(report_dir / "common_report.json"),
        },
        "runs": len(common_rows),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a C-WAH baseline matrix and produce common benchmark reports.")
    parser.add_argument("--env", choices=["mock", "coela"], default="mock")
    parser.add_argument("--tasks", default="0,1,2")
    parser.add_argument("--seeds", default="0,1")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--report-dir", default="")
    parser.add_argument("--max-steps", type=int, default=25)
    parser.add_argument("--max-policy-steps", type=int, default=2)
    parser.add_argument("--full-episode", action="store_true")
    parser.add_argument("--prefer-physical-after-steps", type=int, default=0)
    parser.add_argument("--navigation-loop-threshold", type=int, default=12)
    parser.add_argument("--base-url", default="http://ollama.arc.upiscium.dev/v1")
    parser.add_argument("--model", default="gemma4:e4b")
    parser.add_argument("--coela-cwah-path", default="")
    parser.add_argument("--dataset-path", default="")
    parser.add_argument("--executable-file", default="")
    parser.add_argument("--base-port", type=int, default=6314)
    parser.add_argument("--port-stride", type=int, default=1)
    parser.add_argument("--overwrite", action="store_true", help="Explicitly replace existing matrix and report directories")
    return parser.parse_args()


if __name__ == "__main__":
    main()
