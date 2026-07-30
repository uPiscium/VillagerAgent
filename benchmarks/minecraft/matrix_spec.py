from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from benchmarks.minecraft import seed_contract, world_snapshot

from benchmarks.minecraft.matrix_variants import (
    VARIANT_ORDER,
    MovementTarget,
    get_movement_variant,
)
from benchmarks.minecraft.matrix_validation import (
    SCANNER_ID,
    SCANNER_PATTERNS_VERSION,
    SCANNER_SCHEMA_VERSION,
    SCANNER_SHA256,
    scanner_implementation_sha256,
)


MATRIX_SCHEMA_VERSION = 1
MATRIX_RUN_COUNT = 12
LIFECYCLE_STATES = frozenset({"draft", "validated", "finalized"})
SHA256_LENGTH = 64


@dataclass(frozen=True)
class BaselineIdentity:
    baseline_id: str
    path: str
    sha256: str


@dataclass(frozen=True)
class RuntimeIdentity:
    name: str
    image: str
    digest: str


@dataclass(frozen=True)
class ModelIdentity:
    provider: str
    name: str
    digest: str


@dataclass(frozen=True)
class ScannerIdentity:
    name: str
    schema_version: int
    implementation_sha256: str
    patterns_version: int
    patterns_sha256: str


@dataclass(frozen=True)
class GenerationConfig:
    temperature: float
    top_p: float
    max_tokens: int
    timeout_seconds: float
    max_iterations: int


@dataclass(frozen=True)
class ExecutionPolicy:
    mode: str
    stop_on_first_failure: bool
    retry_policy: str


@dataclass(frozen=True)
class SeedScopeContract:
    requested: tuple[str, ...]
    supported: tuple[str, ...]
    applied: tuple[str, ...]


@dataclass(frozen=True)
class MatrixRunSpec:
    order: int
    run_id: str
    variant: str
    seed: int
    baseline_id: str
    snapshot_path: str
    snapshot_sha256: str
    prompt: str
    initial_state: MovementTarget
    evaluation_target: MovementTarget
    expected_completion_policy: str
    expected_completion_semantics: str
    target_tolerance: float
    variant_definition_sha256: str
    seed_scopes: SeedScopeContract


@dataclass(frozen=True)
class MatrixSpec:
    schema_version: int
    matrix_id: str
    lifecycle_state: str
    premanifest_sha256: str | None
    revision: str
    seeds: tuple[int, ...]
    baselines: tuple[BaselineIdentity, ...]
    runtime: RuntimeIdentity
    model: ModelIdentity
    scanner: ScannerIdentity
    generation: GenerationConfig
    execution: ExecutionPolicy
    runs: tuple[MatrixRunSpec, ...]


def parse_matrix_spec(payload: str | bytes | Mapping[str, Any]) -> MatrixSpec:
    if isinstance(payload, (str, bytes)):
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("matrix spec must be valid JSON") from exc
    else:
        raw = dict(payload)
    _require_keys(
        raw,
        {
            "schema_version", "lifecycle_state", "premanifest_sha256", "revision",
            "matrix_id", "seeds", "baselines", "runtime", "model", "scanner", "generation",
            "execution", "runs",
        },
        "matrix spec",
    )
    return MatrixSpec(
        schema_version=raw["schema_version"],
        matrix_id=raw["matrix_id"],
        lifecycle_state=raw["lifecycle_state"],
        premanifest_sha256=raw["premanifest_sha256"],
        revision=raw["revision"],
        seeds=_tuple_of(raw["seeds"], "seeds"),
        baselines=tuple(_parse_baseline(item) for item in _list(raw["baselines"], "baselines")),
        runtime=_parse_record(RuntimeIdentity, raw["runtime"], "runtime", {"name", "image", "digest"}),
        model=_parse_record(ModelIdentity, raw["model"], "model", {"provider", "name", "digest"}),
        scanner=_parse_record(
            ScannerIdentity,
            raw["scanner"],
            "scanner",
            {"name", "schema_version", "implementation_sha256", "patterns_version", "patterns_sha256"},
        ),
        generation=_parse_record(
            GenerationConfig, raw["generation"], "generation",
            {"temperature", "top_p", "max_tokens", "timeout_seconds", "max_iterations"},
        ),
        execution=_parse_record(
            ExecutionPolicy, raw["execution"], "execution",
            {"mode", "stop_on_first_failure", "retry_policy"},
        ),
        runs=tuple(_parse_run(item) for item in _list(raw["runs"], "runs")),
    )


def load_matrix_spec(path: str | Path, *, repo_root: str | Path | None = None) -> MatrixSpec:
    spec = parse_matrix_spec(Path(path).read_text(encoding="utf-8"))
    return validate_matrix_spec(spec, repo_root=repo_root)


def validate_matrix_spec(
    spec: MatrixSpec, *, repo_root: str | Path | None = None
) -> MatrixSpec:
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    if isinstance(spec.schema_version, bool) or spec.schema_version != MATRIX_SCHEMA_VERSION:
        raise ValueError(f"unsupported matrix schema version: {spec.schema_version!r}")
    _nonempty(spec.matrix_id, "matrix ID")
    if spec.lifecycle_state not in LIFECYCLE_STATES:
        raise ValueError(f"unknown matrix lifecycle state: {spec.lifecycle_state!r}")
    if spec.lifecycle_state != "finalized" and spec.premanifest_sha256 is not None:
        raise ValueError("only a finalized matrix may contain premanifest_sha256")
    _validate_revision(spec.revision, root)
    _validate_axes(spec)
    _validate_identities(spec)
    _validate_generation(spec.generation)
    if spec.execution != ExecutionPolicy("sequential", True, "none"):
        raise ValueError("execution must be sequential, stop on first failure, with retry policy none")
    _validate_runs(spec, root)
    if spec.lifecycle_state == "finalized":
        expected = matrix_spec_sha256(spec)
        if spec.premanifest_sha256 != expected:
            raise ValueError("finalized premanifest hash mismatch; the matrix has drifted")
        return spec
    return replace(spec, lifecycle_state="validated")


def finalize_matrix_spec(
    spec: MatrixSpec, *, repo_root: str | Path | None = None
) -> MatrixSpec:
    if spec.lifecycle_state == "draft":
        raise ValueError("draft matrix must be validated before finalization")
    if spec.lifecycle_state == "finalized":
        return validate_matrix_spec(spec, repo_root=repo_root)
    validated = validate_matrix_spec(spec, repo_root=repo_root)
    finalized = replace(validated, lifecycle_state="finalized", premanifest_sha256=None)
    finalized = replace(finalized, premanifest_sha256=matrix_spec_sha256(finalized))
    return validate_matrix_spec(finalized, repo_root=repo_root)


def matrix_spec_sha256(spec: MatrixSpec) -> str:
    payload = matrix_spec_to_dict(spec)
    payload.pop("premanifest_sha256")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def matrix_spec_to_dict(spec: MatrixSpec) -> dict[str, Any]:
    return {
        "schema_version": spec.schema_version,
        "matrix_id": spec.matrix_id,
        "lifecycle_state": spec.lifecycle_state,
        "premanifest_sha256": spec.premanifest_sha256,
        "revision": spec.revision,
        "seeds": list(spec.seeds),
        "baselines": [vars(item) for item in spec.baselines],
        "runtime": vars(spec.runtime),
        "model": vars(spec.model),
        "scanner": vars(spec.scanner),
        "generation": vars(spec.generation),
        "execution": vars(spec.execution),
        "runs": [
            {
                "order": run.order,
                "run_id": run.run_id,
                "variant": run.variant,
                "seed": run.seed,
                "baseline_id": run.baseline_id,
                "snapshot_path": run.snapshot_path,
                "snapshot_sha256": run.snapshot_sha256,
                "prompt": run.prompt,
                "initial_state": run.initial_state.as_dict(),
                "evaluation_target": run.evaluation_target.as_dict(),
                "expected_completion_policy": run.expected_completion_policy,
                "expected_completion_semantics": run.expected_completion_semantics,
                "target_tolerance": run.target_tolerance,
                "variant_definition_sha256": run.variant_definition_sha256,
                "seed_scopes": {
                    "requested": list(run.seed_scopes.requested),
                    "supported": list(run.seed_scopes.supported),
                    "applied": list(run.seed_scopes.applied),
                },
            }
            for run in spec.runs
        ],
    }


def write_finalized_matrix_spec(spec: MatrixSpec, path: str | Path) -> Path:
    """Atomically serialize a finalized premanifest and make it read-only best effort."""
    if spec.lifecycle_state != "finalized":
        raise ValueError("only a finalized matrix may be written as a premanifest")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(matrix_spec_to_dict(spec), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    try:
        destination.chmod(0o444)
    except OSError:
        pass
    return destination


def _validate_axes(spec: MatrixSpec) -> None:
    if len(spec.seeds) != 2 or len(set(spec.seeds)) != 2:
        raise ValueError("matrix must declare exactly two distinct seeds")
    if any(isinstance(seed, bool) or not isinstance(seed, int) for seed in spec.seeds):
        raise ValueError("matrix seeds must be integers")
    if len(spec.baselines) != 2:
        raise ValueError("matrix must declare exactly two baselines")
    if len({item.baseline_id for item in spec.baselines}) != 2:
        raise ValueError("matrix baseline IDs must be unique")
    if len({item.sha256 for item in spec.baselines}) != 2:
        raise ValueError("matrix baselines must have genuinely distinct SHA-256 identities")
    for baseline in spec.baselines:
        _nonempty(baseline.baseline_id, "baseline ID")
        _nonempty(baseline.path, f"baseline {baseline.baseline_id} path")
        path = Path(baseline.path)
        if path.is_absolute() or ".." in path.parts or "\\" in baseline.path:
            raise ValueError("baseline snapshot paths must be repository-relative POSIX paths")


def _validate_identities(spec: MatrixSpec) -> None:
    for name, value in (
        ("runtime name", spec.runtime.name), ("runtime image", spec.runtime.image),
        ("model provider", spec.model.provider), ("model name", spec.model.name),
        ("scanner name", spec.scanner.name),
    ):
        _nonempty(value, name)
    _sha256(spec.runtime.digest, "runtime digest", allow_prefix=True)
    _sha256(spec.model.digest, "model digest", allow_prefix=True)
    _sha256(spec.scanner.implementation_sha256, "scanner implementation sha256")
    _sha256(spec.scanner.patterns_sha256, "scanner patterns sha256")
    expected_scanner = ScannerIdentity(
        name=SCANNER_ID,
        schema_version=SCANNER_SCHEMA_VERSION,
        implementation_sha256=scanner_implementation_sha256(),
        patterns_version=SCANNER_PATTERNS_VERSION,
        patterns_sha256=SCANNER_SHA256,
    )
    if spec.scanner != expected_scanner:
        raise ValueError("scanner identity does not match the matrix safety implementation")
    if spec.runtime.image == spec.runtime.digest:
        raise ValueError("runtime identity must not use a mutable image name as its digest")


def _validate_generation(config: GenerationConfig) -> None:
    for name, value in (("temperature", config.temperature), ("top_p", config.top_p), ("timeout_seconds", config.timeout_seconds)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"generation {name} must be finite numeric")
    if config.temperature < 0 or not 0 < config.top_p <= 1 or config.timeout_seconds <= 0:
        raise ValueError("generation temperature/top_p/timeout_seconds are out of range")
    for name, value in (("max_tokens", config.max_tokens), ("max_iterations", config.max_iterations)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"generation {name} must be a positive integer")


def _validate_runs(spec: MatrixSpec, root: Path) -> None:
    if len(spec.runs) != MATRIX_RUN_COUNT:
        raise ValueError(f"matrix must contain exactly {MATRIX_RUN_COUNT} runs")
    if len({run.run_id for run in spec.runs}) != MATRIX_RUN_COUNT:
        raise ValueError("matrix run IDs must be unique")
    expected = [
        (variant, seed, baseline.baseline_id)
        for variant in VARIANT_ORDER
        for seed in spec.seeds
        for baseline in spec.baselines
    ]
    actual = [(run.variant, run.seed, run.baseline_id) for run in spec.runs]
    orders = [run.order for run in spec.runs]
    if (
        actual != expected
        or any(isinstance(order, bool) or not isinstance(order, int) for order in orders)
        or orders != list(range(MATRIX_RUN_COUNT))
    ):
        raise ValueError("runs must be the complete explicitly ordered variant x seed x baseline product")
    baselines = {item.baseline_id: item for item in spec.baselines}
    verified_snapshots: set[tuple[str, str]] = set()
    for run in spec.runs:
        _nonempty(run.run_id, "run ID")
        variant = get_movement_variant(run.variant)
        baseline = baselines[run.baseline_id]
        if (run.snapshot_path, run.snapshot_sha256) != (baseline.path, baseline.sha256):
            raise ValueError(f"run {run.run_id!r} snapshot identity does not match its baseline")
        if (
            run.prompt != variant.prompt
            or run.initial_state != variant.initial_position
            or run.evaluation_target != variant.target
            or run.expected_completion_policy != variant.completion_policy
            or run.expected_completion_semantics != variant.completion_semantics
            or run.target_tolerance != variant.tolerance
        ):
            raise ValueError(f"run {run.run_id!r} movement contract does not match its variant")
        if run.variant_definition_sha256 != variant.definition_sha256:
            raise ValueError(f"run {run.run_id!r} variant definition hash mismatch")
        _validate_seed_scopes(run.seed, run.seed_scopes)
        identity = (baseline.path, baseline.sha256)
        if identity not in verified_snapshots:
            _verify_snapshot(root, baseline)
            verified_snapshots.add(identity)


def _validate_seed_scopes(seed: int, contract: SeedScopeContract) -> None:
    if len(set(contract.requested)) != len(contract.requested) or len(set(contract.supported)) != len(contract.supported) or len(set(contract.applied)) != len(contract.applied):
        raise ValueError("seed scope lists must not contain duplicates")
    requested, supported, applied = map(set, (contract.requested, contract.supported, contract.applied))
    if not requested:
        raise ValueError("at least one seed scope must be requested")
    if not requested <= supported:
        raise ValueError("requested seed scopes must all be supported")
    if applied != requested:
        raise ValueError("requested seed scopes must be exactly the applied scopes")
    seed_contract.resolve_seed_contract(
        {"seed": seed, "requested_scopes": list(contract.requested)},
        supported_scopes=contract.supported,
        applied_scopes=contract.applied,
    )


def _verify_snapshot(root: Path, baseline: BaselineIdentity) -> None:
    _sha256(baseline.sha256, f"baseline {baseline.baseline_id} sha256")
    path = Path(baseline.path)
    path = path if path.is_absolute() else root / path
    if not path.is_file():
        raise ValueError(f"baseline snapshot does not exist: {baseline.path}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != baseline.sha256:
        raise ValueError(f"baseline snapshot hash mismatch: {baseline.path}")
    world_snapshot.WorldSnapshotDescriptor(
        snapshot_id=baseline.baseline_id,
        archive_path=path,
        archive_sha256=baseline.sha256,
    )


def _validate_revision(revision: str, root: Path) -> None:
    if not isinstance(revision, str) or len(revision) != 40 or any(
        char not in "0123456789abcdef" for char in revision
    ):
        raise ValueError("revision must be a full lowercase Git SHA")
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True,
            text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("unable to resolve current HEAD revision") from exc
    if revision != head:
        raise ValueError(f"matrix revision {revision} does not match current HEAD {head}")


def _parse_baseline(raw: Any) -> BaselineIdentity:
    return _parse_record(BaselineIdentity, raw, "baseline", {"baseline_id", "path", "sha256"})


def _parse_run(raw: Any) -> MatrixRunSpec:
    _require_keys(
        raw,
        {"order", "run_id", "variant", "seed", "baseline_id", "snapshot_path", "snapshot_sha256", "prompt", "initial_state", "evaluation_target", "expected_completion_policy", "expected_completion_semantics", "target_tolerance", "variant_definition_sha256", "seed_scopes"},
        "run",
    )
    target = _parse_record(MovementTarget, raw["evaluation_target"], "evaluation_target", {"x", "y", "z"})
    initial_state = _parse_record(MovementTarget, raw["initial_state"], "initial_state", {"x", "y", "z"})
    scopes = _parse_record(SeedScopeContract, raw["seed_scopes"], "seed_scopes", {"requested", "supported", "applied"})
    scopes = SeedScopeContract(
        requested=_tuple_of(scopes.requested, "requested seed scopes"),
        supported=_tuple_of(scopes.supported, "supported seed scopes"),
        applied=_tuple_of(scopes.applied, "applied seed scopes"),
    )
    return MatrixRunSpec(
        order=raw["order"], run_id=raw["run_id"], variant=raw["variant"], seed=raw["seed"],
        baseline_id=raw["baseline_id"], snapshot_path=raw["snapshot_path"],
        snapshot_sha256=raw["snapshot_sha256"], prompt=raw["prompt"],
        initial_state=initial_state, evaluation_target=target,
        expected_completion_policy=raw["expected_completion_policy"],
        expected_completion_semantics=raw["expected_completion_semantics"],
        target_tolerance=raw["target_tolerance"],
        variant_definition_sha256=raw["variant_definition_sha256"],
        seed_scopes=scopes,
    )


def _parse_record(cls, raw: Any, context: str, keys: set[str]):
    _require_keys(raw, keys, context)
    return cls(**raw)


def _require_keys(raw: Any, expected: set[str], context: str) -> None:
    if not isinstance(raw, dict):
        raise ValueError(f"{context} must be an object")
    actual = set(raw)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"{context} schema mismatch; missing={missing}, extra={extra}")


def _list(value: Any, context: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    return value


def _tuple_of(value: Any, context: str) -> tuple:
    return tuple(_list(value, context)) if isinstance(value, list) else tuple(value) if isinstance(value, tuple) else _raise_array(context)


def _raise_array(context: str):
    raise ValueError(f"{context} must be an array")


def _nonempty(value: Any, context: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be a non-empty string")


def _sha256(value: Any, context: str, *, allow_prefix: bool = False) -> None:
    _nonempty(value, context)
    candidate = value.removeprefix("sha256:") if allow_prefix else value
    if len(candidate) != SHA256_LENGTH or any(char not in "0123456789abcdef" for char in candidate):
        raise ValueError(f"{context} must be a lowercase SHA-256 digest")
