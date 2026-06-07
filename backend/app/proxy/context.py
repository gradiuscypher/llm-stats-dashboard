"""ProxyContext — the single object passed through the entire proxy pipeline."""

import time
from dataclasses import dataclass, field
from typing import Any

from app.models.api_key import ApiKey
from app.models.user import User


@dataclass
class ProxyContext:
    """Mutable context carried through the proxy request lifecycle.

    Plugins read and write this context.  The pipeline owns one instance per
    request and passes it to every plugin hook.
    """

    # Identity (set by auth layer)
    user: User
    api_key: ApiKey

    # Request (populated by the router from the incoming call)
    request_body: dict          # parsed JSON body (mutable by plugins)
    request_headers: dict
    model: str
    is_stream: bool
    started_at: float = field(default_factory=time.time)

    # Response (populated as we go)
    response_body: dict | None = None       # assembled (non-stream or end-of-stream)
    response_headers: dict | None = None
    status_code: int | None = None
    finish_reason: str | None = None
    usage: dict | None = None
    error: Exception | None = None

    # Scratch space for inter-plugin state, keyed by plugin name
    state: dict[str, Any] = field(default_factory=dict)

    # Plugins may set this to short-circuit (e.g. cache hit) — future
    short_circuit_response: dict | None = None
