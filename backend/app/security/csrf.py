"""CSRF double-submit token implementation.

We use a simple HMAC-based token tied to the session's csrf_secret.
The token is placed in a readable (non-httpOnly) cookie so JavaScript can
read it, and must also be sent in the X-CSRF-Token header on mutating requests.
"""

import hashlib
import hmac
import time

from fastapi import HTTPException, Request, status

CSRF_COOKIE = "lsd_csrf"
CSRF_HEADER = "x-csrf-token"
_SEP = "."


def _sign(secret: str, value: str) -> str:
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def generate_csrf_token(csrf_secret: str) -> str:
    """Generate a time-stamped CSRF token signed with the session's secret."""
    timestamp = str(int(time.time()))
    sig = _sign(csrf_secret, timestamp)
    return f"{timestamp}{_SEP}{sig}"


def validate_csrf_token(csrf_secret: str, token: str) -> bool:
    try:
        timestamp_str, sig = token.split(_SEP, 1)
        expected = _sign(csrf_secret, timestamp_str)
        if not hmac.compare_digest(sig, expected):
            return False
        age = int(time.time()) - int(timestamp_str)
        return age >= 0
    except Exception:
        return False


def require_csrf(request: Request, csrf_secret: str) -> None:
    """Raise 403 if the CSRF token in the header doesn't validate."""
    token = request.headers.get(CSRF_HEADER)
    if not token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token missing")
    if not validate_csrf_token(csrf_secret, token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token invalid")
