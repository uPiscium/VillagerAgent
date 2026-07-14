import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DIRECT_API_KEY_LIST_READ = re.compile(r"json\.load\s*\(\s*open\s*\(\s*[\"']API_KEY_LIST[\"']")


def test_runtime_code_does_not_directly_read_api_key_list():
    offenders = []
    for path in REPO_ROOT.rglob("*.py"):
        if _is_excluded(path):
            continue
        if DIRECT_API_KEY_LIST_READ.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == []


def _is_excluded(path: Path) -> bool:
    parts = set(path.relative_to(REPO_ROOT).parts)
    return bool({".git", ".worktrees", "external", "__pycache__"} & parts)
