"""API tests — docs endpoints must be accessible without any authentication."""


def test_docs_index_no_auth(client):
    """GET /docs-md returns the doc index without a session or API key."""
    resp = client.get("/api/v1/docs-md")
    assert resp.status_code == 200
    pages = resp.json()
    assert isinstance(pages, list)
    paths = [p["path"] for p in pages]
    assert "index.md" in paths
    assert "ai-client-guide.md" in paths
    assert "schemas.md" in paths


def test_docs_index_page_no_auth(client):
    """GET /docs-md/index.md returns raw Markdown without a session or API key."""
    resp = client.get("/api/v1/docs-md/index.md")
    assert resp.status_code == 200
    assert "LLM Stats Dashboard" in resp.text


def test_ai_client_guide_no_auth(client):
    """The AI client guide is reachable without authentication."""
    resp = client.get("/api/v1/docs-md/ai-client-guide.md")
    assert resp.status_code == 200
    assert "conversation_id" in resp.text


def test_schemas_no_auth(client):
    """The canonical schema reference is reachable without authentication."""
    resp = client.get("/api/v1/docs-md/schemas.md")
    assert resp.status_code == 200
    assert "provider" in resp.text


def test_docs_path_traversal_blocked(client):
    """Path traversal attempts outside the docs directory return 404."""
    resp = client.get("/api/v1/docs-md/../../backend/app/config.py")
    assert resp.status_code == 404


def test_docs_missing_page(client):
    """Requesting a non-existent doc returns 404."""
    resp = client.get("/api/v1/docs-md/does-not-exist.md")
    assert resp.status_code == 404
