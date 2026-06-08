"""Unit tests for CSRF token generation and validation."""

from app.security.csrf import generate_csrf_token, validate_csrf_token


def test_valid_token():
    secret = "test_secret"
    token = generate_csrf_token(secret)
    assert validate_csrf_token(secret, token)


def test_wrong_secret_fails():
    token = generate_csrf_token("secret_a")
    assert not validate_csrf_token("secret_b", token)


def test_tampered_token_fails():
    token = generate_csrf_token("secret")
    tampered = token[:-4] + "xxxx"
    assert not validate_csrf_token("secret", tampered)


def test_malformed_token_fails():
    assert not validate_csrf_token("secret", "not_a_valid_token")
    assert not validate_csrf_token("secret", "")
