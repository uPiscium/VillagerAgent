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


def _is_external_or_anchor(target: str) -> bool:
    return target.startswith(("http://", "https://", "mailto:", "#"))
