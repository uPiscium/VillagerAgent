import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def test_markdown_relative_links_exist():
    missing_links = []
    for markdown_path in [REPO_ROOT / "README.md", REPO_ROOT / "README.ja.md", *sorted((REPO_ROOT / "docs").glob("*.md"))]:
        for target in MARKDOWN_LINK.findall(markdown_path.read_text(encoding="utf-8")):
            if _is_external_or_anchor(target):
                continue
            link_path = target.split("#", 1)[0]
            if not link_path:
                continue
            if not (markdown_path.parent / link_path).exists():
                missing_links.append(f"{markdown_path.relative_to(REPO_ROOT)} -> {target}")

    assert missing_links == []


def test_expected_readme_entrypoints_exist_and_legacy_variants_do_not_return():
    assert (REPO_ROOT / "README.md").is_file()
    assert (REPO_ROOT / "README.ja.md").is_file()
    assert not (REPO_ROOT / "READMEja.md").exists()
    assert not (REPO_ROOT / "READMEzh.md").exists()


def test_documented_ollama_defaults_match_source_defaults():
    source = (REPO_ROOT / "model" / "ollama_config.py").read_text(encoding="utf-8")
    default_api_base = _extract_environment_default(source, "OLLAMA_API_BASE")
    default_model = _extract_environment_default(source, "OLLAMA_MODEL")

    docs_to_check = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "README.ja.md",
        REPO_ROOT / "docs" / "configuration.md",
        REPO_ROOT / "docs" / "minimal_run.md",
    ]
    for doc_path in docs_to_check:
        contents = doc_path.read_text(encoding="utf-8")
        assert default_api_base in contents, doc_path.relative_to(REPO_ROOT)
        assert default_model in contents, doc_path.relative_to(REPO_ROOT)


def _is_external_or_anchor(target: str) -> bool:
    return target.startswith(("http://", "https://", "mailto:", "#"))


def _extract_environment_default(source: str, variable_name: str) -> str:
    match = re.search(rf'{variable_name}\s*=\s*os\.environ\.get\("{variable_name}",\s*"([^"]+)"\)', source)
    assert match is not None
    return match.group(1)
