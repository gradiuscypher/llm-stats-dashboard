"""API key management endpoints (session-authed)."""

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.db import get_session
from app.models.api_key import ApiKey
from app.models.session import UserSession
from app.models.user import User
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyPublic
from app.security.csrf import require_csrf
from app.security.passwords import hash_password
from app.security.sessions import SESSION_COOKIE, get_current_user

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


def _get_csrf_secret(request: Request, db: Session) -> str | None:
    raw_sid = request.cookies.get(SESSION_COOKIE)
    if not raw_sid:
        return None
    try:
        sess = db.get(UserSession, uuid.UUID(raw_sid))
        return sess.csrf_secret if sess else None
    except ValueError:
        return None


def _enforce_csrf(request: Request, db: Session) -> None:
    secret = _get_csrf_secret(request, db)
    if secret:
        require_csrf(request, secret)


@router.get("", response_model=list[ApiKeyPublic])
def list_keys(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ApiKey]:
    """List all API keys for the current user (never returns raw secrets)."""
    return list(db.exec(select(ApiKey).where(ApiKey.user_id == current_user.id)).all())


@router.post("", response_model=ApiKeyCreatedResponse, status_code=status.HTTP_201_CREATED)
def create_key(
    payload: ApiKeyCreate,
    request: Request,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ApiKeyCreatedResponse:
    """
    Create a new API key. The raw secret is returned **once** in `raw_key`
    and cannot be retrieved again. Store it securely.
    """
    _enforce_csrf(request, db)

    # Generate key: lsd_<8-char hex prefix>_<48-char hex secret>
    # Use hex (no underscores) so split("_", 2) is unambiguous.
    prefix_part = secrets.token_hex(4)  # 8 hex chars
    secret_part = secrets.token_hex(24)  # 48 hex chars
    prefix = f"lsd_{prefix_part}"
    raw_key = f"lsd_{prefix_part}_{secret_part}"

    api_key = ApiKey(
        user_id=current_user.id,
        name=payload.name,
        prefix=prefix,
        key_hash=hash_password(raw_key),
        scopes=payload.scopes,
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)

    return ApiKeyCreatedResponse(
        id=api_key.id,
        name=api_key.name,
        prefix=api_key.prefix,
        scopes=api_key.scopes,
        last_used_at=api_key.last_used_at,
        revoked_at=api_key.revoked_at,
        created_at=api_key.created_at,
        raw_key=raw_key,
    )


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_key(
    key_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """Permanently revoke an API key. This action cannot be undone."""
    _enforce_csrf(request, db)

    key = db.get(ApiKey, key_id)
    if not key or key.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

    from app.utils.time import utcnow

    key.revoked_at = utcnow()
    db.add(key)
    db.commit()
