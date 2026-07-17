import json
from pathlib import Path

import pytest

from villageragent_visualizer.artifacts import ArtifactRepository
from villageragent_visualizer.dto import ArtifactErrorCode, ArtifactWarningCode


def test_load_json_sanitizes_nested_private_data_and_preserves_unknown_fields(tmp_path: Path) -> None:
    artifact_path = tmp_path / "run" / "artifact.json"
    artifact_path.parent.mkdir()
    artifact_path.write_text(json.dumps({
        "schema_version": "1.0.0",
        "unknown_future_field": {"value": 3},
        "api_key": "top-secret",
        "private": "hidden",
        "nested": {
            "_private": "hidden",
            "credential_source": "vault",
            "items": [
                {"session_token": "hidden", "name": "public"},
                {"private_key": "hidden", "status": "ok"},
            ],
        },
    }), encoding="utf-8")

    result = ArtifactRepository(tmp_path).load_json(
        "run/artifact.json",
        supported_schema_version="1.0.0",
    )

    assert result.ok
    assert result.artifact is not None
    assert result.artifact.data == {
        "schema_version": "1.0.0",
        "unknown_future_field": {"value": 3},
        "nested": {
            "items": [
                {"name": "public"},
                {"status": "ok"},
            ],
        },
    }
    assert result.artifact.warnings == ()


@pytest.mark.parametrize(("contents", "expected_code"), [
    (b"{not-json", ArtifactErrorCode.MALFORMED),
    (b'{"value": NaN}', ArtifactErrorCode.MALFORMED),
    (b"\xff", ArtifactErrorCode.INVALID_ENCODING),
])
def test_load_json_classifies_invalid_content(
    tmp_path: Path,
    contents: bytes,
    expected_code: ArtifactErrorCode,
) -> None:
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_bytes(contents)

    result = ArtifactRepository(tmp_path).load_json("artifact.json")

    assert not result.ok
    assert result.error is not None
    assert result.error.code is expected_code


def test_load_json_distinguishes_missing_and_oversized_artifacts(tmp_path: Path) -> None:
    repository = ArtifactRepository(tmp_path, max_bytes=8)

    missing = repository.load_json("missing.json")
    assert missing.error is not None
    assert missing.error.code is ArtifactErrorCode.MISSING

    (tmp_path / "large.json").write_text('{"value": 123}', encoding="utf-8")
    oversized = repository.load_json("large.json")
    assert oversized.error is not None
    assert oversized.error.code is ArtifactErrorCode.OVERSIZED


@pytest.mark.parametrize("path", [
    "runtime_result.json.tmp",
    "run.tmp/runtime_result.json",
    "../outside.json",
])
def test_load_json_rejects_temporary_and_traversal_paths(tmp_path: Path, path: str) -> None:
    result = ArtifactRepository(tmp_path).load_json(path)

    assert result.error is not None
    assert result.error.code is ArtifactErrorCode.INVALID_PATH


def test_load_json_rejects_absolute_and_symlink_paths(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-visualizer-artifact.json"
    outside.write_text("{}", encoding="utf-8")
    (tmp_path / "linked.json").symlink_to(outside)
    repository = ArtifactRepository(tmp_path)

    absolute = repository.load_json(outside)
    linked = repository.load_json("linked.json")

    assert absolute.error is not None
    assert absolute.error.code is ArtifactErrorCode.INVALID_PATH
    assert linked.error is not None
    assert linked.error.code is ArtifactErrorCode.INVALID_PATH


def test_load_json_returns_io_error_for_directory_without_raising(tmp_path: Path) -> None:
    (tmp_path / "artifact.json").mkdir()

    result = ArtifactRepository(tmp_path).load_json("artifact.json")

    assert result.error is not None
    assert result.error.code is ArtifactErrorCode.IO_ERROR


def test_load_json_warns_when_schema_version_is_missing(tmp_path: Path) -> None:
    (tmp_path / "artifact.json").write_text('{"value": 1}', encoding="utf-8")

    result = ArtifactRepository(tmp_path).load_json("artifact.json", supported_schema_version="1.0.0")

    assert result.artifact is not None
    assert result.artifact.schema_version is None
    assert [warning.code for warning in result.artifact.warnings] == [
        ArtifactWarningCode.PRODUCER_VERSIONED,
    ]


@pytest.mark.parametrize("version", ["1.1.0", "1.0.1"])
def test_load_json_warns_for_future_compatible_schema(
    tmp_path: Path,
    version: str,
) -> None:
    (tmp_path / "artifact.json").write_text(
        json.dumps({"schema_version": version, "future": True}),
        encoding="utf-8",
    )

    result = ArtifactRepository(tmp_path).load_json("artifact.json", supported_schema_version="1.0.0")

    assert result.artifact is not None
    assert result.artifact.data == {"schema_version": version, "future": True}
    assert [warning.code for warning in result.artifact.warnings] == [
        ArtifactWarningCode.FUTURE_SCHEMA_VERSION,
    ]


def test_load_json_distinguishes_major_mismatch_from_malformed_json(tmp_path: Path) -> None:
    (tmp_path / "artifact.json").write_text('{"schema_version": "2.0.0"}', encoding="utf-8")

    result = ArtifactRepository(tmp_path).load_json("artifact.json", supported_schema_version="1.0.0")

    assert result.error is not None
    assert result.error.code is ArtifactErrorCode.UNSUPPORTED_SCHEMA_MAJOR


@pytest.mark.parametrize("version", ["v1", 1])
def test_load_json_rejects_non_semver_when_semver_is_required(
    tmp_path: Path,
    version: str | int,
) -> None:
    (tmp_path / "artifact.json").write_text(
        json.dumps({"schema_version": version}),
        encoding="utf-8",
    )

    result = ArtifactRepository(tmp_path).load_json("artifact.json", supported_schema_version="1.0.0")

    assert result.error is not None
    assert result.error.code is ArtifactErrorCode.INVALID_SCHEMA_VERSION


def test_load_json_allows_producer_specific_numeric_version_without_semver_policy(tmp_path: Path) -> None:
    (tmp_path / "artifact.json").write_text('{"schema_version": 1}', encoding="utf-8")

    result = ArtifactRepository(tmp_path).load_json("artifact.json")

    assert result.artifact is not None
    assert result.artifact.schema_version == "1"
    assert result.artifact.warnings == ()
