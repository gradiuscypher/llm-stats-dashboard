"""Pytest fixtures for backend tests."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.config import settings
from app.db import get_session
from app.main import create_app
from app.models.user import User
from app.security.passwords import hash_password

# ---------------------------------------------------------------------------
# In-memory SQLite engine for fast unit tests that don't need Postgres features.
# API integration tests use the test Postgres DB (see pg_session fixture).
# ---------------------------------------------------------------------------

@pytest.fixture(name="sqlite_engine")
def sqlite_engine_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)


@pytest.fixture(name="sqlite_session")
def sqlite_session_fixture(sqlite_engine):
    with Session(sqlite_engine) as session:
        yield session


# ---------------------------------------------------------------------------
# Postgres test DB (integration tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", name="pg_engine")
def pg_engine_fixture():
    engine = create_engine(settings.test_database_url, poolclass=NullPool)
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)


@pytest.fixture(name="pg_session")
def pg_session_fixture(pg_engine):
    # Use a nested transaction so each test rolls back cleanly
    connection = pg_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# Test app + HTTP client (uses Postgres test DB)
# ---------------------------------------------------------------------------

@pytest.fixture(name="client")
def client_fixture(pg_engine):
    app = create_app()

    def override_get_session():
        with Session(pg_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(name="clean_tables")
def clean_tables_fixture(pg_engine) -> None:
    """Truncate all app tables for isolation. Call explicitly or via autouse wrapper."""
    with pg_engine.connect() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE user_sessions, api_keys, log_entries, messages, users, model_prices "
                "RESTART IDENTITY CASCADE"
            )
        )
        conn.commit()


@pytest.fixture(autouse=True)
def _auto_clean(clean_tables) -> None:  # noqa: ARG001
    """Auto-run clean_tables before every test."""


@pytest.fixture(name="test_user")
def test_user_fixture(pg_engine, clean_tables) -> User:  # noqa: ARG001
    """Create a test user. Depends on clean_tables to ensure it runs after cleanup."""
    with Session(pg_engine) as session:
        user = User(
            username="testuser",
            email="test@example.com",
            password_hash=hash_password("testpass123"),
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        yield user


@pytest.fixture(name="auth_client")
def auth_client_fixture(client, test_user) -> None:  # noqa: ARG001
    """TestClient with a valid session cookie set."""
    resp = client.post("/api/v1/auth/login", json={"username": "testuser", "password": "testpass123"})
    assert resp.status_code == 200, f"Login failed: {resp.json()}"
    yield client
