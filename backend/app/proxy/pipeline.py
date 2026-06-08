"""PluginPipeline — runs hooks on the proxy pipeline in order.

Owns the StreamAssembler for streaming requests and enforces isolation
principles (logging failures never break the proxied request).
"""

import logging

from app.proxy.assembler import StreamAssembler
from app.proxy.context import ProxyContext

logger = logging.getLogger(__name__)


class PluginPipeline:
    """Ordered pipeline of proxy plugins.

    Usage:
        pipeline = PluginPipeline(plugins)
        await pipeline.on_request(ctx)
        ... forward to upstream ...
        await pipeline.on_response(ctx)

    Stream path:
        pipeline = PluginPipeline(plugins)
        await pipeline.on_request(ctx)
        assembler = pipeline.start_stream(ctx)
        for chunk in upstream_stream:
            relay = await pipeline.on_stream_chunk(ctx, chunk)
            ... send relay to client ...
            assembler.feed(chunk)
        ctx.response_body = assembler.assemble()
        ctx.usage = assembler.usage
        ctx.finish_reason = assembler.finish_reason
        await pipeline.on_response(ctx)
    """

    def __init__(self, plugins: list) -> None:
        self._plugins = plugins

    # ------------------------------------------------------------------
    # on_request
    # ------------------------------------------------------------------

    async def on_request(self, ctx: ProxyContext) -> None:
        """Run on_request hooks in registration order.

        Each plugin may mutate ctx.request_body. Failures here are surfaced
        (mutator plugins need the call to abort on error).
        """
        for plugin in self._plugins:
            try:
                await plugin.on_request(ctx)
            except Exception:
                logger.exception("Plugin %r on_request failed", plugin.name)
                raise

    # ------------------------------------------------------------------
    # on_response
    # ------------------------------------------------------------------

    async def on_response_sync(self, ctx: ProxyContext) -> None:
        """Run on_response_sync hooks in order (inline, before returning to client).

        Mutator plugins implement this to touch the client-visible body.
        Errors are caught & logged per-plugin (fail-open) so a buggy
        plugin never breaks the client response.
        """
        for plugin in self._plugins:
            try:
                await plugin.on_response_sync(ctx)
            except Exception:
                logger.exception("Plugin %r on_response_sync failed", plugin.name)

    async def on_response(self, ctx: ProxyContext) -> None:
        """Run on_response hooks in order. Side-effect oriented (logging).

        Exceptions are caught & logged per-plugin — a logging failure must
        never fail the user's request.
        """
        for plugin in self._plugins:
            try:
                await plugin.on_response(ctx)
            except Exception:
                logger.exception("Plugin %r on_response failed", plugin.name)

    # ------------------------------------------------------------------
    # on_stream_chunk
    # ------------------------------------------------------------------

    def start_stream(self, ctx: ProxyContext) -> StreamAssembler:
        """Create and return a StreamAssembler for this streaming request."""
        return StreamAssembler(ctx.model)

    async def on_stream_chunk(self, ctx: ProxyContext, chunk: dict) -> dict | None:
        """Run on_stream_chunk hooks in order, chaining the chunk through.

        A None return from any plugin drops the chunk from the relayed stream.
        Errors in a chunk hook are isolated — chunk relayed unchanged.
        """
        current = chunk
        for plugin in self._plugins:
            try:
                result = await plugin.on_stream_chunk(ctx, current)
                if result is None:
                    return None
                current = result
            except Exception:
                logger.exception("Plugin %r on_stream_chunk failed", plugin.name)
                # On error, relay the original chunk unchanged
        return current

    # ------------------------------------------------------------------
    # on_error
    # ------------------------------------------------------------------

    async def on_error(self, ctx: ProxyContext, error: Exception) -> None:
        """Run on_error hooks in order; best-effort."""
        ctx.error = error
        for plugin in self._plugins:
            try:
                await plugin.on_error(ctx, error)
            except Exception:
                logger.exception("Plugin %r on_error failed", plugin.name)
