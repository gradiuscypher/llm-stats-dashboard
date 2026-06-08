"""API integration tests for API key management."""


def _csrf(auth_client) -> str:
    resp = auth_client.get("/api/v1/auth/csrf")
    return resp.json()["csrf_token"]


def test_list_keys_empty(auth_client):
    resp = auth_client.get("/api/v1/api-keys")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_key(auth_client):
    csrf = _csrf(auth_client)
    resp = auth_client.post(
        "/api/v1/api-keys",
        json={"name": "test-key", "scopes": ["logs:write"]},
        headers={"x-csrf-token": csrf},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "raw_key" in data
    assert data["raw_key"].startswith("lsd_")
    assert data["scopes"] == ["logs:write"]


def test_raw_key_not_in_list(auth_client):
    csrf = _csrf(auth_client)
    auth_client.post(
        "/api/v1/api-keys",
        json={"name": "list-test", "scopes": ["logs:read"]},
        headers={"x-csrf-token": csrf},
    )
    resp = auth_client.get("/api/v1/api-keys")
    for key in resp.json():
        assert "raw_key" not in key


def test_create_invalid_scope(auth_client):
    csrf = _csrf(auth_client)
    resp = auth_client.post(
        "/api/v1/api-keys",
        json={"name": "bad", "scopes": ["admin:everything"]},
        headers={"x-csrf-token": csrf},
    )
    assert resp.status_code == 422


def test_revoke_key(auth_client):
    csrf = _csrf(auth_client)
    created = auth_client.post(
        "/api/v1/api-keys",
        json={"name": "to-revoke", "scopes": ["logs:write"]},
        headers={"x-csrf-token": csrf},
    ).json()

    resp = auth_client.delete(
        f"/api/v1/api-keys/{created['id']}",
        headers={"x-csrf-token": csrf},
    )
    assert resp.status_code == 204
