"""Logging sink — persists proxy log entries and diffs after a proxied call.

Called by the proxy router, NOT as a transform plugin.  The sink:
1. Maps the OpenRouter request/response to a canonical LogEntryCreate.
2. Interns the ORIGINAL (pre-interceptor) request messages as canonical.
   Transform diffs are an overlay — identity is stable across plugin toggles.
3. Interns the verbatim upstream response.
4. Persists structured request_diffs via the diffs service.
5. Handles error entries (persist_error_log).
"""

import logging

from sqlmodel import Session

from app.db import engine
from app.proxy.context import ProxyContext
from app.services.diffs import persist_diffs
from app.services.ingest import ingest_log_entry
from app.services.messages import intern_messages
from app.services.openrouter_map import (
    map_error_to_log_entry,
    map_to_log_entry,
)

logger = logging.getLogger(__name__)


def persist_log(ctx: ProxyContext, response_body: dict) -> None:
    """Map and persist the completed request/response + diffs."""
    try:
        # Reuse identity resolved pre-call (stashed on ctx.state by proxy router).
        identity = ctx.state.get("identity", {})
        resolved_conv_id = identity.get("conversation_id")
        chain_key = identity.get("chain_key")
        chain_prefix_key = identity.get("chain_prefix_key")

        payload = map_to_log_entry(
            ctx,
            response_body,
            conversation_id_header=resolved_conv_id,
        )
        payload.conversation_id = resolved_conv_id or payload.conversation_id

        with Session(engine) as db:
            # Intern FINAL request messages + verbatim response.
            raw_messages = [
                m.model_dump(exclude_none=True) for m in payload.request.messages
            ]
            raw_response = payload.response.message.model_dump(exclude_none=True)
            raw_messages.append(raw_response)
            intern_messages(raw_messages, ctx.user.id, db)

            entry = ingest_log_entry(
                payload,
                ctx.user.id,
                db,
                api_key_id=ctx.api_key.id,
                chain_key=chain_key,
                chain_prefix_key=chain_prefix_key,
            )

            # Persist structured request_diffs.
            if ctx.request_diffs:
                persist_diffs(ctx.request_diffs, entry.id, ctx.user.id, db)
                db.commit()
    except Exception:
        logger.exception("persist_log failed")


def persist_error_log(ctx: ProxyContext, error: Exception) -> None:
    """Persist the failed request as an error entry, with request diffs."""
    try:
        identity = ctx.state.get("identity", {})
        resolved_conv_id = identity.get("conversation_id")
        chain_key = identity.get("chain_key")
        chain_prefix_key = identity.get("chain_prefix_key")

        payload = map_error_to_log_entry(
            ctx, error, conversation_id_header=resolved_conv_id
        )
        payload.conversation_id = resolved_conv_id or payload.conversation_id

        with Session(engine) as db:
            # Intern request messages only — no assistant reply on errors.
            raw_messages = [
                m.model_dump(exclude_none=True) for m in payload.request.messages
            ]
            intern_messages(raw_messages, ctx.user.id, db)

            entry = ingest_log_entry(
                payload,
                ctx.user.id,
                db,
                api_key_id=ctx.api_key.id,
                chain_key=chain_key,
                chain_prefix_key=chain_prefix_key,
            )

            # Persist request diffs even on error.
            if ctx.request_diffs:
                persist_diffs(ctx.request_diffs, entry.id, ctx.user.id, db)
                db.commit()
    except Exception:
        logger.exception("persist_error_log failed")
