from pathlib import Path

from benchmarks.experiment_provenance import (
    project_public_argv_paths,
    project_public_path,
    project_public_path_fields,
)


def test_public_path_relativizes_repository_path():
    assert project_public_path("/repo/foo/bar", repository_root=Path("/repo")) == "foo/bar"


def test_public_path_redacts_external_path():
    assert project_public_path("/home/user/result", repository_root=Path("/repo")) == "<external>"


def test_sanitize_argv_paths_redacts_inline_output_path():
    argv = ["python", "--output=/tmp/result"]

    assert project_public_argv_paths(argv, repository_root=Path("/repo")) == [
        "python",
        "--output=<external>",
    ]


def test_sanitize_argv_paths_redacts_separate_runtime_root():
    argv = ["python", "--runtime-root", "/tmp/runtime-root"]

    assert project_public_argv_paths(argv, repository_root=Path("/bundle")) == [
        "python",
        "--runtime-root",
        "<external>",
    ]


def test_sanitize_argv_paths_preserves_non_path_arguments():
    argv = [
        "python",
        "--model",
        "gemma4:12b",
        "--api-base",
        "http://localhost:11434/v1",
        "--minecraft-command",
        "/tp @s",
        "--resource",
        "minecraft:stone",
        "--note",
        "/hello/world",
    ]

    assert project_public_argv_paths(argv, repository_root=Path("/bundle")) == argv


def test_sanitize_path_fields_preserves_non_path_semantics():
    payload = {
        "api_base": "https://example.com/foo",
        "api_model": "gemma4:12b",
        "minecraft_command": "/tp @s",
        "resource_identifier": "/minecraft/resource",
        "task_description": "/hello/world",
        "ordinary_string": "/hello/world",
        "path": "/repo/foo/bar",
        "output_dir": "/home/user/result",
    }

    assert project_public_path_fields(payload, repository_root=Path("/repo")) == {
        **payload,
        "path": "foo/bar",
        "output_dir": "<external>",
    }


def test_sanitize_path_fields_preserves_url_in_path_field():
    payload = {"path": "https://example.com/foo"}

    assert project_public_path_fields(payload, repository_root=Path("/repo")) == payload


def test_public_path_treats_windows_absolute_path_as_external():
    assert project_public_path(
        r"C:\Users\researcher\result",
        repository_root=Path("/repo"),
    ) == "<external>"


def test_public_path_relativizes_windows_repository_path():
    assert project_public_path(
        r"C:\repo\foo\bar",
        repository_root=Path(r"C:\repo"),
    ) == "foo/bar"
