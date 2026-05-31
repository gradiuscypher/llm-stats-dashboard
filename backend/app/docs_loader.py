"""Serve Markdown docs from the /docs directory at the repo root."""

from pathlib import Path

# Walk up from backend/app to find docs/
_REPO_ROOT = Path(__file__).parent.parent.parent
DOCS_DIR = _REPO_ROOT / "docs"


def list_docs() -> list[dict]:
    """Return a list of available doc pages with path and title."""
    if not DOCS_DIR.exists():
        return []
    results = []
    for md_file in sorted(DOCS_DIR.rglob("*.md")):
        rel = md_file.relative_to(DOCS_DIR)
        results.append({
            "path": str(rel).replace("\\", "/"),
            "title": md_file.stem.replace("-", " ").replace("_", " ").title(),
        })
    return results


def get_doc(path: str) -> str | None:
    """
    Return raw markdown content for a given relative path (e.g. 'index.md').
    Returns None if the file doesn't exist or path traversal is attempted.
    """
    requested = (DOCS_DIR / path).resolve()
    # Prevent path traversal
    if not str(requested).startswith(str(DOCS_DIR.resolve())):
        return None
    if not requested.exists() or not requested.is_file():
        return None
    return requested.read_text(encoding="utf-8")
