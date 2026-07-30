from __future__ import annotations

import hashlib
import json
import platform
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any
from subprocess import run as run_process

import yaml

from benchmarks.common.sanitization import (
    collect_secret_values,
    sanitize_artifact_value,
    sanitize_command,
)


PROVENANCE_SCHEMA_VERSION = "2.0.0"
PROVENANCE_FILE = "provenance.json"
_LOCK_FILES = ("flake.lock", "poetry.lock", "uv.lock", "Pipfile.lock", "requirements.txt")
_PATH_FIELD_NAMES = frozenset({
    "artifact_dir",
    "config",
    "executable",
    "json_output",
    "matrix_provenance",
    "output",
    "path",
    "report_dir",
})
_PATH_FIELD_SUFFIXES = ("_dir", "_file", "_path", "_paths", "_root")


def standard_run_name(*parts: object) -> str:
    text = "_".join(str(part) for part in parts if part not in (None, ""))
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text.strip())
    text = re.sub(r"_+", "_", text).strip("_")
    return text if text not in {"", ".", ".."} else "experiment_run"


def write_provenance(
    output_dir: Path,
    *,
    benchmark: str,
    command: str | list[str],
    resolved_config: Any,
    environment_notes: str = "",
    assets: list[dict[str, Any]] | None = None,
    required_identities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Start a sanitized provenance record that is finalized on every terminal path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    secret_values = collect_secret_values(resolved_config)
    argv = _command_argv(command)
    repository_root = _repository_root()
    safe_argv = project_public_argv_paths(
        sanitize_command(argv, secret_values=secret_values),
        repository_root=repository_root,
    )
    sanitized_command = shlex.join(safe_argv)
    settings = project_public_path_fields(
        sanitize_artifact_value(resolved_config, secret_values=secret_values),
        repository_root=repository_root,
    )
    repository = project_public_path_fields(
        git_identity(repository_root, required=True, name="villageragent"),
        repository_root=repository_root,
    )
    lock_identity = project_public_path_fields(
        dependency_lock_identity(repository_root),
        repository_root=repository_root,
    )
    recorded_assets = [
        project_public_path_fields(
            sanitize_artifact_value(item, secret_values=secret_values),
            repository_root=repository_root,
        )
        for item in (assets or [])
    ]
    recorded_assets.extend(
        project_public_path_fields(
            sanitize_artifact_value(item, secret_values=secret_values),
            repository_root=repository_root,
        )
        for item in (required_identities or [])
    )
    reasons = _unverifiable_reasons([repository, lock_identity, *recorded_assets])
    started_at = _utc_now()
    provenance = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "benchmark": benchmark,
        "commit": repository.get("sha", "unknown"),
        "lifecycle": {
            "started_at": started_at,
            "ended_at": None,
            "duration_seconds": None,
            "status": "running",
        },
        "argv": safe_argv,
        "command": sanitized_command,
        "interpreter": {
            "executable": project_public_path(sys.executable, repository_root=repository_root),
            "implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "repository": repository,
        "dependency_lock": lock_identity,
        "effective_settings": settings,
        "assets": recorded_assets,
        "environment_notes": environment_notes,
        "environment_unverifiable": bool(reasons),
        "unverifiable_reasons": reasons,
    }
    (output_dir / "command.txt").write_text(sanitized_command + "\n", encoding="utf-8")
    _write_resolved_config(output_dir, settings)
    _write_json(output_dir / PROVENANCE_FILE, provenance)
    return provenance


def finalize_provenance(output_dir: Path, *, status: str) -> dict[str, Any]:
    if status not in {"success", "failure", "timeout"}:
        raise ValueError(f"Unsupported provenance terminal status: {status}")
    path = output_dir / PROVENANCE_FILE
    if not path.exists():
        return {}
    provenance = json.loads(path.read_text(encoding="utf-8"))
    ended_at = _utc_now()
    started = datetime.fromisoformat(provenance["lifecycle"]["started_at"].replace("Z", "+00:00"))
    ended = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    provenance["lifecycle"].update({
        "ended_at": ended_at,
        "duration_seconds": max(0.0, round((ended - started).total_seconds(), 6)),
        "status": status,
    })
    _write_json(path, provenance)
    return provenance


def update_provenance_assets(output_dir: Path, assets: list[dict[str, Any]]) -> dict[str, Any]:
    path = output_dir / PROVENANCE_FILE
    provenance = json.loads(path.read_text(encoding="utf-8"))
    updates = project_public_path_fields(
        sanitize_artifact_value(assets),
        repository_root=_repository_root(),
    )
    update_names = {item.get("name") for item in updates}
    provenance["assets"] = [
        item for item in provenance["assets"] if item.get("name") not in update_names
    ] + updates
    reasons = _unverifiable_reasons([
        provenance["repository"],
        provenance["dependency_lock"],
        *provenance["assets"],
    ])
    provenance["environment_unverifiable"] = bool(reasons)
    provenance["unverifiable_reasons"] = reasons
    _write_json(path, provenance)
    return provenance


def update_provenance_settings(output_dir: Path, resolved_config: Any) -> dict[str, Any]:
    path = output_dir / PROVENANCE_FILE
    provenance = json.loads(path.read_text(encoding="utf-8"))
    secret_values = collect_secret_values(resolved_config)
    settings = project_public_path_fields(
        sanitize_artifact_value(resolved_config, secret_values=secret_values),
        repository_root=_repository_root(),
    )
    provenance["effective_settings"] = settings
    _write_resolved_config(output_dir, settings)
    _write_json(path, provenance)
    return provenance


def file_identity(path: str | Path, *, name: str, kind: str, required: bool = True) -> dict[str, Any]:
    if not str(path).strip():
        return {
            "name": name,
            "kind": kind,
            "path": "",
            "required": required,
            "available": False,
            "reason": "path_not_configured",
        }
    candidate = Path(path).expanduser()
    identity: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "path": str(candidate),
        "required": required,
        "available": candidate.exists(),
    }
    if not candidate.exists():
        identity["reason"] = "missing"
        return identity
    if candidate.is_file():
        identity.update({"type": "file", "size": candidate.stat().st_size, "sha256": _sha256_file(candidate)})
        return identity
    if candidate.is_dir():
        size, digest = _directory_fingerprint(candidate)
        identity.update({"type": "directory", "size": size, "sha256": digest})
        return identity
    identity.update({"available": False, "reason": "unsupported_file_type"})
    return identity


def git_identity(path: str | Path, *, required: bool, name: str, kind: str = "repository") -> dict[str, Any]:
    if not str(path).strip():
        return {
            "name": name,
            "kind": kind,
            "path": "",
            "required": required,
            "available": False,
            "reason": "path_not_configured",
        }
    candidate = Path(path).expanduser()
    identity: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "path": str(candidate),
        "required": required,
        "available": False,
    }
    try:
        sha = _git(candidate, "rev-parse", "HEAD")
        dirty = bool(_git(candidate, "status", "--porcelain", "--untracked-files=normal"))
    except (OSError, subprocess.SubprocessError):
        identity["reason"] = "git_identity_unavailable"
        return identity
    identity.update({"available": True, "sha": sha, "dirty": dirty})
    return identity


def model_identity(*, name: str, provider: str, model: str, metadata: dict[str, Any] | None = None, required: bool = True) -> dict[str, Any]:
    metadata = metadata or {}
    immutable_id = metadata.get("digest") or metadata.get("revision") or metadata.get("system_fingerprint")
    identity = {
        "name": name,
        "kind": "model",
        "provider": provider,
        "model": model,
        "required": required,
        "available": bool(immutable_id),
    }
    identity.update({key: value for key, value in metadata.items() if value is not None})
    if not immutable_id:
        identity["reason"] = "immutable_model_identity_unavailable"
    return identity


def python_environment_identity(
    executable: str | Path,
    *,
    name: str,
    required: bool = True,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Fingerprint an interpreter and its installed Python distributions."""
    candidate = Path(executable).expanduser()
    identity: dict[str, Any] = {
        "name": name,
        "kind": "python_environment",
        "path": str(candidate),
        "required": required,
        "available": False,
    }
    if not candidate.exists():
        identity["reason"] = "missing"
        return identity
    script = (
        "import importlib.metadata as m,json,platform;"
        "packages=sorted((d.metadata.get('Name',''),d.version) for d in m.distributions());"
        "print(json.dumps({'implementation':platform.python_implementation(),"
        "'python_version':platform.python_version(),'packages':packages},sort_keys=True))"
    )
    try:
        completed = subprocess.run(
            [str(candidate), "-c", script],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError):
        identity["reason"] = "python_environment_identity_unavailable"
        return identity
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    identity.update({
        "available": True,
        "implementation": payload.get("implementation"),
        "python_version": payload.get("python_version"),
        "package_count": len(payload.get("packages", [])),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    })
    return identity


def dependency_lock_identity(root: Path) -> dict[str, Any]:
    identities = [file_identity(root / name, name=name, kind="dependency_lock") for name in _LOCK_FILES if (root / name).exists()]
    return {
        "name": "dependency_lock",
        "kind": "dependency_lock_set",
        "required": True,
        "available": bool(identities),
        "files": identities,
        **({} if identities else {"reason": "dependency_lock_missing"}),
    }


def _write_resolved_config(output_dir: Path, resolved_config: Any) -> None:
    if isinstance(resolved_config, dict):
        _write_json(output_dir / "config.resolved.json", resolved_config)
    with (output_dir / "config.resolved.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump(resolved_config, f, sort_keys=False, allow_unicode=True)


def _command_argv(command: str | list[str]) -> list[str]:
    if isinstance(command, list):
        return [str(argument) for argument in command]
    try:
        return shlex.split(command)
    except ValueError:
        return [command]


def project_public_path_fields(value: Any, *, repository_root: Path, field_name: str = "") -> Any:
    if isinstance(value, dict):
        return {
            key: project_public_path_fields(
                child,
                repository_root=repository_root,
                field_name=str(key),
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            project_public_path_fields(
                item,
                repository_root=repository_root,
                field_name=field_name,
            )
            for item in value
        ]
    if isinstance(value, str) and _is_path_field(field_name):
        return project_public_path(value, repository_root=repository_root)
    return value


def project_public_argv_paths(argv: list[str], *, repository_root: Path) -> list[str]:
    sanitized = []
    path_value_expected = False
    for index, argument in enumerate(argv):
        flag, separator, value = argument.partition("=")
        if separator and flag.startswith("--") and _is_path_field(flag.lstrip("-")):
            sanitized.append(f"{flag}={project_public_path(value, repository_root=repository_root)}")
            path_value_expected = False
            continue
        if index == 0 or path_value_expected:
            sanitized.append(project_public_path(argument, repository_root=repository_root))
            path_value_expected = False
            continue
        sanitized.append(argument)
        path_value_expected = argument.startswith("--") and _is_path_field(argument.lstrip("-"))
    return sanitized


def _is_path_field(field_name: str) -> bool:
    normalized = field_name.lower().replace("-", "_")
    return normalized in _PATH_FIELD_NAMES or normalized.endswith(_PATH_FIELD_SUFFIXES)


def project_public_path(value: str, *, repository_root: Path) -> str:
    windows_candidate = PureWindowsPath(value)
    if windows_candidate.is_absolute():
        windows_root = PureWindowsPath(str(repository_root))
        if windows_root.is_absolute():
            try:
                relative = windows_candidate.relative_to(windows_root)
            except ValueError:
                return "<external>"
            return relative.as_posix() or "."
        return "<external>"
    candidate = Path(value)
    if not candidate.is_absolute():
        return value
    try:
        relative = candidate.relative_to(repository_root)
    except ValueError:
        return "<external>"
    return relative.as_posix() or "."


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _git(path: Path, *arguments: str) -> str:
    return run_process(
        ["git", "-C", str(path), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_fingerprint(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    for child in sorted(item for item in path.rglob("*") if item.is_file() and not item.is_symlink()):
        relative = child.relative_to(path).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        child_size = child.stat().st_size
        size += child_size
        digest.update(child_size.to_bytes(8, "big"))
        with child.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
    return size, digest.hexdigest()


def _unverifiable_reasons(identities: list[dict[str, Any]]) -> list[str]:
    return sorted({
        f"{identity.get('kind', 'asset')}:{identity.get('name', 'unknown')}:{identity.get('reason', 'identity_unavailable')}"
        for identity in identities
        if identity.get("required") and not identity.get("available")
    })


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
