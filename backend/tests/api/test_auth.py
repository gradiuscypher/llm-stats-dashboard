"""API integration tests for auth endpoints."""



def test_login_success(client, test_user):
    resp = client.post("/api/v1/auth/login", json={"username": "testuser", "password": "testpass123"})
    assert resp.status_code == 200
    assert "lsd_session" in resp.cookies
    assert "lsd_csrf" in resp.cookies


def test_login_wrong_password(client, test_user):
    resp = client.post("/api/v1/auth/login", json={"username": "testuser", "password": "wrongpass"})
    assert resp.status_code == 401


def test_login_unknown_user(client):
    resp = client.post("/api/v1/auth/login", json={"username": "nobody", "password": "pass"})
    assert resp.status_code == 401


def test_me_requires_auth(client):
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401


def test_me_returns_user(auth_client, test_user):
    resp = auth_client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "testuser"


def test_logout_clears_session(auth_client):
    resp = auth_client.post("/api/v1/auth/logout")
    assert resp.status_code == 200
    # Session should be gone
    resp2 = auth_client.get("/api/v1/auth/me")
    assert resp2.status_code == 401


def test_healthz(client):
    assert client.get("/healthz").status_code == 200
