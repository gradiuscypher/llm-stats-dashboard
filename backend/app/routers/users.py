"""User management endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.db import get_session
from app.models.session import UserSession
from app.models.user import User
from app.schemas.user import UserCreate, UserPublic, UserUpdate
from app.security.csrf import require_csrf
from app.security.passwords import hash_password
from app.security.sessions import get_current_user

router = APIRouter(prefix="/users", tags=["users"])


def _get_csrf_secret(request: Request, db: Session) -> str | None:
    from app.security.sessions import SESSION_COOKIE

    raw_sid = request.cookies.get(SESSION_COOKIE)
    if not raw_sid:
        return None
    try:
        sess = db.get(UserSession, uuid.UUID(raw_sid))
        return sess.csrf_secret if sess else None
    except ValueError:
        return None


@router.post("", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_session)) -> User:
    """Register a new user account (open self-serve)."""
    existing_username = db.exec(select(User).where(User.username == payload.username)).first()
    if existing_username:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken")

    if payload.email:
        existing_email = db.exec(select(User).where(User.email == payload.email)).first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
            )

    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/me", response_model=UserPublic)
def update_me(
    payload: UserUpdate,
    request: Request,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> User:
    """Update the current user's email or password."""
    csrf_secret = _get_csrf_secret(request, db)
    if csrf_secret:
        require_csrf(request, csrf_secret)

    if payload.email is not None:
        existing = db.exec(select(User).where(User.email == payload.email)).first()
        if existing and existing.id != current_user.id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")
        current_user.email = payload.email

    if payload.password is not None:
        if len(payload.password) < 8:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Password must be at least 8 characters",
            )
        current_user.password_hash = hash_password(payload.password)

    from app.utils.time import utcnow

    current_user.updated_at = utcnow()
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user
