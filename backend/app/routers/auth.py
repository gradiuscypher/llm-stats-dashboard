"""Authentication endpoints (session-cookie based)."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlmodel import Session, select

from app.config import settings
from app.db import get_session
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.user import UserPublic
from app.security.csrf import CSRF_COOKIE, generate_csrf_token
from app.security.passwords import verify_password
from app.security.sessions import SESSION_COOKIE, create_session, get_current_user, revoke_session

router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_KWARGS: dict = {
    "httponly": True,
    "samesite": "lax",
    "secure": False,  # set True in production via middleware
}


@router.get("/csrf")
def get_csrf_token(
    request: Request,
    response: Response,
    db: Session = Depends(get_session),
) -> dict:
    """
    Issue or refresh a CSRF token tied to the current session.
    Returns the token in both the response body and the `lsd_csrf` cookie.
    JavaScript should read this token and send it as the `X-CSRF-Token` header
    on all state-changing requests.
    """
    import uuid as _uuid

    from app.models.session import UserSession as _UserSession
    get_current_user(request, db)  # validates session
    raw_sid = request.cookies.get(SESSION_COOKIE)
    sess = db.get(_UserSession, _uuid.UUID(raw_sid))  # type: ignore[arg-type]
    token = generate_csrf_token(sess.csrf_secret)  # type: ignore[union-attr]
    response.set_cookie(CSRF_COOKIE, token, samesite="lax", httponly=False)
    return {"csrf_token": token}


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    response: Response,
    db: Session = Depends(get_session),
) -> TokenResponse:
    """
    Authenticate with username and password.
    Sets an httpOnly session cookie (`lsd_session`) and a readable CSRF cookie
    (`lsd_csrf`). The frontend must send the CSRF token in `X-CSRF-Token` on
    subsequent mutating requests.
    """
    user = db.exec(select(User).where(User.username == payload.username)).first()
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    session_id, csrf_secret = create_session(user.id, db)
    csrf_token = generate_csrf_token(csrf_secret)

    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=settings.session_max_age_seconds,
        **_COOKIE_KWARGS,
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=settings.session_max_age_seconds,
        samesite="lax",
        httponly=False,
    )
    return TokenResponse()


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_session),
) -> dict:
    """Revoke the current session and clear cookies."""
    raw_sid = request.cookies.get(SESSION_COOKIE)
    if raw_sid:
        revoke_session(raw_sid, db)
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    return {"message": "logged out"}


@router.get("/me", response_model=UserPublic)
def me(user: User = Depends(get_current_user)) -> User:
    """Return the currently authenticated user's profile."""
    return user


# ---------------------------------------------------------------------------
# OAuth scaffolding (disabled — stubs for future implementation)
# ---------------------------------------------------------------------------

@router.get("/oauth/{provider}/authorize", include_in_schema=True, tags=["oauth (scaffold)"])
def oauth_authorize(provider: str) -> dict:
    """
    [SCAFFOLD — not yet implemented]
    Redirect the user to the OAuth provider's authorization page.
    Supported providers will be: google, github.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"OAuth provider '{provider}' is not yet enabled.",
    )


@router.get("/oauth/{provider}/callback", include_in_schema=True, tags=["oauth (scaffold)"])
def oauth_callback(provider: str, code: str | None = None) -> dict:
    """[SCAFFOLD — not yet implemented] OAuth callback handler."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"OAuth provider '{provider}' is not yet enabled.",
    )
