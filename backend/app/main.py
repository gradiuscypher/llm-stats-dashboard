"""FastAPI application factory."""

from app.logging_config import configure_logging

configure_logging()

import logging  # noqa: E402

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from slowapi import Limiter, _rate_limit_exceeded_handler  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402
from slowapi.util import get_remote_address  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint  # noqa: E402
from starlette.responses import Response as StarletteResponse  # noqa: E402

from app.config import settings  # noqa: E402
from app.routers import api_keys, auth, docs_router, health, logs, proxy, users  # noqa: E402

request_logger = logging.getLogger("app.requests")

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------
class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(  # type: ignore[override]
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> StarletteResponse:
        # Log auth headers at DEBUG so they're visible when diagnosing 401s.
        # We redact the key value but show which header was used + key prefix.
        auth_header = request.headers.get("authorization", "")
        x_api_key = request.headers.get("x-api-key", "")
        if auth_header:
            # Show scheme + first 20 chars of credential only
            parts = auth_header.split(" ", 1)
            scheme = parts[0]
            cred = parts[1][:20] + "..." if len(parts) > 1 else ""
            auth_display = f"{scheme} {cred}"
        elif x_api_key:
            auth_display = f"X-API-Key {x_api_key[:20]}..."
        else:
            auth_display = "(none)"

        request_logger.debug(
            "%s %s  auth=%s",
            request.method,
            request.url.path,
            auth_display,
        )

        response = await call_next(request)

        request_logger.debug(
            "%s %s  → %s",
            request.method,
            request.url.path,
            response.status_code,
        )
        return response


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(  # type: ignore[override]
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> StarletteResponse:
        response = await call_next(request)

        # Content-Security-Policy
        # Fonts are loaded from /fonts (same origin). Adjust as needed.
        csp = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "  # Tailwind needs this in dev; tighten in prod
            "font-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self';"
        )
        response.headers["Content-Security-Policy"] = csp
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"

        return response


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    app = FastAPI(
        title="LLM Stats Dashboard API",
        version="0.1.0",
        description=(
            "Backend API for the LLM Stats Dashboard. "
            "Tracks LLM usage logs, token counts, costs, and tool calls. "
            "See `/api/v1/docs-md` for human/AI-readable Markdown documentation."
        ),
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # Rate limiter
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    # Request logging (DEBUG-level; logs auth header prefix + status)
    app.add_middleware(RequestLoggingMiddleware)

    # Security headers
    app.add_middleware(SecurityHeadersMiddleware)

    # CORS — credentials allowed only for the configured frontend origin
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-CSRF-Token"],
    )

    # Routers
    prefix = "/api/v1"
    app.include_router(health.router)
    app.include_router(auth.router, prefix=prefix)
    app.include_router(users.router, prefix=prefix)
    app.include_router(api_keys.router, prefix=prefix)
    app.include_router(logs.router, prefix=prefix)
    app.include_router(docs_router.router, prefix=prefix)
    # Proxy router is registered last — its paths don't collide with existing routes
    app.include_router(proxy.router, prefix=prefix)

    return app


app = create_app()
