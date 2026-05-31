"""API key authentication dependency."""

from collections.abc import Callable
from datetime import datetime

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlmodel import Session, select

from app.db import get_session
from app.models.api_key import ApiKey
from app.models.user import User
from app.security.passwords import verify_password

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _parse_key(raw: str) -> tuple[str, str] | None:
    """
    Key format: lsd_<prefix>_<secret>
    Returns (prefix, full_raw) or None if malformed.
    """
    if not raw or not raw.startswith("lsd_"):
        return None
    parts = raw.split("_", 2)
    if len(parts) != 3:
        return None
    prefix = f"lsd_{parts[1]}"
    return prefix, raw


async def get_current_user_from_api_key(
    raw_key: str | None = Security(_api_key_header),
    db: Session = Depends(get_session),
) -> tuple[User, ApiKey]:
    """Resolve an API key header to (User, ApiKey). Raises 401 if invalid."""
    if not raw_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    parsed = _parse_key(raw_key)
    if not parsed:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed API key")

    prefix, full_key = parsed
    candidates = db.exec(select(ApiKey).where(ApiKey.prefix == prefix)).all()

    matched: ApiKey | None = None
    for candidate in candidates:
        if not candidate.is_revoked and verify_password(full_key, candidate.key_hash):
            matched = candidate
            break

    if not matched:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    # Update last_used_at
    matched.last_used_at = datetime.utcnow()
    db.add(matched)
    db.commit()

    user = db.get(User, matched.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive")

    return user, matched


def require_scope(scope: str) -> Callable:
    """Returns a FastAPI dependency that enforces a specific scope on an API key."""
    async def dependency(
        auth: tuple[User, ApiKey] = Depends(get_current_user_from_api_key),
    ) -> User:
        user, api_key = auth
        if scope not in api_key.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API key missing required scope: {scope}",
            )
        return user
    return dependency
