"""CompressionPlugin — stub demonstrating safe request mutation.

MVP: a documented no-op skeleton.  Implements on_request only, with a
fail-open contract (skip on error).  Records bytes/tokens saved (always 0)
in ctx.state for the logging plugin to capture in metadata.

Concrete compression strategies are deferred to post-MVP.
"""

from app.proxy.context import ProxyContext
from app.proxy.plugins.base import BasePlugin


class CompressionPlugin(BasePlugin):
    name = "compression"

    async def on_request(self, ctx: ProxyContext) -> None:
        """Placeholder for message compression / history trimming.

        When implemented, this would trim or deduplicate messages in
        ctx.request_body["messages"] to reduce token usage, and record
        savings in ctx.state["compression"].
        """
        ctx.state["compression"] = {
            "saved_tokens": 0,
            "saved_bytes": 0,
            "strategy": "none",
        }
