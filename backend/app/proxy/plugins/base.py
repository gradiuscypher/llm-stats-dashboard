"""Plugin base classes and protocol.

ProxyPlugin is the protocol that all proxy plugins conform to.
BasePlugin provides no-op defaults so plugins only implement what they need.
"""

from app.proxy.context import ProxyContext


class BasePlugin:
    """ABC with no-op defaults for all four hooks.

    Concrete plugins inherit from this and override only the hooks they need.
    """

    name: str

    async def on_request(self, ctx: ProxyContext) -> None:
        """Inspect / mutate ctx.request_body before forwarding."""

    async def on_stream_chunk(self, ctx: ProxyContext, chunk: dict) -> dict | None:
        """Called per SSE delta. Return a (possibly mutated) chunk to relay,
        or None to drop it. Default impl returns chunk unchanged."""
        return chunk

    async def on_response(self, ctx: ProxyContext) -> None:
        """Called once with the fully-assembled response."""

    async def on_error(self, ctx: ProxyContext, error: Exception) -> None:
        """Called if the upstream call or stream fails."""
