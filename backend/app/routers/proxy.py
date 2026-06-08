"""Proxy router — transparent OpenRouter passthrough under /api/v1.

Mounts endpoints that mirror OpenRouter's surface:
  POST /api/v1/chat/completions
  POST /api/v1/completions
  GET  /api/v1/models
  GET  /api/v1/proxy/health

The interceptor runs request-side transforms BEFORE forwarding.
The logging sink persists log entries + diffs after the call.
Response is verbatim passthrough — no response/chunk interception.
"""

import copy
import json
import logging
from collections.abc import AsyncGenerator

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlmodel import Session

from app.config import settings
from app.db import get_session
from app.models.api_key import ApiKey as ApiKeyModel
from app.models.user import User
from app.proxy.assembler import StreamAssembler
from app.proxy.context import ProxyContext
from app.proxy.interceptor import RequestInterceptor, TransformContext
from app.proxy.logging_sink import persist_error_log, persist_log
from app.proxy.registry import resolve_pipeline
from app.proxy.upstream import (
    _build_upstream_headers,
    _strip_hop_by_hop,
    forward_non_stream,
    forward_stream,
)
from app.security.api_key_auth import get_current_user_from_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["proxy"])


# ---------------------------------------------------------------------------
# Health — proxy-specific readiness
# ---------------------------------------------------------------------------


@router.get("/proxy/health")
async def proxy_health() -> dict:
    """Return proxy readiness: upstream reachable, key configured."""
    problems: list[str] = []

    if not settings.openrouter_api_key:
        problems.append("OPENROUTER_API_KEY not configured")

    upstream_ok = False
    if settings.openrouter_api_key:
        try:
            headers = _build_upstream_headers()
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{settings.openrouter_base_url}/models",
                    headers=headers,
                )
                upstream_ok = resp.status_code < 500
        except Exception:
            upstream_ok = False

    if not upstream_ok:
        problems.append("upstream OpenRouter unreachable")

    return {
        "status": "ok" if not problems else "degraded",
        "upstream": settings.openrouter_base_url,
        "upstream_reachable": upstream_ok,
        "problems": problems,
    }


# ---------------------------------------------------------------------------
# Models — transparent passthrough
# ---------------------------------------------------------------------------


@router.get("/models")
async def proxy_models() -> JSONResponse:
    """Proxy OpenRouter's model list."""
    if not settings.openrouter_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenRouter API key not configured",
        )
    try:
        headers = _build_upstream_headers()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{settings.openrouter_base_url}/models",
                headers=headers,
            )
            resp.raise_for_status()
            return JSONResponse(
                content=resp.json(),
                headers=_strip_hop_by_hop(dict(resp.headers)),
            )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e)) from e
    except httpx.RequestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e


# ---------------------------------------------------------------------------
# Auth helper for proxy routes: validates proxy:use scope
# ---------------------------------------------------------------------------


async def _proxy_auth(
    auth: tuple[User, ApiKeyModel] = Depends(get_current_user_from_api_key),
) -> tuple[User, ApiKeyModel]:
    """Auth dependency that validates proxy:use scope on the resolved key."""
    user, api_key = auth
    if "proxy:use" not in api_key.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key missing required scope: proxy:use",
        )
    return auth


# ---------------------------------------------------------------------------
# Shared: build ProxyContext from incoming request
# ---------------------------------------------------------------------------


async def _build_context(
    request: Request, user: User, api_key: ApiKeyModel
) -> ProxyContext:
    """Parse the incoming request body and build a ProxyContext."""
    body_bytes = await request.body()
    if len(body_bytes) > settings.max_log_body_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Request body exceeds maximum allowed size",
        )

    try:
        request_body = json.loads(body_bytes)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON body",
        ) from None

    is_stream = request_body.get("stream", False) is True
    model = request_body.get("model", "unknown")

    # Snapshot original messages BEFORE the interceptor runs.
    # Used for conversation-identity inference (must be stable across
    # transform toggles) and for diff computation.
    original_request_messages = copy.deepcopy(
        request_body.get("messages", [])
    )

    return ProxyContext(
        user=user,
        api_key=api_key,
        request_body=request_body,
        original_request_messages=original_request_messages,
        request_headers=dict(request.headers),
        model=model,
        is_stream=is_stream,
    )


# ---------------------------------------------------------------------------
# Run interceptor on request messages
# ---------------------------------------------------------------------------


def _run_interceptor(
    ctx: ProxyContext,
    transforms: list,
) -> None:
    """Apply interceptor transforms to ctx.request_body[\"messages\"].

    Stores final messages back into ctx.request_body and diffs on ctx.
    """
    if not transforms:
        return

    messages = ctx.request_body.get("messages", [])
    if not messages:
        return

    tctx = TransformContext(
        model=ctx.model,
        user_id=str(ctx.user.id),
        request_metadata={},
    )

    interceptor = RequestInterceptor(transforms)
    result = interceptor.run(messages, tctx)

    # Write final messages back into the request body — these go upstream
    # and are interred as canonical.
    ctx.request_body["messages"] = result.final_messages
    ctx.request_diffs = result.diffs

    # Thread transform metrics (e.g. compression token savings) to ctx.state
    # so the logging sink + openrouter_map can persist them.
    if tctx.request_metadata.get("compression"):
        ctx.state["compression"] = tctx.request_metadata["compression"]


# ---------------------------------------------------------------------------
# Non-stream handler
# ---------------------------------------------------------------------------


async def _handle_non_stream(
    ctx: ProxyContext,
    path: str = "/chat/completions",
) -> dict:
    """Forward a non-stream request after running the interceptor.

    Response is verbatim — no plugin hooks touch it.
    """
    try:
        body = ctx.request_body
        upstream_response = await forward_non_stream(path, body)
    except httpx.HTTPStatusError as e:
        persist_error_log(ctx, e)
        raise HTTPException(status_code=e.response.status_code, detail=str(e)) from e
    except httpx.RequestError as e:
        persist_error_log(ctx, e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e

    # Populate context
    ctx.response_body = upstream_response
    ctx.status_code = 200
    choices = upstream_response.get("choices", [])
    if choices:
        ctx.finish_reason = choices[0].get("finish_reason")
    ctx.usage = upstream_response.get("usage")

    return ctx.response_body


# ---------------------------------------------------------------------------
# Stream handler
# ---------------------------------------------------------------------------


async def _handle_stream(
    ctx: ProxyContext,
    path: str = "/chat/completions",
) -> StreamingResponse:
    """Set up SSE streaming relay — no per-chunk plugin hooks.

    Response is streamed straight through to the client.  StreamAssembler
    still feeds for logging after the stream completes.
    """
    # Ensure stream_options.include_usage is set
    body = ctx.request_body
    if not body.get("stream_options"):
        body["stream_options"] = {}
    body["stream_options"]["include_usage"] = True

    assembler = StreamAssembler(ctx.model)

    async def sse_generator() -> AsyncGenerator[bytes, None]:
        try:
            async for parsed, raw_bytes in forward_stream(path, body):
                if parsed is not None:
                    # Feed assembler for logging (NOT for plugin chunk hooks).
                    assembler.feed(parsed)
                    line = f"data: {json.dumps(parsed)}\n\n"
                    yield line.encode()
                elif raw_bytes is not None:
                    yield raw_bytes

            # Stream complete — assemble full response for logging.
            logger.info(
                "Stream finished for model=%s — assembling response", ctx.model
            )
            ctx.response_body = assembler.assemble()
            ctx.usage = assembler.usage
            ctx.finish_reason = assembler.finish_reason
            ctx.status_code = 200

            # Persist log with verbatim assembled response.
            logger.info("Persisting log for model=%s", ctx.model)
            persist_log(ctx, ctx.response_body)
            logger.info("Log persisted for model=%s", ctx.model)

        except httpx.HTTPStatusError as e:
            persist_error_log(ctx, e)
            error_data = json.dumps({"error": str(e)})
            yield f"data: {error_data}\n\n".encode()
        except httpx.RequestError as e:
            persist_error_log(ctx, e)
            error_data = json.dumps({"error": str(e)})
            yield f"data: {error_data}\n\n".encode()
        except Exception as e:
            logger.exception("Unexpected error in stream relay")
            persist_error_log(ctx, e)

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# POST /chat/completions
# ---------------------------------------------------------------------------


@router.post("/chat/completions", response_model=None)
async def proxy_chat_completions(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
    auth: tuple[User, ApiKeyModel] = Depends(_proxy_auth),
) -> StreamingResponse | JSONResponse:
    """Proxy a chat completion to OpenRouter.

    Supports both stream (SSE) and non-stream modes transparently.
    Request transforms run before forwarding. Response is verbatim.
    """
    if not settings.openrouter_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenRouter API key not configured",
        )

    user, api_key = auth
    ctx = await _build_context(request, user, api_key)

    # Resolve conversation identity from ORIGINAL messages BEFORE transforms.
    # This keeps identity stable regardless of which transforms are enabled.
    conv_id_header = ctx.request_headers.get("x-conversation-id")
    from app.services.conversation_identity import infer_conversation_id

    candidate_conv, chain_keys, matched = infer_conversation_id(
        ctx.original_request_messages or [],
        user.id,
        db,
        explicit=conv_id_header,
        user_field=ctx.request_body.get("user"),
    )
    # Stash identity on context so the logging sink reuses it.
    ctx.state["identity"] = {
        "conversation_id": candidate_conv,
        "chain_key": chain_keys.chain_key,
        "chain_prefix_key": chain_keys.chain_prefix_key,
    }

    # Build transforms (no logging "plugin" — that's the sink now).
    transforms = resolve_pipeline(user.id, candidate_conv, db)

    # Run interceptor (request-side transforms).
    _run_interceptor(ctx, transforms)

    if ctx.is_stream:
        return await _handle_stream(ctx)
    else:
        upstream_response = await _handle_non_stream(ctx)

        # Logging fire-and-forget after response is returned to client
        async def log_after() -> None:
            try:
                persist_log(ctx, upstream_response)
            except Exception:
                logger.exception("Background logging failed")

        background_tasks.add_task(log_after)
        return JSONResponse(content=upstream_response)


# ---------------------------------------------------------------------------
# POST /completions (legacy text completions)
# ---------------------------------------------------------------------------


@router.post("/completions", response_model=None)
async def proxy_completions(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
    auth: tuple[User, ApiKeyModel] = Depends(_proxy_auth),
) -> StreamingResponse | JSONResponse:
    """Proxy a text completion to OpenRouter."""
    if not settings.openrouter_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenRouter API key not configured",
        )

    user, api_key = auth
    ctx = await _build_context(request, user, api_key)

    conv_id_header = ctx.request_headers.get("x-conversation-id")
    from app.services.conversation_identity import infer_conversation_id

    candidate_conv, chain_keys, matched = infer_conversation_id(
        ctx.original_request_messages or [],
        user.id,
        db,
        explicit=conv_id_header,
        user_field=ctx.request_body.get("user"),
    )
    ctx.state["identity"] = {
        "conversation_id": candidate_conv,
        "chain_key": chain_keys.chain_key,
        "chain_prefix_key": chain_keys.chain_prefix_key,
    }

    transforms = resolve_pipeline(user.id, candidate_conv, db)
    _run_interceptor(ctx, transforms)

    if ctx.is_stream:
        return await _handle_stream(ctx, path="/completions")
    else:
        upstream_response = await _handle_non_stream(ctx, path="/completions")

        async def log_after() -> None:
            try:
                persist_log(ctx, upstream_response)
            except Exception:
                logger.exception("Background logging failed")

        background_tasks.add_task(log_after)
        return JSONResponse(content=upstream_response)
