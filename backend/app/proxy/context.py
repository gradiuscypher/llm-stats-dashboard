"""ProxyContext — the single object passed through the entire proxy request."""

import time
from dataclasses import dataclass, field
from typing import Any

from app.models.api_key import ApiKey
from app.models.user import User
from app.proxy.interceptor import MessageDiff


@dataclass
class ProxyContext:
    """Mutable context carried through the proxy request lifecycle.

    Transforms and the logging sink read and write this context.
    One instance per request.
    """

    # Identity (set by auth layer)
    user: User
    api_key: ApiKey

    # Request (populated by the router from the incoming call)
    request_body: dict  # parsed JSON body (mutated by interceptor)
    request_headers: dict
    model: str
    is_stream: bool
    started_at: float = field(default_factory=time.time)

    # Snapshot of the original request messages, taken before the interceptor runs.
    # Used for conversation-identity inference (identity must be stable across
    # transform toggles) and for diff computation.
    original_request_messages: list[dict] | None = None

    # Response (populated as we go)
    response_body: dict | None = None  # assembled (non-stream or end-of-stream)
    response_headers: dict | None = None
    status_code: int | None = None
    finish_reason: str | None = None
    usage: dict | None = None
    error: Exception | None = None

    # Scratch space for inter-plugin state, keyed by plugin name
    state: dict[str, Any] = field(default_factory=dict)

    # Plugins may set this to short-circuit (e.g. cache hit)
    short_circuit_response: dict | None = None

    # Structured diffs — populated by the interceptor, persisted by the logging sink.
    request_diffs: list[MessageDiff] = field(default_factory=list)
