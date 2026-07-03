from pathlib import Path

from benchmarks.cwah.coela_env import resolve_coela_executable


def test_resolve_coela_executable_prefers_readme_sibling_layout(tmp_path):
    root = tmp_path / "repo"
    coela_cwah = root / "external" / "CoELA" / "cwah"
    executable = root / "external" / "CoELA" / "executable" / "linux_exec.v2.3.0.x86_64"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")

    assert resolve_coela_executable(root=root, coela_cwah=coela_cwah, explicit_path="") == executable.resolve()


def test_resolve_coela_executable_uses_explicit_path_first(tmp_path):
    root = tmp_path / "repo"
    coela_cwah = root / "external" / "CoELA" / "cwah"
    explicit = tmp_path / "custom" / "linux_exec.v2.3.0.x86_64"
    explicit.parent.mkdir(parents=True)
    explicit.write_text("", encoding="utf-8")

    assert resolve_coela_executable(root=root, coela_cwah=coela_cwah, explicit_path=explicit) == explicit.resolve()


def test_resolve_coela_executable_falls_back_to_legacy_layout(tmp_path):
    root = tmp_path / "repo"
    coela_cwah = root / "external" / "CoELA" / "cwah"
    legacy = root / "external" / "executable" / "linux_exec.v2.3.0.x86_64"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("", encoding="utf-8")

    assert resolve_coela_executable(root=root, coela_cwah=coela_cwah, explicit_path=None) == legacy.resolve()
