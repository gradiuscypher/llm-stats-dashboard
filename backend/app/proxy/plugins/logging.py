"""LoggingPlugin — maps OpenRouter request/response to canonical LogEntryCreate.

Implements on_response and on_error.  Calls the existing ingest_log_entry
service so the dashboard read path works with zero changes.
"""

import logging

from sqlmodel import Session

from app.db import engine
from app.proxy.plugins.base import BasePlugin
from app.services.ingest import ingest_log_entry
from app.services.openrouter_map import map_error_to_log_entry, map_to_log_entry

logger = logging.getLogger(__name__)


class LoggingPlugin(BasePlugin):
    name = "logging"

    async def on_response(self, ctx) -> None:
        """Map and persist the completed request/response."""
        if ctx.response_body is None:
            logger.warning("LoggingPlugin.on_response called with no response_body")
            return

        try:
            # Extract X-Conversation-Id header if present
            conv_id_header = ctx.request_headers.get("x-conversation-id")
            payload = map_to_log_entry(ctx, ctx.response_body, conversation_id_header=conv_id_header)

            # Background the DB write so it doesn't block the response
            with Session(engine) as db:
                ingest_log_entry(payload, ctx.user.id, db)
        except Exception:
            logger.exception("LoggingPlugin failed to persist log entry")

    async def on_error(self, ctx, error: Exception) -> None:
        """Persist the failed request as an error entry."""
        try:
            conv_id_header = ctx.request_headers.get("x-conversation-id")
            payload = map_error_to_log_entry(ctx, error, conversation_id_header=conv_id_header)

            with Session(engine) as db:
                ingest_log_entry(payload, ctx.user.id, db)
        except Exception:
            logger.exception("LoggingPlugin failed to persist error log entry")
