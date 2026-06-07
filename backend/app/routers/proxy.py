"""Proxy router — transparent OpenRouter passthrough under /api/v1.

Mounts endpoints that mirror OpenRouter's surface:
  POST /api/v1/chat/completions
  POST /api/v1/completions
  GET  /api/v1/models
  GET  /api/v1/proxy/health

The chat/completions and completions endpoints flow through the plugin pipeline.
"""

import json
import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import settings
from app.models.api_key import ApiKey as ApiKeyModel
from app.models.user import User
from app.proxy.context import ProxyContext
from app.proxy.pipeline import PluginPipeline
from app.proxy.registry import get_pipeline
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
# Pipeline factory (lazy singleton)
# ---------------------------------------------------------------------------

def _build_pipeline() -> PluginPipeline:
    return PluginPipeline(get_pipeline())


# ---------------------------------------------------------------------------
# Health — proxy-specific readiness
# ---------------------------------------------------------------------------

@router.get("/proxy/health")
async def proxy_health():
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
async def proxy_models():
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
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except httpx.RequestError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


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

async def _build_context(request: Request, user: User, api_key: ApiKeyModel) -> ProxyContext:
    """Parse the incoming request body and build a ProxyContext."""
    # Read body once
    body_bytes = await request.body()
    if len(body_bytes) > settings.max_log_body_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Request body exceeds maximum allowed size",
        )

    try:
        request_body = json.loads(body_bytes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body")

    is_stream = request_body.get("stream", False) is True
    model = request_body.get("model", "unknown")

    return ProxyContext(
        user=user,
        api_key=api_key,
        request_body=request_body,
        request_headers=dict(request.headers),
        model=model,
        is_stream=is_stream,
    )


# ---------------------------------------------------------------------------
# Non-stream handler
# ---------------------------------------------------------------------------

async def _handle_non_stream(
    ctx: ProxyContext,
    pipeline: PluginPipeline,
    path: str = "/chat/completions",
) -> dict:
    """Run pipeline on_request, forward, run on_response, return response."""
    await pipeline.on_request(ctx)

    try:
        body = ctx.request_body
        upstream_response = await forward_non_stream(path, body)
    except httpx.HTTPStatusError as e:
        await pipeline.on_error(ctx, e)
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except httpx.RequestError as e:
        await pipeline.on_error(ctx, e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))

    # Populate context
    ctx.response_body = upstream_response
    ctx.status_code = 200
    choices = upstream_response.get("choices", [])
    if choices:
        ctx.finish_reason = choices[0].get("finish_reason")
    ctx.usage = upstream_response.get("usage")

    return upstream_response


# ---------------------------------------------------------------------------
# Stream handler
# ---------------------------------------------------------------------------

async def _handle_stream(
    ctx: ProxyContext,
    pipeline: PluginPipeline,
    path: str = "/chat/completions",
) -> StreamingResponse:
    """Set up SSE streaming relay through the pipeline."""
    await pipeline.on_request(ctx)

    # Ensure stream_options.include_usage is set
    body = ctx.request_body
    if not body.get("stream_options"):
        body["stream_options"] = {}
    body["stream_options"]["include_usage"] = True

    assembler = pipeline.start_stream(ctx)

    async def sse_generator():
        try:
            async for parsed, raw_bytes in forward_stream(path, body):
                if parsed is not None:
                    # Feed assembler
                    assembler.feed(parsed)
                    # Run through pipeline chunk hooks
                    relay = await pipeline.on_stream_chunk(ctx, parsed)
                    if relay is None:
                        continue  # chunk dropped by plugin
                    line = f"data: {json.dumps(relay)}\n\n"
                    yield line.encode()
                elif raw_bytes is not None:
                    yield raw_bytes

            # Stream complete — assemble full response for logging
            logger.info("Stream finished for model=%s — assembling response", ctx.model)
            ctx.response_body = assembler.assemble()
            ctx.usage = assembler.usage
            ctx.finish_reason = assembler.finish_reason
            ctx.status_code = 200

            # Fire on_response for logging (best-effort, after stream to client)
            logger.info("Calling pipeline.on_response for model=%s", ctx.model)
            await pipeline.on_response(ctx)
            logger.info("pipeline.on_response completed for model=%s", ctx.model)

        except httpx.HTTPStatusError as e:
            await pipeline.on_error(ctx, e)
            error_data = json.dumps({"error": str(e)})
            yield f"data: {error_data}\n\n".encode()
        except httpx.RequestError as e:
            await pipeline.on_error(ctx, e)
            error_data = json.dumps({"error": str(e)})
            yield f"data: {error_data}\n\n".encode()
        except Exception as e:
            logger.exception("Unexpected error in stream relay")
            await pipeline.on_error(ctx, e)

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

@router.post("/chat/completions")
async def proxy_chat_completions(
    request: Request,
    background_tasks: BackgroundTasks,
    auth: tuple[User, ApiKeyModel] = Depends(_proxy_auth),
):
    """Proxy a chat completion to OpenRouter.

    Supports both stream (SSE) and non-stream modes transparently.
    """
    if not settings.openrouter_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenRouter API key not configured",
        )

    user, api_key = auth
    ctx = await _build_context(request, user, api_key)
    pipeline = _build_pipeline()

    if ctx.is_stream:
        return await _handle_stream(ctx, pipeline)
    else:
        upstream_response = await _handle_non_stream(ctx, pipeline)

        # Logging fire-and-forget after response is returned to client
        async def log_after():
            try:
                await pipeline.on_response(ctx)
            except Exception:
                logger.exception("Background logging failed")

        background_tasks.add_task(log_after)
        return JSONResponse(content=upstream_response)


# ---------------------------------------------------------------------------
# POST /completions (legacy text completions)
# ---------------------------------------------------------------------------

@router.post("/completions")
async def proxy_completions(
    request: Request,
    background_tasks: BackgroundTasks,
    auth: tuple[User, ApiKeyModel] = Depends(_proxy_auth),
):
    """Proxy a text completion to OpenRouter."""
    if not settings.openrouter_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenRouter API key not configured",
        )

    user, api_key = auth
    ctx = await _build_context(request, user, api_key)
    pipeline = _build_pipeline()

    if ctx.is_stream:
        return await _handle_stream(ctx, pipeline, path="/completions")
    else:
        upstream_response = await _handle_non_stream(ctx, pipeline, path="/completions")

        async def log_after():
            try:
                await pipeline.on_response(ctx)
            except Exception:
                logger.exception("Background logging failed")

        background_tasks.add_task(log_after)
        return JSONResponse(content=upstream_response)
