"""Docs serving endpoints — raw Markdown for AI consumption and frontend rendering."""

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import PlainTextResponse

from app.docs_loader import get_doc, list_docs

router = APIRouter(prefix="/docs-md", tags=["docs"])


@router.get("", summary="List all available documentation pages")
def list_doc_pages() -> list[dict]:
    """
    Returns a JSON index of all available Markdown documentation pages.
    Each entry has `path` (use with `GET /docs-md/{path}`) and `title`.
    Start with `index.md` for an overview and table of contents.
    """
    return list_docs()


@router.get("/{path:path}", response_class=PlainTextResponse, summary="Fetch raw Markdown doc")
def get_doc_page(path: str) -> str:
    """
    Returns raw Markdown content for the given documentation page.
    Designed for both the frontend renderer and direct AI consumption.
    Recommended starting point: `index.md`.
    """
    content = get_doc(path)
    if content is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Doc not found: {path}")
    return content
