import hashlib
import copy
import json
import subprocess
from pathlib import Path

import pytest

from benchmarks.minecraft.approved_experiment import (
    ApprovedExperimentError,
    get_approved_experiment,
    load_registry,
    resolve_approved_experiment,
)
from benchmarks.minecraft.matrix_spec import matrix_spec_sha256, parse_matrix_spec
from benchmarks.minecraft.matrix_validation import (
    SCANNER_ID, SCANNER_PATTERNS_VERSION, SCANNER_SCHEMA_VERSION, SCANNER_SHA256,
    scanner_implementation_sha256,
)
from benchmarks.minecraft.matrix_variants import VARIANT_ORDER, get_movement_variant


def _run(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, text=True,
                          capture_output=True).stdout.strip()


def _repo(tmp_path, name):
    root = tmp_path / name
    root.mkdir()
    (root / "README").write_text("fixture\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    _run(root, "add", ".")
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "fixture"], cwd=root, check=True)
    return root


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _payload(root, revision):
    a, b = root / "a.tar.gz", root / "b.tar.gz"
    a.write_bytes(b"baseline A")
    b.write_bytes(b"baseline B")
    baselines = [{"baseline_id": "a", "path": a.name, "sha256": _sha(a.read_bytes())},
                 {"baseline_id": "b", "path": b.name, "sha256": _sha(b.read_bytes())}]
    runs = []
    for variant_id in VARIANT_ORDER:
        variant = get_movement_variant(variant_id)
        for seed in (17, 29):
            for baseline in baselines:
                runs.append({"order": len(runs), "run_id": f"{variant_id}-{seed}-{baseline['baseline_id']}",
                    "variant": variant_id, "seed": seed, "baseline_id": baseline["baseline_id"],
                    "snapshot_path": baseline["path"], "snapshot_sha256": baseline["sha256"],
                    "prompt": variant.prompt, "initial_state": variant.initial_position.as_dict(),
                    "evaluation_target": variant.target.as_dict(), "position_convention": variant.position_convention,
                    "expected_completion_policy": variant.completion_policy,
                    "expected_completion_semantics": variant.completion_semantics,
                    "target_tolerance": variant.tolerance, "variant_definition_sha256": variant.definition_sha256,
                    "seed_scopes": {"requested": ["meta_judger"], "supported": ["meta_judger"], "applied": ["meta_judger"]}})
    return {"schema_version": 2, "matrix_id": "approved-fixture", "lifecycle_state": "finalized",
        "premanifest_sha256": None, "revision": revision, "seeds": [17, 29], "baselines": baselines,
        "runtime": {"name": "minecraft-runtime", "image": "example/runtime:matrix", "digest": "sha256:" + "1" * 64},
        "model": {"provider": "ollama", "name": "model:tag", "digest": "2" * 64},
        "scanner": {"name": SCANNER_ID, "schema_version": SCANNER_SCHEMA_VERSION,
          "implementation_sha256": scanner_implementation_sha256(), "patterns_version": SCANNER_PATTERNS_VERSION,
          "patterns_sha256": SCANNER_SHA256},
        "generation": {"temperature": 0.0, "top_p": 1.0, "max_tokens": 2048, "timeout_seconds": 600.0, "max_iterations": 20},
        "execution": {"mode": "sequential", "stop_on_first_failure": True, "retry_policy": "none"}, "runs": runs}


def _fixture(tmp_path):
    root = _repo(tmp_path, "source")
    # Baselines are part of the approved revision, just as they are in a real checkout.
    (root / "a.tar.gz").write_bytes(b"baseline A")
    (root / "b.tar.gz").write_bytes(b"baseline B")
    _run(root, "add", ".")
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "baselines"], cwd=root, check=True)
    revision = _run(root, "rev-parse", "HEAD")
    payload = _payload(root, revision)
    payload["premanifest_sha256"] = matrix_spec_sha256(parse_matrix_spec(payload))
    artifact = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    return root, revision, payload, artifact


def _record(payload, artifact, revision, **changes):
    ordered_runs = [
        {key: run[key] for key in (
            "order", "run_id", "variant", "seed", "baseline_id",
            "variant_definition_sha256",
        )}
        for run in payload["runs"]
    ]
    record = {
        "schema_version": 1,
        "experiment_id": "exp-1",
        "approved_source_revision": revision,
        "canonical_premanifest_identity": payload["premanifest_sha256"],
        "runtime_identity": payload["runtime"],
        "model_endpoint": "http://ollama.example:11434",
        "artifact": {
            "provider": "github-gist",
            "owner": "test-owner",
            "gist_id": "1" * 32,
            "revision": "a" * 40,
            "path": "premanifest.json",
            "byte_sha256": _sha(artifact),
        },
        "expected": {
            "model": payload["model"],
            "seeds": payload["seeds"],
            "baselines": payload["baselines"],
            "generation": payload["generation"],
            "execution": payload["execution"],
            "cleanup_policy": "independent_snapshot_per_run",
            "ordered_runs": ordered_runs,
        },
    }
    record.update(changes)
    return record


def _registry(root, *records):
    d = root / "registry"; d.mkdir(parents=True, exist_ok=True)
    for i, record in enumerate(records):
        (d / f"{i}.json").write_text(json.dumps(record))
    return d


def _assert_error(fn, text=None):
    with pytest.raises((ApprovedExperimentError, ValueError, KeyError)) as exc:
        fn()
    if text:
        assert text.lower() in str(exc.value).lower()


def test_valid_registry_unknown_lookup_and_duplicate_ids(tmp_path):
    root, revision, payload, artifact = _fixture(tmp_path)
    record = _record(payload, artifact, revision)
    registry = _registry(root, record)
    loaded = load_registry(registry)
    assert get_approved_experiment("exp-1", registry).experiment_id == "exp-1"
    _assert_error(lambda: get_approved_experiment("missing", registry), "unknown")
    _assert_error(lambda: load_registry(_registry(root, record, record)), "duplicate")


def test_committed_production_registry_preserves_approved_identity():
    record = get_approved_experiment("minecraft-judged-production-v1")
    assert record.approved_source_revision == "b2e1f1f35878b8028bf6139ee4fd8d19b5337aa5"
    assert record.canonical_premanifest_identity == "cfb26bd54dbeedc76f7a3804867d3320de7afe1f619778fee1b1737a4509fa2f"
    assert record.runtime_identity["digest"] == "sha256:63e59662fd8d8b79d99b9910225455af4addfe5e80d5a65023cbaa8ca37c73d0"
    assert record.model_endpoint == "http://10.255.255.5:11434"
    assert record.artifact.revision == "b07eabc43e62f199990e475cb3848933382e3d36"
    assert record.artifact.path == "premanifest.json"
    assert record.artifact.byte_sha256 == "716fe1bfc85ce683e18fbaca8641ebf9065992367635385255dafeabdfbe1760"


@pytest.mark.parametrize("change", [
    lambda r: r.update(extra=True), lambda r: r["runtime_identity"].update(extra=True),
    lambda r: r["expected"]["generation"].update(extra=True),
    lambda r: r["artifact"].update(extra=True),
    lambda r: r.update(approved_source_revision="bad"),
    lambda r: r.update(canonical_premanifest_identity="g" * 64),
    lambda r: r["artifact"].update(byte_sha256="0" * 63),
    lambda r: r["artifact"].update(path=""),
    lambda r: r["artifact"].update(revision="latest"),
    lambda r: r["expected"].update(cleanup_policy="mutable"),
])
def test_registry_rejects_unknown_fields_malformed_identities_and_mutable_artifacts(tmp_path, change):
    root, revision, payload, artifact = _fixture(tmp_path)
    record = _record(payload, artifact, revision); change(record)
    _assert_error(lambda: load_registry(_registry(root, record)))


class Response:
    def __init__(self, body, status=200, url="https://api.github.com/gists/" + "1" * 32 + "/" + "a" * 40, chunks=None):
        self.body, self.status, self.url, self.chunks, self.used = body, status, url, chunks, False
        self.headers = {}
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def geturl(self): return self.url
    def read(self, size=-1):
        if self.chunks is not None:
            item = self.chunks.pop(0) if self.chunks else b""
            if isinstance(item, BaseException):
                raise item
            return item
        if self.used:
            return b""
        self.used = True
        return self.body


def _network(monkeypatch, metadata, artifact, captured=None, **kwargs):
    def urlopen(request, timeout=None):
        url = request.full_url
        if captured is not None:
            captured.append(url)
        if "api.github.com" in url:
            return Response(metadata, **kwargs)
        return Response(artifact, url="https://gist.githubusercontent.com/test-owner/" + "1" * 32 + "/raw/" + "a" * 40 + "/premanifest.json", **kwargs)
    monkeypatch.setattr("benchmarks.minecraft.approved_experiment.urllib.request.urlopen", urlopen)


def _resolve(monkeypatch, root, revision, payload, artifact, tmp_path, **kwargs):
    execution = tmp_path / "execution"
    subprocess.run(["git", "clone", "-q", str(root), str(execution)], check=True)
    record = _record(payload, artifact, revision)
    registry = _registry(tmp_path, record)
    metadata = json.dumps({"owner": {"login": "test-owner"}, "files": {"premanifest.json": {}}}).encode()
    _network(monkeypatch, metadata, artifact)
    result = resolve_approved_experiment("exp-1", tmp_path / "output", execution, registry, **kwargs)
    return result, execution, record


def test_resolution_exact_revision_file_canonical_success_and_cleanup(tmp_path, monkeypatch):
    root, revision, payload, artifact = _fixture(tmp_path)
    captured = []
    execution = tmp_path / "execution"
    subprocess.run(["git", "clone", "-q", str(root), str(execution)], check=True)
    record = _record(payload, artifact, revision)
    registry = _registry(tmp_path, record)
    metadata = json.dumps({"owner": {"login": "test-owner"}, "files": {"premanifest.json": {}}}).encode()
    _network(monkeypatch, metadata, artifact, captured=captured)
    result = resolve_approved_experiment("exp-1", tmp_path / "output", execution, registry)
    assert result.spec.revision == revision
    assert result.premanifest_path.read_bytes() == artifact
    assert result.provenance_path.exists()
    assert not list((tmp_path).glob(".output.*"))
    assert not _run(execution, "status", "--porcelain")
    assert captured == [
        "https://api.github.com/gists/" + "1" * 32 + "/" + "a" * 40,
        "https://gist.githubusercontent.com/test-owner/" + "1" * 32
        + "/raw/" + "a" * 40 + "/premanifest.json",
    ]


def test_resolution_rejects_report_markdown_and_canonical_mismatch(tmp_path, monkeypatch):
    root, revision, payload, artifact = _fixture(tmp_path)
    execution = tmp_path / "execution"; subprocess.run(["git", "clone", "-q", str(root), str(execution)], check=True)
    for body in (b"# report\n", b"**markdown**\n"):
        record = _record(payload, body, revision)
        registry = _registry(tmp_path / ("r" + str(len(body))), record)
        _network(monkeypatch, json.dumps({"owner": {"login": "test-owner"}, "files": {"premanifest.json": {}}}).encode(), body)
        _assert_error(lambda: resolve_approved_experiment("exp-1", tmp_path / "output", execution, registry))
    record = _record(payload, artifact, revision, canonical_premanifest_identity="0" * 64)
    registry = _registry(tmp_path / "canonical", record)
    _network(monkeypatch, json.dumps({"owner": {"login": "test-owner"}, "files": {"premanifest.json": {}}}).encode(), artifact)
    _assert_error(lambda: resolve_approved_experiment("exp-1", tmp_path / "output", execution, registry), "identity")


def test_interrupted_fetch_and_atomic_write_leave_no_partial_output(tmp_path, monkeypatch):
    root, revision, payload, artifact = _fixture(tmp_path)
    execution = tmp_path / "execution"; subprocess.run(["git", "clone", "-q", str(root), str(execution)], check=True)
    record = _record(payload, artifact, revision); registry = _registry(tmp_path, record)
    _network(monkeypatch, json.dumps({"owner": {"login": "test-owner"}, "files": {"premanifest.json": {}}}).encode(), artifact,
             chunks=[artifact[:10], OSError("interrupted")])
    _assert_error(lambda: resolve_approved_experiment("exp-1", tmp_path / "output", execution, registry))
    assert not (tmp_path / "output").exists()
    assert not list(tmp_path.glob(".output.*"))

    _network(monkeypatch, json.dumps({"owner": {"login": "test-owner"}, "files": {"premanifest.json": {}}}).encode(), artifact)
    calls = 0
    original_write = __import__(
        "benchmarks.minecraft.approved_experiment", fromlist=["_write_bytes_durable"]
    )._write_bytes_durable

    def interrupted_write(path, body):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("interrupted")
        original_write(path, body)

    monkeypatch.setattr(
        "benchmarks.minecraft.approved_experiment._write_bytes_durable",
        interrupted_write,
    )
    with pytest.raises(OSError, match="interrupted"):
        resolve_approved_experiment(
            "exp-1", tmp_path / "output", execution, registry
        )
    assert not (tmp_path / "output").exists()
    assert not list(tmp_path.glob(".output.tmp-*"))


@pytest.mark.parametrize("case", ["404", "timeout", "metadata_limit", "artifact_limit", "bytes", "missing_file"])
def test_resolution_transport_size_file_and_byte_failures(tmp_path, monkeypatch, case):
    root, revision, payload, artifact = _fixture(tmp_path)
    execution = tmp_path / "execution"; subprocess.run(["git", "clone", "-q", str(root), str(execution)], check=True)
    record = _record(payload, artifact, revision); registry = _registry(tmp_path, record)
    if case == "404":
        monkeypatch.setattr("benchmarks.minecraft.approved_experiment.urllib.request.urlopen", lambda *a, **k: Response(b"", 404))
    elif case == "timeout":
        def timeout(*a, **k): raise TimeoutError("timeout")
        monkeypatch.setattr("benchmarks.minecraft.approved_experiment.urllib.request.urlopen", timeout)
    else:
        metadata = b"{}" if case == "missing_file" else json.dumps({"owner": {"login": "test-owner"}, "files": {"premanifest.json": {}}}).encode()
        body = artifact + b"x" if case == "bytes" else artifact
        _network(monkeypatch, metadata, body)
    options = {"max_metadata_bytes": 1} if case == "metadata_limit" else {"max_artifact_bytes": 1} if case == "artifact_limit" else {}
    _assert_error(lambda: resolve_approved_experiment("exp-1", tmp_path / "output", execution, registry, **options))


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(seeds=[99, 100]), lambda p: p["generation"].update(max_tokens=1),
    lambda p: p["execution"].update(retry_policy="automatic"), lambda p: p["runs"][0].update(order=99),
    lambda p: p["runs"][0].update(variant="diagonal"),
])
def test_approved_revision_runtime_endpoint_and_matrix_contract_drift_rejected(tmp_path, monkeypatch, mutation):
    root, revision, payload, artifact = _fixture(tmp_path)
    mutation(payload)
    artifact = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    _assert_error(lambda: _resolve(monkeypatch, root, revision, payload, artifact, tmp_path))


@pytest.mark.parametrize(("mutation", "message"), [
    (lambda p: p["runtime"].update(digest="sha256:" + "9" * 64), "runtime"),
    (lambda p: p["model"].update(digest="8" * 64), "model"),
    (lambda p: p.update(seeds=[29, 17]), "seed"),
    (lambda p: p["baselines"].reverse(), "baseline"),
    (lambda p: p["generation"].update(max_tokens=1024), "generation"),
    (lambda p: p["execution"].update(retry_policy="manual"), "retry"),
    (lambda p: p["runs"][0].update(run_id="changed-run"), "run"),
])
def test_explicit_approved_contract_drift_is_rejected_even_with_new_canonical_hash(
    tmp_path, monkeypatch, mutation, message
):
    root, revision, approved_payload, _ = _fixture(tmp_path)
    drifted = copy.deepcopy(approved_payload)
    mutation(drifted)
    drifted["premanifest_sha256"] = None
    drifted["premanifest_sha256"] = matrix_spec_sha256(parse_matrix_spec(drifted))
    artifact = json.dumps(drifted, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    record = _record(approved_payload, b"placeholder", revision)
    record["canonical_premanifest_identity"] = drifted["premanifest_sha256"]
    record["artifact"]["byte_sha256"] = _sha(artifact)
    registry = _registry(tmp_path, record)
    execution = tmp_path / "execution"
    subprocess.run(["git", "clone", "-q", str(root), str(execution)], check=True)
    metadata = json.dumps({
        "owner": {"login": "test-owner"},
        "files": {"premanifest.json": {}},
    }).encode()
    _network(monkeypatch, metadata, artifact)

    _assert_error(
        lambda: resolve_approved_experiment(
            "exp-1", tmp_path / "output", execution, registry
        ),
        message,
    )


def test_execution_revision_dirty_and_output_worktree_rejected_active_checkout_unchanged(tmp_path, monkeypatch):
    root, revision, payload, artifact = _fixture(tmp_path)
    execution = tmp_path / "execution"; subprocess.run(["git", "clone", "-q", str(root), str(execution)], check=True)
    record = _record(payload, artifact, revision); registry = _registry(tmp_path, record)
    _network(monkeypatch, json.dumps({"owner": {"login": "test-owner"}, "files": {"premanifest.json": {}}}).encode(), artifact)
    before = _run(execution, "status", "--porcelain")
    _assert_error(lambda: resolve_approved_experiment("exp-1", execution, execution, registry), "output")
    control_plane_output = Path(__file__).resolve().parents[1] / "resolver-must-not-create"
    _assert_error(
        lambda: resolve_approved_experiment(
            "exp-1", control_plane_output, execution, registry
        ),
        "outside",
    )
    assert not control_plane_output.exists()
    assert _run(execution, "status", "--porcelain") == before
    (execution / "dirty").write_text("dirty")
    _assert_error(lambda: resolve_approved_experiment("exp-1", tmp_path / "output", execution, registry), "clean")
    (execution / "dirty").unlink()
    (execution / "newer").write_text("new revision")
    _run(execution, "add", ".")
    subprocess.run([
        "git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "new revision",
    ], cwd=execution, check=True)
    _assert_error(
        lambda: resolve_approved_experiment(
            "exp-1", tmp_path / "output", execution, registry
        ),
        "revision",
    )
