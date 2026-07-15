import json
import subprocess
import sys

from benchmarks.experiment_provenance import (
    file_identity,
    finalize_provenance,
    git_identity,
    python_environment_identity,
    write_provenance,
)


def test_provenance_redacts_secrets_and_finalizes_lifecycle(tmp_path):
    secret = "provenance-secret-12345"
    write_provenance(
        tmp_path,
        benchmark="craft",
        command=["python", "run.py", "--api-key", secret],
        resolved_config={"api_key": secret, "api_key_env": "TEST_API_KEY", "setting": 3},
    )

    provenance = finalize_provenance(tmp_path, status="success")
    serialized = json.dumps(provenance)

    assert secret not in serialized
    assert provenance["schema_version"] == "2.0.0"
    assert provenance["commit"] == provenance["repository"]["sha"]
    assert provenance["argv"][-1] == "[REDACTED]"
    assert provenance["effective_settings"] == {
        "api_key": "[REDACTED]",
        "api_key_env": "TEST_API_KEY",
        "setting": 3,
    }
    assert provenance["lifecycle"]["status"] == "success"
    assert provenance["lifecycle"]["ended_at"]
    assert provenance["lifecycle"]["duration_seconds"] >= 0


def test_file_and_directory_fingerprints_are_deterministic(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "b.txt").write_text("second", encoding="utf-8")
    (dataset / "a.txt").write_text("first", encoding="utf-8")

    first = file_identity(dataset, name="dataset", kind="dataset")
    second = file_identity(dataset, name="dataset", kind="dataset")

    assert first["size"] == 11
    assert first["sha256"] == second["sha256"]
    (dataset / "a.txt").write_text("changed", encoding="utf-8")
    assert file_identity(dataset, name="dataset", kind="dataset")["sha256"] != first["sha256"]


def test_git_identity_records_dirty_state(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (repository / "tracked.txt").write_text("clean", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
    subprocess.run([
        "git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "-q", "-m", "initial",
    ], check=True)

    assert git_identity(repository, required=True, name="test")["dirty"] is False
    (repository / "tracked.txt").write_text("dirty", encoding="utf-8")
    identity = git_identity(repository, required=True, name="test")
    assert identity["dirty"] is True
    assert len(identity["sha"]) == 40


def test_missing_required_asset_marks_environment_unverifiable(tmp_path):
    missing = file_identity(tmp_path / "missing.bin", name="runtime", kind="executable")
    provenance = write_provenance(
        tmp_path / "run",
        benchmark="cwah",
        command="python -m benchmark",
        resolved_config={},
        assets=[missing],
    )

    assert provenance["environment_unverifiable"] is True
    assert "executable:runtime:missing" in provenance["unverifiable_reasons"]


def test_python_environment_identity_fingerprints_installed_dependencies():
    first = python_environment_identity(sys.executable, name="runner")
    second = python_environment_identity(sys.executable, name="runner")

    assert first["available"] is True
    assert first["python_version"]
    assert first["package_count"] > 0
    assert first["sha256"] == second["sha256"]


def test_python_environment_identity_times_out_safely(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr("benchmarks.experiment_provenance.subprocess.run", timeout)
    identity = python_environment_identity(
        sys.executable,
        name="runner",
        timeout_seconds=0.01,
    )

    assert identity["available"] is False
    assert identity["reason"] == "python_environment_identity_unavailable"
