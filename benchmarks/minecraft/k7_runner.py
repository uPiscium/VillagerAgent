"""The one-shot durable runner for the frozen K7b K6 census."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from benchmarks.common.eac.canonical import canonical_bytes
from benchmarks.minecraft import k6_fixture, k6_protocol

HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[1]
CONTRACT_PATH = HERE / "k7_runner_v1.json"
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
STATUSES = {"not_started", "started", "completed", "failed"}


class K7RunnerError(RuntimeError):
    """A K7 preflight, execution, or durable-artifact gate failed."""


def _digest_without_field(value: dict[str, Any]) -> str:
    content = dict(value)
    content.pop("detached_artifact_sha256", None)
    return hashlib.sha256(canonical_bytes(content)).hexdigest()


def load_k7_contract(path: str | Path = CONTRACT_PATH) -> tuple[dict[str, Any], str]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {
        "artifact_id", "artifact_version", "detached_artifact_sha256",
        "runner_id", "runner_version", "implementation", "implementation_sha256",
        "protocol_binding",
        "census", "canonical_order_source", "retry", "resume",
        "completeness_requirement", "failure_policy", "persistence_layout_version",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise K7RunnerError("K7 runner contract schema mismatch")
    if value["artifact_id"] != "minecraft-k7b-census-runner" or value["artifact_version"] != 1:
        raise K7RunnerError("K7 runner contract identity mismatch")
    if value["implementation"] != "benchmarks/minecraft/k7_runner.py":
        raise K7RunnerError("K7 runner contract implementation binding mismatch")
    if not isinstance(value["implementation_sha256"], str) or re.fullmatch(
        r"[0-9a-f]{64}", value["implementation_sha256"]
    ) is None:
        raise K7RunnerError("K7 runner contract implementation digest is malformed")
    digest = _digest_without_field(value)
    declared = value["detached_artifact_sha256"]
    if not isinstance(declared, str) or re.fullmatch(r"[0-9a-f]{64}", declared) is None:
        raise K7RunnerError("K7 runner contract detached digest is malformed")
    if declared != digest:
        raise K7RunnerError("K7 runner contract detached digest mismatch")
    if set(value["protocol_binding"]) != {
        "protocol_digest", "inventory_digest", "result_schema_digest",
    }:
        raise K7RunnerError("K7 runner contract binding schema mismatch")
    if value["census"] != {"primary": 40, "control": 20, "total": 60}:
        raise K7RunnerError("K7 runner contract census mismatch")
    if value["canonical_order_source"] != "benchmarks.minecraft.k6_protocol.build_k6_cells":
        raise K7RunnerError("K7 runner contract canonical order mismatch")
    if value["retry"] is not False or value["resume"] is not False:
        raise K7RunnerError("K7 runner contract permits retry or resume")
    if value["completeness_requirement"] != {
        "exact_cells": 60, "primary_cells": 40, "control_cells": 20,
        "pairs": 30, "aggregate_complete": True,
    }:
        raise K7RunnerError("K7 runner contract completeness mismatch")
    if value["failure_policy"] != {
        "pre_submission_failures": [
            "digest_or_binding_mismatch", "revision_mismatch", "dirty_tree",
            "invalid_census", "output_preparation_failure",
        ],
        "post_submission_action": "abort_without_aggregate",
        "cell_retry": False,
        "resume": False,
        "silent_replacement": False,
        "automatic_infrastructure_classification": False,
        "infrastructure_failures": [
            "externally_evidenced_host_or_process_interruption",
            "filesystem_io_unavailability_or_exhaustion",
            "os_level_resource_failure",
        ],
    }:
        raise K7RunnerError("K7 runner contract failure policy mismatch")
    if value["persistence_layout_version"] != "minecraft-k7b-run/1":
        raise K7RunnerError("K7 runner contract persistence layout mismatch")
    return value, digest


def runner_contract_digest(path: str | Path = CONTRACT_PATH) -> str:
    return load_k7_contract(path)[1]


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments], capture_output=True, text=True,
        check=False, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode != 0:
        raise K7RunnerError(f"K7 git command failed: {' '.join(arguments)}")
    return result.stdout


def _repository_top(root: Path) -> Path:
    top = Path(_git(root, "rev-parse", "--show-toplevel").strip()).resolve()
    if top != root.resolve():
        raise K7RunnerError("K7 repo-root must be the Git top-level directory")
    return top


def _worktrees(root: Path) -> tuple[Path, ...]:
    paths = []
    for line in _git(root, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            paths.append(Path(line[9:]).resolve())
    return tuple(paths) or (root.resolve(),)


def _validate_output_dir(output_dir: str | Path, worktrees: tuple[Path, ...]) -> Path:
    path = Path(output_dir)
    if not path.is_absolute():
        raise K7RunnerError("K7 output-dir must be absolute")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.exists() and current.is_symlink():
            raise K7RunnerError("K7 output-dir cannot contain symlink ancestors")
    if not path.exists() or not path.is_dir() or path.is_symlink():
        raise K7RunnerError("K7 output-dir must be an existing non-symlink directory")
    resolved = path.resolve()
    metadata = resolved.stat()
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o022:
        raise K7RunnerError("K7 output-dir must be owned by the current user and not group/world writable")
    if any(resolved == worktree or worktree in resolved.parents for worktree in worktrees):
        raise K7RunnerError("K7 output-dir must be outside every Git worktree")
    return resolved


def _verify_repository(root: Path, expected: str) -> str:
    if COMMIT_RE.fullmatch(expected) is None:
        raise K7RunnerError("K7 expected-execution-revision must be a full 40-character Git commit")
    head = _git(root, "rev-parse", "HEAD").strip()
    if head != expected:
        raise K7RunnerError("K7 execution revision does not match expected execution revision")
    if _git(root, "status", "--porcelain", "--untracked-files=all").strip():
        raise K7RunnerError("K7 Git tree is not clean, including untracked files")
    return head


def _protocol_checks(protocol_module: Any) -> tuple[dict[str, Any], tuple[Any, ...]]:
    protocol = protocol_module.load_k6_protocol()
    cells = tuple(protocol_module.build_k6_cells())
    if len(cells) != 60:
        raise K7RunnerError("K7 census must contain exactly 60 cells")
    if sum(cell.matrix == "primary" for cell in cells) != 40:
        raise K7RunnerError("K7 census must contain exactly 40 primary cells")
    if sum(cell.matrix == "control" for cell in cells) != 20:
        raise K7RunnerError("K7 census must contain exactly 20 control cells")
    return protocol, cells


def preflight(
    repo_root: str | Path | None = None,
    *,
    expected_execution_revision: str,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    return _preflight_with_protocol(
        repo_root,
        expected_execution_revision=expected_execution_revision,
        output_dir=output_dir,
        protocol_module=k6_protocol,
    )


def _preflight_with_protocol(
    repo_root: str | Path | None,
    *,
    expected_execution_revision: str,
    output_dir: str | Path | None,
    protocol_module: Any,
) -> dict[str, Any]:
    root = Path(repo_root).resolve() if repo_root is not None else REPOSITORY_ROOT
    if root != REPOSITORY_ROOT.resolve():
        raise K7RunnerError("K7 repo-root must be the checkout containing the census runner")
    root = _repository_top(root)
    contract, contract_digest = load_k7_contract()
    protocol, cells = _protocol_checks(protocol_module)
    if output_dir is not None:
        _validate_output_dir(output_dir, _worktrees(root))
    expected_bindings = {
        "protocol_digest": protocol["validated_protocol_digest"],
        "inventory_digest": protocol["validated_inventory_digest"],
        "result_schema_digest": protocol["validated_result_schema_digest"],
    }
    if contract["protocol_binding"] != expected_bindings:
        raise K7RunnerError("K7 runner contract K6 artifact binding mismatch")
    revision = _verify_repository(root, expected_execution_revision)
    implementation = REPOSITORY_ROOT / contract["implementation"]
    if implementation.resolve() != (HERE / "k7_runner.py").resolve():
        raise K7RunnerError("K7 runner contract implementation path mismatch")
    implementation_digest = hashlib.sha256(implementation.read_bytes()).hexdigest()
    if implementation_digest != contract["implementation_sha256"]:
        raise K7RunnerError("K7 runner contract implementation digest mismatch")
    return {
        "run_id": None,
        "execution_revision": revision,
        "runner_id": contract["runner_id"],
        "runner_version": contract["runner_version"],
        "runner_contract_digest": contract_digest,
        "implementation_sha256": implementation_digest,
        "protocol_digest": expected_bindings["protocol_digest"],
        "inventory_digest": expected_bindings["inventory_digest"],
        "result_schema_digest": expected_bindings["result_schema_digest"],
        "cell_ids": [cell.cell_id for cell in cells],
        "primary_cells": 40,
        "control_cells": 20,
        "total_cells": 60,
        "repository_clean": True,
    }


def _durable_json(path: Path, value: Any, *, replace_existing: bool = False) -> None:
    if not path.parent.is_dir():
        raise K7RunnerError(f"K7 durable target parent does not exist: {path.parent}")
    if path.exists() and not replace_existing:
        raise K7RunnerError(f"K7 durable target already exists: {path}")
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        raise K7RunnerError(f"K7 durable temporary target already exists: {temporary}")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if replace_existing:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise K7RunnerError(f"K7 durable target already exists: {path}") from exc
            temporary.unlink()
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _fsync_directory(path: Path) -> None:
    directory = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _cell_identity(cell: Any) -> dict[str, Any]:
    return {name: getattr(cell, name) for name in (
        "cell_id", "scenario_family", "inventory_id", "condition", "affected_actor", "matrix"
    )}


def _check_pairs(traces: list[dict[str, Any]], protocol_module: Any) -> None:
    pairs: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for trace in traces:
        cell = trace["cell"]
        key = (cell["scenario_family"], cell["inventory_id"], cell["affected_actor"], cell["matrix"])
        pairs.setdefault(key, []).append(trace)
    if len(pairs) != 30:
        raise K7RunnerError("K7 pair gate failed: expected exactly 30 pair keys")
    for pair in pairs.values():
        if len(pair) != 2 or {item["cell"]["condition"] for item in pair} != set(protocol_module.CONDITIONS):
            raise K7RunnerError("K7 pair gate failed: each pair requires two conditions")
        if len({item["pairing_digest"] for item in pair}) != 1:
            raise K7RunnerError("K7 pair gate failed: pairing digests differ")


def run(
    repo_root: str | Path | None = None,
    *,
    run_id: str,
    expected_execution_revision: str,
    output_dir: str | Path,
) -> dict[str, Any]:
    return _run_with_dependencies(
        repo_root,
        run_id=run_id,
        expected_execution_revision=expected_execution_revision,
        output_dir=output_dir,
        fixture_module=k6_fixture,
        protocol_module=k6_protocol,
    )


def _run_with_dependencies(
    repo_root: str | Path | None,
    *,
    run_id: str,
    expected_execution_revision: str,
    output_dir: str | Path,
    fixture_module: Any,
    protocol_module: Any,
) -> dict[str, Any]:
    if RUN_ID_RE.fullmatch(run_id) is None or run_id in {".", ".."}:
        raise K7RunnerError("K7 run_id is invalid")
    checks = _preflight_with_protocol(
        repo_root,
        expected_execution_revision=expected_execution_revision,
        output_dir=output_dir,
        protocol_module=protocol_module,
    )
    output = _validate_output_dir(output_dir, _worktrees(Path(repo_root).resolve() if repo_root else REPOSITORY_ROOT))
    run_dir = output / run_id
    if run_dir.exists() or run_dir.is_symlink():
        raise K7RunnerError("K7 run directory must not already exist")
    run_dir.mkdir(mode=0o700)
    _fsync_directory(output)
    (run_dir / "cells").mkdir(mode=0o700)
    _fsync_directory(run_dir)
    cells = tuple(protocol_module.build_k6_cells())
    manifest = {
        "schema_version": "minecraft-k7b-run/1",
        "run_id": run_id,
        "execution_revision": checks["execution_revision"],
        "runner": {
            "identity": checks["runner_id"], "version": checks["runner_version"],
            "contract_digest": checks["runner_contract_digest"],
            "implementation_sha256": checks["implementation_sha256"],
        },
        "protocol_digest": checks["protocol_digest"],
        "inventory_digest": checks["inventory_digest"],
        "result_schema_digest": checks["result_schema_digest"],
        "repository_clean": checks["repository_clean"],
        "canonical_order_source": "benchmarks.minecraft.k6_protocol.build_k6_cells",
        "planned_cell_ids": [cell.cell_id for cell in cells],
        "started": False,
        "completed": False,
        "aggregate_generated": False,
        "aggregate_path": None,
        "run_status": "not_started",
        "failure": None,
        "cells": [
            {"ordinal": ordinal, "cell_id": cell.cell_id, "path": f"cells/{ordinal:04d}_{cell.cell_id}.json", "status": "not_started"}
            for ordinal, cell in enumerate(cells, 1)
        ],
    }
    manifest_path = run_dir / "run_manifest.json"
    _durable_json(manifest_path, manifest)
    try:
        for index, cell in enumerate(cells):
            entry = manifest["cells"][index]
            entry["status"] = "started"
            manifest["started"] = True
            manifest["run_status"] = "started"
            _durable_json(manifest_path, manifest, replace_existing=True)
            try:
                trial = fixture_module.construct_k6_trial(cell)
                trace = trial.submit()
                trace = protocol_module.validate_k6_trace(trace, cell=cell)
                _durable_json(run_dir / entry["path"], trace)
                entry["status"] = "completed"
                _durable_json(manifest_path, manifest, replace_existing=True)
            except Exception as exc:
                entry["status"] = "failed"
                entry["error"] = f"{type(exc).__name__}: {exc}"
                try:
                    _durable_json(manifest_path, manifest, replace_existing=True)
                except Exception as persistence_exc:
                    raise K7RunnerError(
                        f"K7 cell {cell.cell_id} failed and failed status persistence also failed: "
                        f"{persistence_exc}"
                    ) from exc
                raise K7RunnerError(f"K7 cell {cell.cell_id} failed: {exc}") from exc
        disk_paths = sorted((run_dir / "cells").glob("*.json"))
        expected_paths = {run_dir / entry["path"] for entry in manifest["cells"]}
        if set(disk_paths) != expected_paths or len(disk_paths) != 60:
            raise K7RunnerError("K7 completeness gate failed: exact durable trace file set required")
        traces = []
        seen = set()
        for entry, cell in zip(manifest["cells"], cells):
            trace = json.loads((run_dir / entry["path"]).read_text(encoding="utf-8"))
            protocol_module.validate_k6_trace(trace, cell=cell)
            cell_id = trace["cell"]["cell_id"]
            if cell_id in seen or cell_id != cell.cell_id:
                raise K7RunnerError("K7 completeness gate failed: duplicate or misplaced cell identity")
            seen.add(cell_id)
            traces.append(trace)
        if len(seen) != 60 or sum(item["cell"]["matrix"] == "primary" for item in traces) != 40 or sum(item["cell"]["matrix"] == "control" for item in traces) != 20:
            raise K7RunnerError("K7 completeness gate failed: exact 40/20 cell counts required")
        _check_pairs(traces, protocol_module)
        if any(entry["status"] != "completed" for entry in manifest["cells"]):
            raise K7RunnerError("K7 completeness gate failed: manifest contains non-completed cells")
        aggregate = protocol_module.aggregate_k6_results(traces)
        if aggregate.get("complete") is not True or aggregate.get("observed_primary_cells") != 40 or aggregate.get("observed_control_cells") != 20:
            raise K7RunnerError("K7 aggregate completeness gate failed")
        protocol = protocol_module.load_k6_protocol()
        wrapper = {
            "schema_version": "minecraft-k7b-aggregate/1",
            "run_id": run_id,
            "execution_revision": checks["execution_revision"],
            "runner_identity": checks["runner_id"],
            "runner_version": checks["runner_version"],
            "runner_contract_digest": checks["runner_contract_digest"],
            "protocol_digest": checks["protocol_digest"],
            "inventory_digest": checks["inventory_digest"],
            "result_schema_digest": checks["result_schema_digest"],
            "raw_trace_count": 60,
            "pair_count": 30,
            "protocol_pre_run_exposure": protocol["pre_run_exposure"],
            "aggregate": aggregate,
        }
        _durable_json(run_dir / "aggregate.json", wrapper)
        manifest["completed"] = True
        manifest["aggregate_generated"] = True
        manifest["aggregate_path"] = "aggregate.json"
        manifest["run_status"] = "completed"
        _durable_json(manifest_path, manifest, replace_existing=True)
        return wrapper
    except Exception as failure:
        manifest["completed"] = False
        manifest["aggregate_generated"] = False
        manifest["aggregate_path"] = None
        manifest["run_status"] = "failed"
        manifest["failure"] = {
            "type": type(failure).__name__,
            "message": str(failure),
        }
        try:
            _durable_json(manifest_path, manifest, replace_existing=True)
        except Exception as persistence_failure:
            raise K7RunnerError(
                "K7 run aborted and durable failure-state persistence also failed; "
                f"on-disk status is indeterminate: {persistence_failure}"
            ) from failure
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="k7_runner")
    subcommands = parser.add_subparsers(dest="command", required=True)
    preflight_parser = subcommands.add_parser("preflight")
    preflight_parser.add_argument("--expected-execution-revision", required=True)
    run_parser = subcommands.add_parser("run")
    run_parser.add_argument("--run-id", required=True)
    run_parser.add_argument("--expected-execution-revision", required=True)
    run_parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    if args.command == "preflight":
        result = preflight(
            expected_execution_revision=args.expected_execution_revision,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    result = run(
        run_id=args.run_id,
        expected_execution_revision=args.expected_execution_revision,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


run_k7 = run
preflight_k7 = preflight


if __name__ == "__main__":
    sys.exit(main())
