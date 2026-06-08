"""API integration tests for user registration."""


def test_register_success(client):
    resp = client.post(
        "/api/v1/users",
        json={
            "username": "newuser",
            "email": "new@example.com",
            "password": "strongpass1",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["username"] == "newuser"
    assert "password_hash" not in data


def test_register_duplicate_username(client, test_user):
    resp = client.post(
        "/api/v1/users",
        json={
            "username": "testuser",
            "password": "anotherpass1",
        },
    )
    assert resp.status_code == 409


def test_register_weak_password(client):
    resp = client.post("/api/v1/users", json={"username": "weakpwuser", "password": "short"})
    assert resp.status_code == 422


def test_register_invalid_username(client):
    resp = client.post("/api/v1/users", json={"username": "bad user!", "password": "goodpassword1"})
    assert resp.status_code == 422
