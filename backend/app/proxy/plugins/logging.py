"""LoggingPlugin — maps OpenRouter request/response to canonical LogEntryCreate.

Implements on_response and on_error.  Calls the existing ingest_log_entry
service so the dashboard read path works with zero changes.
"""

import logging

from sqlmodel import Session

from app.db import engine
from app.proxy.context import ProxyContext
from app.proxy.plugins.base import BasePlugin
from app.services.ingest import ingest_log_entry
from app.services.messages import intern_messages
from app.services.openrouter_map import (
    derive_conversation_id,
    map_error_to_log_entry,
    map_to_log_entry,
)

logger = logging.getLogger(__name__)


class LoggingPlugin(BasePlugin):
    name = "logging"

    async def on_response(self, ctx: ProxyContext) -> None:
        """Map and persist the completed request/response."""
        if ctx.response_body is None:
            logger.warning("LoggingPlugin.on_response called with no response_body")
            return

        try:
            # Extract X-Conversation-Id header if present
            conv_id_header = ctx.request_headers.get("x-conversation-id")
            payload = map_to_log_entry(
                ctx,
                ctx.response_body,
                conversation_id_header=conv_id_header,
            )

            with Session(engine) as db:
                # Intern request messages so prefix-chain derivation works.
                # ingest_log_entry re-interns below — idempotent, returns same IDs.
                raw_messages = [m.model_dump(exclude_none=True) for m in payload.request.messages]
                message_ids = intern_messages(raw_messages, ctx.user.id, db)

                # Derive conversation_id with structural prefix-chain resolution.
                # Passing message_ids+db enables prefix-ancestor inheritance (step 3);
                # without them it falls back to a fresh UUID (step 4).
                derived_conv_id = derive_conversation_id(
                    ctx.request_body,
                    ctx.api_key.prefix,
                    explicit=conv_id_header,
                    message_ids=message_ids,
                    user_id=ctx.user.id,
                    db=db,
                )
                payload.conversation_id = derived_conv_id

                ingest_log_entry(payload, ctx.user.id, db, api_key_id=ctx.api_key.id)
        except Exception:
            logger.exception("LoggingPlugin failed to persist log entry")

    async def on_error(self, ctx: ProxyContext, error: Exception) -> None:
        """Persist the failed request as an error entry."""
        try:
            conv_id_header = ctx.request_headers.get("x-conversation-id")
            payload = map_error_to_log_entry(ctx, error, conversation_id_header=conv_id_header)

            with Session(engine) as db:
                # Intern request messages for prefix-chain derivation (see on_response)
                raw_messages = [m.model_dump(exclude_none=True) for m in payload.request.messages]
                message_ids = intern_messages(raw_messages, ctx.user.id, db)

                derived_conv_id = derive_conversation_id(
                    ctx.request_body,
                    ctx.api_key.prefix,
                    explicit=conv_id_header,
                    message_ids=message_ids,
                    user_id=ctx.user.id,
                    db=db,
                )
                payload.conversation_id = derived_conv_id

                ingest_log_entry(payload, ctx.user.id, db, api_key_id=ctx.api_key.id)
        except Exception:
            logger.exception("LoggingPlugin failed to persist error log entry")
