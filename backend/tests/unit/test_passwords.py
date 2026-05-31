"""Unit tests for password hashing."""

from app.security.passwords import hash_password, verify_password


def test_hash_and_verify():
    pw = "super_secret_123"
    hashed = hash_password(pw)
    assert hashed != pw
    assert verify_password(pw, hashed)


def test_wrong_password_fails():
    hashed = hash_password("correct_password")
    assert not verify_password("wrong_password", hashed)


def test_hashes_are_unique():
    pw = "same_password"
    assert hash_password(pw) != hash_password(pw)
