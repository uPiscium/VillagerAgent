from pathlib import Path


def test_default_pytest_paths_include_cwah_unit_tests():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert '"benchmarks/cwah/tests"' in pyproject
