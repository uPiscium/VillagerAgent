import json
import io
import subprocess
import zipfile

import pytest

from benchmarks.common.publish_bundle import (
    GitHubReleasePublisher,
    LocalStagingPublisher,
    PublicationError,
    PublicBundleValidationError,
    build_deterministic_archive,
    check_paper_result_archives,
    derive_public_bundle,
    publish_bundle,
    validate_public_bundle,
)
from benchmarks.common.run_artifacts import finalize_run_directory, prepare_run_directory


def _bundle(tmp_path, *, extra=None):
    bundle = tmp_path / "bundle"
    attempt_id = prepare_run_directory(bundle, producer="benchmarks.minecraft.matrix")
    files = {
        "provenance.json": {"benchmark": "minecraft", "lifecycle": {"status": "success"}},
        "config.resolved.json": {"model": "test", "api_key": "[REDACTED]"},
        "matrix_summary.json": {"benchmark": "minecraft", "runs": 1},
        "metrics.json": {"success_rate": 1.0},
    }
    files.update(extra or {})
    for name, payload in files.items():
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    finalize_run_directory(
        bundle,
        attempt_id=attempt_id,
        producer="benchmarks.minecraft.matrix",
        status="completed",
    )
    return bundle


def test_validates_bundle_and_builds_byte_identical_archive(tmp_path):
    bundle = _bundle(tmp_path)
    validation = validate_public_bundle(bundle)
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    first_metadata, _ = build_deterministic_archive(bundle, first)
    second_metadata, _ = build_deterministic_archive(bundle, second)

    assert validation.benchmark == "minecraft"
    assert first.read_bytes() == second.read_bytes()
    assert first_metadata.archive_sha256 == second_metadata.archive_sha256
    with zipfile.ZipFile(first) as archive:
        assert "artifact_manifest.json" in archive.namelist()
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())


def test_validator_does_not_treat_model_token_limit_as_credential(tmp_path):
    bundle = _bundle(tmp_path, extra={"model.json": {"max_tokens": 4096}})

    assert validate_public_bundle(bundle).benchmark == "minecraft"


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"secrets.json": {"api_key": "sk-abcdefghijklmnopqrstuvwxyz"}}, "credential"),
        ({"command.json": {"command": "runner --api-key ordinary-secret-value"}}, "credential argument"),
        ({"state.json": {"private_observation": "answer"}}, "private observation"),
        ({"hidden_evaluator.json": {"answer": 1}}, "hidden/runtime path"),
        ({".runtime/result.json": {"status": "done"}}, "runtime path"),
    ],
)
def test_rejects_credentials_private_state_and_runtime_files(tmp_path, extra, message):
    bundle = _bundle(tmp_path, extra=extra)

    with pytest.raises(PublicBundleValidationError, match=message):
        validate_public_bundle(bundle)


def test_rejects_unexpected_large_file(tmp_path):
    bundle = _bundle(tmp_path, extra={"large.txt": "12345"})

    with pytest.raises(PublicBundleValidationError, match="large file"):
        validate_public_bundle(bundle, max_file_bytes=4)


def test_local_publisher_is_immutable(tmp_path):
    bundle = _bundle(tmp_path)
    output = tmp_path / "bundle.zip"
    publisher = LocalStagingPublisher(tmp_path / "archive", "minecraft-result-v1")

    reference = publish_bundle(bundle, output, publisher)

    assert reference.stable_id == "minecraft-result-v1"
    assert reference.publisher == "local-staging"
    assert (tmp_path / "archive/minecraft-result-v1/bundle.zip").exists()
    with pytest.raises(Exception, match="already exists"):
        publisher.publish(
            output,
            output.with_suffix(".zip.metadata.json"),
            build_deterministic_archive(bundle, tmp_path / "again.zip")[0],
        )


def test_github_publisher_uses_new_release_and_can_be_mocked(tmp_path):
    bundle = _bundle(tmp_path)
    output = tmp_path / "bundle.zip"
    metadata, metadata_path = build_deterministic_archive(bundle, output)
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if "api" in command:
            return subprocess.CompletedProcess(command, 0, "true\n", "")
        return subprocess.CompletedProcess(command, 1 if "view" in command else 0, "", "not found")

    reference = GitHubReleasePublisher("owner/repo", "results-v1", runner=runner).publish(
        output, metadata_path, metadata
    )

    assert calls[0][:2] == ["gh", "api"]
    assert calls[1][:3] == ["gh", "release", "view"]
    assert calls[2][:3] == ["gh", "release", "create"]
    assert reference.archive_url.endswith("/results-v1/bundle.zip")


def test_docs_check_requires_complete_resolvable_registry_entry(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "result.md").write_text("Result. <!-- paper-result: craft-main -->\n", encoding="utf-8")
    registry = docs / "benchmark_archives.json"
    registry.write_text('{"schema_version": 1, "results": []}', encoding="utf-8")

    with pytest.raises(PublicBundleValidationError, match="craft-main"):
        check_paper_result_archives(docs, registry)

    manifest = b'{"attempt_id":"test"}\n'
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("artifact_manifest.json", manifest)
    archive_bytes = archive_buffer.getvalue()
    import hashlib
    archive_sha = hashlib.sha256(archive_bytes).hexdigest()
    manifest_sha = hashlib.sha256(manifest).hexdigest()
    metadata = json.dumps({
        "archive_sha256": archive_sha,
        "source_manifest_sha256": manifest_sha,
        "run_statuses": {"completed": 2, "failed": 1},
    }).encode()
    registry.write_text(json.dumps({
        "schema_version": 1,
        "results": [{
            "id": "craft-main",
            "benchmark": "craft",
            "classification": "archived",
            "archive_url": "https://github.com/owner/repo/releases/download/results-v1/bundle.zip",
            "metadata_url": "https://github.com/owner/repo/releases/download/results-v1/bundle.zip.metadata.json",
            "archive_sha256": archive_sha,
            "manifest_path": "artifact_manifest.json",
            "manifest_sha256": manifest_sha,
            "run_accounting": {"expected": 3, "completed": 2, "failed": 1, "missing": 0},
        }],
    }), encoding="utf-8")

    blobs = {
        "https://github.com/owner/repo/releases/download/results-v1/bundle.zip": archive_bytes,
        "https://github.com/owner/repo/releases/download/results-v1/bundle.zip.metadata.json": metadata,
    }
    assert check_paper_result_archives(docs, registry, fetcher=blobs.__getitem__) == ["craft-main"]


@pytest.mark.parametrize(
    "name,content",
    [
        ("events.jsonl", '{"private_observation":"answer"}\n'),
        ("events.csv", "event,password\nstep,ordinary-secret\n"),
        ("events.yaml", "nested:\n  hidden_state: answer\n"),
        ("events.txt", "password: ordinary-secret\n"),
    ],
)
def test_structured_and_text_scanners_reject_sensitive_keys(tmp_path, name, content):
    bundle = _bundle(tmp_path, extra={name: content})

    with pytest.raises(PublicBundleValidationError, match="private observation|credential"):
        validate_public_bundle(bundle)


@pytest.mark.parametrize(
    "name,content",
    [
        ("plural.json", {"nested": {"api_keys": ["SENTINEL_API_KEYS_SECRET"]}}),
        ("plural.jsonl", '{"nested":{"credentials":{"primary":"SENTINEL_CREDENTIALS_SECRET"}}}\n'),
        ("plural.csv", "event,api_key_list\nstep,SENTINEL_API_KEY_LIST_SECRET\n"),
        ("plural.yaml", "nested:\n  credentials:\n    - SENTINEL_CREDENTIALS_SECRET\n"),
        ("plural.txt", "api_keys: SENTINEL_API_KEYS_SECRET\n"),
    ],
)
def test_scanners_reject_plural_list_and_general_credential_keys(tmp_path, name, content):
    bundle = _bundle(tmp_path, extra={name: content})

    with pytest.raises(PublicBundleValidationError, match="credential"):
        validate_public_bundle(bundle)


def test_sanitize_builds_managed_derivative_and_links_failed_source(tmp_path):
    source = tmp_path / "source"
    source_attempt = prepare_run_directory(source, producer="benchmarks.cwah.matrix")
    files = {
        "provenance.json": {"benchmark": "cwah", "password": "ordinary-secret"},
        "config.resolved.json": {
            "api_key": "ordinary-secret",
            "api_key_list": ["SENTINEL_API_KEY_LIST_SECRET"],
            "credentials": {"primary": "SENTINEL_CREDENTIALS_SECRET"},
            "model": "test",
        },
        "matrix_summary.json": {"status": "failed"},
        "matrix_metrics.csv": "status,password\nfailed,ordinary-secret\n",
        "raw.json": {"private_observation": "answer"},
        "normalized/events.jsonl": (
            '{"event":"step","hidden_state":"answer","token":"ordinary-secret",'
            '"api_keys":["SENTINEL_API_KEYS_SECRET"],'
            '"target_structure":"SENTINEL_TARGET","oracle_moves":["SENTINEL_ORACLE"],'
            '"builder_prompt":"SENTINEL_BUILDER_PROMPT",'
            '"private_reasoning":"SENTINEL_REASONING","stdout":"SENTINEL_STDOUT"}\n'
        ),
    }
    for name, payload in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    finalize_run_directory(
        source,
        attempt_id=source_attempt,
        producer="benchmarks.cwah.matrix",
        status="failed",
    )

    public = tmp_path / "public"
    validation = derive_public_bundle(source, public)

    assert validation.benchmark == "cwah"
    assert validation.run_statuses == {"failed": 1}
    assert not (public / "raw.json").exists()
    source_link = json.loads((public / "publication_source.json").read_text())
    assert source_link["source_attempt_id"] == source_attempt
    assert source_link["source_status"] == "failed"
    event = json.loads((public / "normalized/events.jsonl").read_text())
    assert "hidden_state" not in event
    assert event["token"] == "[REDACTED]"
    assert event["api_keys"] == "[REDACTED]"
    for hidden_key in (
        "target_structure",
        "oracle_moves",
        "builder_prompt",
        "private_reasoning",
        "stdout",
    ):
        assert hidden_key not in event
    source_config = json.loads((public / "source_config.resolved.json").read_text())
    assert source_config["api_key_list"] == "[REDACTED]"
    assert source_config["credentials"] == "[REDACTED]"
    assert list(__import__("csv").DictReader((public / "matrix_metrics.csv").open()))[0]["password"] == "[REDACTED]"


def test_validates_finalized_failed_bundle_with_explicit_status(tmp_path):
    bundle = _bundle(tmp_path)
    attempt = json.loads((bundle / "attempt.json").read_text())
    (bundle / "_COMPLETED").unlink()
    attempt["status"] = "failed"
    (bundle / "attempt.json").write_text(json.dumps(attempt), encoding="utf-8")
    finalize_run_directory(
        bundle,
        attempt_id=attempt["attempt_id"],
        producer="benchmarks.minecraft.matrix",
        status="failed",
    )

    validation = validate_public_bundle(bundle)

    assert validation.run_statuses == {"failed": 1}


def test_local_staging_rejects_traversal_and_symlink_root(tmp_path):
    with pytest.raises(PublicationError, match="safe path component"):
        LocalStagingPublisher(tmp_path, "../escape").publish(None, None, None)

    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(PublicationError, match="symlink"):
        LocalStagingPublisher(linked, "result-v1").publish(None, None, None)


def test_github_publisher_rejects_repository_without_immutable_releases(tmp_path):
    publisher = GitHubReleasePublisher(
        "owner/repo",
        "results-v1",
        runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "false\n", ""),
    )

    with pytest.raises(PublicationError, match="immutable releases"):
        publisher.publish(tmp_path / "bundle.zip", tmp_path / "metadata.json", None)


def test_archive_uses_verified_snapshot_when_source_changes(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path)
    original = __import__("shutil").copytree

    def copy_then_mutate(source, destination, **kwargs):
        result = original(source, destination, **kwargs)
        (bundle / "metrics.json").write_text('{"success_rate": 0.0}', encoding="utf-8")
        return result

    monkeypatch.setattr("benchmarks.common.publish_bundle.shutil.copytree", copy_then_mutate)
    output = tmp_path / "snapshot.zip"

    build_deterministic_archive(bundle, output)

    with zipfile.ZipFile(output) as archive:
        assert json.loads(archive.read("metrics.json"))["success_rate"] == 1.0


def test_docs_check_detects_undeclared_reports_across_roots(tmp_path):
    docs = tmp_path / "docs"
    benchmarks = tmp_path / "benchmarks"
    docs.mkdir()
    benchmarks.mkdir()
    (benchmarks / "result.md").write_text("## Aggregate Results\n\n| score |\n| 1 |\n")
    registry = docs / "benchmark_archives.json"
    registry.write_text('{"schema_version": 1, "results": []}', encoding="utf-8")

    with pytest.raises(PublicBundleValidationError, match="result.md"):
        check_paper_result_archives(docs, registry, report_roots=[docs, benchmarks])


def test_registry_rejects_unresolvable_or_corrupt_archive(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "result.md").write_text("<!-- paper-result: craft-main -->\n")
    registry = docs / "benchmark_archives.json"
    registry.write_text(json.dumps({
        "schema_version": 1,
        "results": [{
            "id": "craft-main",
            "benchmark": "craft",
            "classification": "archived",
            "archive_url": "https://example.invalid/bundle.zip",
            "metadata_url": "https://example.invalid/bundle.metadata.json",
            "archive_sha256": "a" * 64,
            "manifest_path": "artifact_manifest.json",
            "manifest_sha256": "b" * 64,
            "run_accounting": {"expected": 1, "completed": 1, "failed": 0, "missing": 0},
        }],
    }), encoding="utf-8")

    with pytest.raises(PublicBundleValidationError, match="not resolvable"):
        check_paper_result_archives(
            docs,
            registry,
            fetcher=lambda url: (_ for _ in ()).throw(OSError("offline")),
        )


def test_registry_rejects_run_accounting_mismatch_with_archive_metadata(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "result.md").write_text("<!-- paper-result: craft-main -->\n")
    manifest = b'{"attempt_id":"test"}\n'
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("artifact_manifest.json", manifest)
    archive_bytes = archive_buffer.getvalue()
    import hashlib
    archive_sha = hashlib.sha256(archive_bytes).hexdigest()
    manifest_sha = hashlib.sha256(manifest).hexdigest()
    archive_url = "https://github.com/owner/repo/releases/download/results-v1/bundle.zip"
    metadata_url = archive_url + ".metadata.json"
    registry = docs / "benchmark_archives.json"
    registry.write_text(json.dumps({
        "schema_version": 1,
        "results": [{
            "id": "craft-main",
            "benchmark": "craft",
            "classification": "archived",
            "archive_url": archive_url,
            "metadata_url": metadata_url,
            "archive_sha256": archive_sha,
            "manifest_path": "artifact_manifest.json",
            "manifest_sha256": manifest_sha,
            "run_accounting": {"expected": 2, "completed": 2, "failed": 0, "missing": 0},
        }],
    }), encoding="utf-8")
    blobs = {
        archive_url: archive_bytes,
        metadata_url: json.dumps({
            "archive_sha256": archive_sha,
            "source_manifest_sha256": manifest_sha,
            "run_statuses": {"completed": 1, "failed": 1},
        }).encode(),
    }

    with pytest.raises(PublicBundleValidationError, match="run accounting mismatch"):
        check_paper_result_archives(docs, registry, fetcher=blobs.__getitem__)


def test_new_unarchived_benchmark_declaration_cannot_use_legacy_exception(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "result.md").write_text(
        "<!-- benchmark-result: new-result -->\n## Aggregate Results\n",
        encoding="utf-8",
    )
    registry = docs / "benchmark_archives.json"
    registry.write_text(json.dumps({
        "schema_version": 1,
        "results": [{
            "id": "new-result",
            "benchmark": "craft",
            "classification": "legacy-diagnostic-unarchived",
            "legacy_pre_policy": True,
            "publication_satisfied": False,
            "claim_eligible": False,
            "reason": "missing",
            "run_accounting": {"expected": 1, "completed": 0, "failed": 0, "missing": 1},
        }],
    }), encoding="utf-8")

    with pytest.raises(PublicBundleValidationError, match="not an approved pre-policy"):
        check_paper_result_archives(docs, registry)
