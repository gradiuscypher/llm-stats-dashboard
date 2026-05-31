"""Server-side session management using signed cookies."""

import secrets
import uuid
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session

from app.config import settings
from app.db import get_session
from app.models.session import UserSession
from app.models.user import User

SESSION_COOKIE = "lsd_session"


def create_session(user_id: uuid.UUID, db: Session) -> tuple[str, str]:
    """
    Create a server-side session. Returns (session_id_str, csrf_secret).
    The session_id is stored in a signed httpOnly cookie.
    """
    csrf_secret = secrets.token_hex(32)
    now = datetime.utcnow()
    expires = now + timedelta(seconds=settings.session_max_age_seconds)
    session = UserSession(
        user_id=user_id,
        csrf_secret=csrf_secret,
        created_at=now,
        expires_at=expires,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return str(session.id), csrf_secret


def revoke_session(session_id: str, db: Session) -> None:
    sid = uuid.UUID(session_id)
    sess = db.get(UserSession, sid)
    if sess:
        sess.revoked = True
        db.add(sess)
        db.commit()


def get_current_user(
    request: Request,
    db: Session = Depends(get_session),
) -> User:
    """Dependency: resolve session cookie → User. Raises 401 if invalid."""
    raw_session_id = request.cookies.get(SESSION_COOKIE)
    if not raw_session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        sid = uuid.UUID(raw_session_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session"
        ) from exc

    sess = db.get(UserSession, sid)
    if not sess or sess.revoked or sess.expires_at < datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid"
        )

    user = db.get(User, sess.user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
        )

    return user
