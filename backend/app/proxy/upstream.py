"""httpx client to OpenRouter — handles both stream and non-stream requests."""

from collections.abc import AsyncIterator

import httpx

from app.config import settings


def _build_upstream_headers(extra: dict | None = None) -> dict:
    """Build headers forwarded to OpenRouter, injecting the server-held API key."""
    headers: dict = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    if settings.openrouter_referer:
        headers["HTTP-Referer"] = settings.openrouter_referer
    if settings.openrouter_app_title:
        headers["X-Title"] = settings.openrouter_app_title
    if extra:
        headers.update(extra)
    return headers


def _strip_hop_by_hop(headers: dict) -> dict:
    """Remove hop-by-hop and auth headers from upstream response headers."""
    hop_by_hop = {
        "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade",
        "authorization", "x-api-key",
    }
    return {k: v for k, v in headers.items() if k.lower() not in hop_by_hop}


async def forward_non_stream(
    path: str,
    body: dict,
    extra_upstream_headers: dict | None = None,
) -> dict:
    """Forward a non-streaming request to OpenRouter and return the full JSON response."""
    url = f"{settings.openrouter_base_url}{path}"
    headers = _build_upstream_headers(extra_upstream_headers)
    async with httpx.AsyncClient(timeout=settings.proxy_upstream_timeout_s) as client:
        response = await client.post(url, json=body, headers=headers)
        response.raise_for_status()
        return response.json()


async def forward_stream(
    path: str,
    body: dict,
    extra_upstream_headers: dict | None = None,
) -> AsyncIterator[tuple[dict | None, bytes | None]]:
    """Forward a streaming request and yield (parsed_chunk_or_None, raw_line_bytes).

    Yields tuples of:
      - (parsed_dict, None) for data: {...} lines
      - (None, raw_bytes) for non-JSON lines (comments, keep-alives, [DONE])
    """
    url = f"{settings.openrouter_base_url}{path}"
    headers = _build_upstream_headers(extra_upstream_headers)
    async with (
        httpx.AsyncClient(timeout=settings.proxy_upstream_timeout_s) as client,
    ):
        async with client.stream("POST", url, json=body, headers=headers) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    yield None, (line + "\n").encode()
                    continue
                data_str = line[6:]  # strip "data: " prefix
                if data_str == "[DONE]":
                    yield None, b"data: [DONE]\n\n"
                    continue
                try:
                    import json
                    yield json.loads(data_str), None
                except json.JSONDecodeError:
                    yield None, (line + "\n").encode()
