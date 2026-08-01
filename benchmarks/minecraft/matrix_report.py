from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable

from benchmarks.minecraft.matrix_validation import scan_text


RUNS_FILE = "matrix_runs.jsonl"
SUMMARY_FILE = "matrix_summary.json"
GATE_REPORT_FILE = "matrix_gate_report.md"
MANIFEST_FILE = "matrix_manifest.json"
COMPLETED_MARKER = "_MATRIX_COMPLETED"
FAILED_MARKER = "_MATRIX_FAILED"
_OUTPUTS = (RUNS_FILE, SUMMARY_FILE, GATE_REPORT_FILE)
_SAFE_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


class MatrixReportValidationError(ValueError):
    """Raised when matrix report inputs or outputs are inconsistent."""


def generate_matrix_report(
    matrix_dir: str | Path,
    validations: Iterable[dict[str, Any] | str | Path] | None = None,
    *,
    expected_run_count: int | None = None,
    matrix_id: str | None = None,
    revision: str | None = None,
    premanifest_sha256: str | None = None,
    artifact_references: dict[str, Any] | None = None,
    failure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write deterministic, ordered matrix reports and a content manifest."""
    root = Path(matrix_dir)
    root.mkdir(parents=True, exist_ok=True)
    records = _load_records(root, validations)
    ordered = sorted(records, key=_run_order_key)
    _validate_unique_records(ordered)
    if any(scan_text(json.dumps(record, sort_keys=True)) for record in ordered):
        raise MatrixReportValidationError("validation records contain unsafe path or credential content")
    expected = len(ordered) if expected_run_count is None else expected_run_count
    if not isinstance(expected, int) or isinstance(expected, bool) or expected <= 0:
        raise MatrixReportValidationError("expected_run_count must be a positive integer")
    skipped = sum(record.get("status") == "skipped" for record in ordered)
    started = sum(record.get("attempts", 0) > 0 for record in ordered)
    completed = sum(
        record.get("attempts", 0) > 0 and record.get("status") != "skipped"
        for record in ordered
    )
    passed = sum(record.get("passed") is True for record in ordered)
    failed = sum(record.get("status") != "skipped" and record.get("passed") is not True for record in ordered)
    gate_passed = expected == 12 and len(ordered) == expected and passed == expected and failed == skipped == 0
    summary = {
        "schema_version": 1,
        "benchmark": "minecraft",
        "matrix_id": matrix_id,
        "revision": revision,
        "premanifest_sha256": premanifest_sha256,
        "planned": expected,
        "started": started,
        "completed": completed,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "planned_runs": expected,
        "started_runs": started,
        "completed_runs": completed,
        "passed_runs": passed,
        "failed_runs": failed,
        "skipped_runs": skipped,
        "gate_status": "passed" if gate_passed else "failed",
        "gate_passed": gate_passed,
        "expected_run_count": expected,
        "run_count": len(ordered),
        "missing_runs": max(expected - len(ordered), 0),
    }
    runs_text = "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        for record in ordered
    )
    _write_text(root / RUNS_FILE, runs_text)
    _write_json(root / SUMMARY_FILE, summary)
    _write_text(root / GATE_REPORT_FILE, _markdown(summary, ordered))
    artifacts = [
        {"path": name, "size": (root / name).stat().st_size, "sha256": _sha256(root / name)}
        for name in _OUTPUTS
    ]
    manifest = {
        "schema_version": 1,
        "manifest_type": "minecraft_matrix_report",
        "status": "completed" if gate_passed else "failed",
        "gate_passed": gate_passed,
        "artifacts": artifacts,
        "references": {
            "runs": RUNS_FILE,
            "summary": SUMMARY_FILE,
            "gate_report": GATE_REPORT_FILE,
            **(artifact_references or {}),
        },
    }
    _write_json(root / MANIFEST_FILE, manifest)
    completed = root / COMPLETED_MARKER
    failed_marker = root / FAILED_MARKER
    completed.unlink(missing_ok=True)
    failed_marker.unlink(missing_ok=True)
    manifest_sha256 = _sha256(root / MANIFEST_FILE)
    if gate_passed and expected == 12 and passed == 12:
        _write_text(completed, json.dumps({
            "matrix_id": matrix_id,
            "matrix_manifest_sha256": manifest_sha256,
            "passed": 12,
        }, sort_keys=True) + "\n")
    else:
        marker = {**(failure or {
            "reason": "matrix_gate_failed",
            "run": next((item.get("run_name") for item in ordered if item.get("status") == "failed"), None),
            "skipped_count": skipped,
        }), "matrix_manifest_sha256": manifest_sha256}
        _write_text(failed_marker, json.dumps(marker, sort_keys=True) + "\n")
    validate_matrix_manifest(root)
    return {**summary, "runs": ordered, "manifest": manifest}


write_matrix_report = generate_matrix_report
generate_matrix_reports = generate_matrix_report


def validate_matrix_manifest(matrix_dir: str | Path) -> dict[str, Any]:
    root = Path(matrix_dir)
    manifest = _read_object(root / MANIFEST_FILE)
    entries = manifest.get("artifacts")
    if not isinstance(entries, list) or [item.get("path") for item in entries if isinstance(item, dict)] != list(_OUTPUTS):
        raise MatrixReportValidationError("matrix manifest artifact order or membership is invalid")
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
            raise MatrixReportValidationError("matrix manifest contains an invalid artifact entry")
        path = root / entry["path"]
        if not path.is_file() or path.stat().st_size != entry["size"] or _sha256(path) != entry["sha256"]:
            raise MatrixReportValidationError(f"matrix artifact identity mismatch: {entry['path']}")
    references = manifest.get("references", {})
    hashed_references = []
    if isinstance(references.get("premanifest"), dict):
        hashed_references.append(references["premanifest"])
    if isinstance(references.get("validations"), list):
        hashed_references.extend(references["validations"])
    for reference in hashed_references:
        relative = reference.get("path") if isinstance(reference, dict) else None
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise MatrixReportValidationError("matrix manifest contains an invalid reference")
        path = root / relative
        if not path.is_file() or _sha256(path) != reference.get("sha256"):
            raise MatrixReportValidationError(f"matrix reference identity mismatch: {relative}")
    completed = root / COMPLETED_MARKER
    failed = root / FAILED_MARKER
    if completed.exists() == failed.exists():
        raise MatrixReportValidationError("exactly one matrix terminal marker is required")
    expected_marker = completed if manifest.get("status") == "completed" and manifest.get("gate_passed") is True else failed
    try:
        marker = json.loads(expected_marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MatrixReportValidationError("matrix terminal marker is invalid") from exc
    if not isinstance(marker, dict):
        raise MatrixReportValidationError("matrix terminal marker is invalid")
    if marker.get("matrix_manifest_sha256") != _sha256(root / MANIFEST_FILE):
        raise MatrixReportValidationError("matrix terminal marker manifest identity is invalid")
    summary = _read_object(root / SUMMARY_FILE)
    rows = _read_jsonl(root / RUNS_FILE)
    passed = sum(row.get("passed") is True for row in rows)
    skipped = sum(row.get("status") == "skipped" for row in rows)
    failed_count = sum(row.get("status") != "skipped" and row.get("passed") is not True for row in rows)
    expected = summary.get("expected_run_count")
    gate_passed = isinstance(expected, int) and expected == 12 and passed == 12 and len(rows) == expected and failed_count == skipped == 0
    if (
        summary.get("run_count") != len(rows)
        or summary.get("passed_runs") != passed
        or summary.get("failed_runs") != failed_count
        or summary.get("skipped") != skipped
        or summary.get("missing_runs") != max(expected - len(rows), 0)
        or summary.get("gate_passed") is not gate_passed
        or manifest.get("gate_passed") is not gate_passed
        or manifest.get("status") != ("completed" if gate_passed else "failed")
    ):
        raise MatrixReportValidationError("matrix summary counts do not match run records")
    if rows != sorted(rows, key=_run_order_key):
        raise MatrixReportValidationError("matrix run records are not ordered")
    return manifest


def _load_records(root: Path, values: Iterable[dict[str, Any] | str | Path] | None) -> list[dict[str, Any]]:
    if values is None:
        paths = sorted((root / "runs").glob("*/matrix_run_validation.json")) if (root / "runs").is_dir() else []
        return [_read_object(path) for path in paths]
    records = []
    for value in values:
        record = value if isinstance(value, dict) else _read_object(Path(value))
        if not isinstance(record, dict):
            raise MatrixReportValidationError("validation record must be an object")
        records.append(dict(record))
    return records


def _run_order_key(record: dict[str, Any]) -> tuple[int, str, str]:
    index = record.get("matrix_index")
    return (
        index if isinstance(index, int) and not isinstance(index, bool) else 2**31,
        str(record.get("run_name") or ""),
        str(record.get("attempt_id") or ""),
    )


def _validate_unique_records(records: list[dict[str, Any]]) -> None:
    identities = [(str(item.get("run_name") or ""), str(item.get("attempt_id") or "")) for item in records]
    if any(not _SAFE_IDENTITY.fullmatch(name) or not _SAFE_IDENTITY.fullmatch(attempt) for name, attempt in identities):
        raise MatrixReportValidationError("run_name and attempt_id must be safe portable identifiers")
    if len(identities) != len(set(identities)):
        raise MatrixReportValidationError("duplicate matrix validation record")


def _markdown(summary: dict[str, Any], records: list[dict[str, Any]]) -> str:
    status = "PASS" if summary["gate_passed"] else "FAIL"
    lines = [
        "# Minecraft Matrix Gate Report",
        "",
        f"Matrix: `{summary.get('matrix_id') or 'unspecified'}`",
        "",
        f"Revision: `{summary.get('revision') or 'unspecified'}`",
        "",
        f"Premanifest: `{summary.get('premanifest_sha256') or 'unspecified'}`",
        "",
        f"Gate: **{status}**",
        "",
        f"Runs: {summary['passed_runs']} passed, {summary['failed_runs']} failed, {summary['missing_runs']} missing of {summary['expected_run_count']} expected.",
        "",
        f"Production release decision: **{'APPROVED' if summary['gate_passed'] else 'BLOCKED'}**",
        "",
        "## Runs",
        "",
        "| Run | Variant | Seed | Baseline | Attempt | Result |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    lines.extend(
        f"| {_markdown_cell(record['run_name'])} | {_markdown_cell(record.get('variant', ''))} | {_markdown_cell(record.get('seed', ''))} | {_markdown_cell(_baseline_id(record))} | {_markdown_cell(record['attempt_id'])} | {_markdown_cell(str(record.get('status') or ('passed' if record.get('passed') is True else 'failed')).upper())} |"
        for record in records
    )
    lines.extend(["", "## Axis Aggregates", ""])
    for axis in ("variant", "seed", "baseline"):
        groups: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            value = _baseline_id(record) if axis == "baseline" else record.get(axis)
            groups.setdefault(str(value), []).append(record)
        lines.append(f"### {axis.title()}")
        lines.append("")
        lines.append("| Value | Passed | Total |")
        lines.append("| --- | ---: | ---: |")
        for value, group in sorted(groups.items()):
            lines.append(f"| {_markdown_cell(value)} | {sum(item.get('passed') is True for item in group)} | {len(group)} |")
        lines.append("")
    action_counts = [item.get("action", {}).get("count") for item in records if isinstance(item.get("action"), dict)]
    action_counts = [item for item in action_counts if isinstance(item, (int, float)) and not isinstance(item, bool)]
    action_text = "unavailable" if not action_counts else f"min={min(action_counts):g}, mean={sum(action_counts) / len(action_counts):.2f}, max={max(action_counts):g}"
    diagnostics = sum(item.get("diagnostics", {}).get("available") is True for item in records if isinstance(item.get("diagnostics"), dict))
    cleanup = sum(item.get("cleanup", {}).get("passed") is True for item in records if isinstance(item.get("cleanup"), dict))
    safety = sum(all(value is True for value in item.get("safety", {}).values()) for item in records if isinstance(item.get("safety"), dict) and item.get("safety"))
    lines.extend([
        "## Evidence", "", f"Actions: {action_text}", "",
        f"Diagnostics available: {diagnostics}/{len(records)}", "",
        f"Cleanup passed: {cleanup}/{len(records)}", "",
        f"Safety passed: {safety}/{len(records)}",
    ])
    return "\n".join(lines) + "\n"


def _baseline_id(record: dict[str, Any]) -> Any:
    baseline = record.get("baseline")
    return baseline.get("id", "") if isinstance(baseline, dict) else baseline or ""


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise MatrixReportValidationError("matrix_runs.jsonl contains a non-object row")
            rows.append(value)
    return rows


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MatrixReportValidationError(f"expected JSON object: {path.name}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    _write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _write_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
