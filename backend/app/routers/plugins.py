"""Plugin configuration endpoints.

Per-user global plugin toggles and per-conversation overrides.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.db import get_session
from app.models.plugin_config import PluginConfig, PluginConfigConversation
from app.proxy.registry import _DEFAULT_ENABLED, LOCKED_PLUGINS, all_plugin_names
from app.schemas.plugin import (
    ConversationPluginState,
    PluginInfo,
    PluginUpdateRequest,
)
from app.security.sessions import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["plugins"])


# ---------------------------------------------------------------------------
# Plugin metadata descriptions (static)
# ---------------------------------------------------------------------------

PLUGIN_META: dict[str, str] = {
    "logging": "Automatically logs every proxied LLM call to the dashboard.",
    "compression": "Reduces tokens via Headroom: SmartCrusher, CacheAligner, CCR, ONNX Kompress.",
    "word_count": "Appends a word-count marker to messages sent to and received from the provider.",
    "session_tracking": "Passes conversation ID as session_id to OpenRouter for group debugging.",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_global_state(
    user_id: uuid.UUID,
    db: Session,
) -> dict[str, bool | None]:
    """Return {plugin_name: enabled | None} for the user's global config.

    None means no row exists (use default).
    """
    rows = db.exec(
        select(PluginConfig).where(PluginConfig.user_id == user_id)
    ).all()
    return {r.plugin_name: r.enabled for r in rows}


def _resolve_conversation_state(
    user_id: uuid.UUID,
    conversation_id: str,
    db: Session,
) -> dict[str, bool]:
    """Return {plugin_name: enabled} for per-conversation overrides."""
    rows = db.exec(
        select(PluginConfigConversation).where(
            PluginConfigConversation.user_id == user_id,
            PluginConfigConversation.conversation_id == conversation_id,
        )
    ).all()
    return {r.plugin_name: r.enabled for r in rows}


# ---------------------------------------------------------------------------
# Global plugin list
# ---------------------------------------------------------------------------


@router.get("/plugins", response_model=list[PluginInfo])
async def list_plugins(
    request: Request,
    db: Session = Depends(get_session),
) -> list[PluginInfo]:
    """List all available plugins with the caller's per-user enabled state."""
    user = get_current_user(request, db)
    global_state = _resolve_global_state(user.id, db)

    result: list[PluginInfo] = []
    for name in all_plugin_names():
        desc = PLUGIN_META.get(name, "")
        locked = name in LOCKED_PLUGINS
        user_enabled = global_state.get(name)
        result.append(
            PluginInfo(
                name=name,
                description=desc,
                default_enabled=name in _DEFAULT_ENABLED,
                locked=locked,
                user_enabled=user_enabled,
            )
        )
    return result


# ---------------------------------------------------------------------------
# Per-user global toggle
# ---------------------------------------------------------------------------


@router.put("/plugins/{plugin_name}", response_model=PluginInfo)
async def set_plugin_global(
    plugin_name: str,
    body: PluginUpdateRequest,
    request: Request,
    db: Session = Depends(get_session),
) -> PluginInfo:
    """Set the per-user global enabled state for a plugin.

    Requires session (CSRF).  Returns the updated plugin info.
    """
    user = get_current_user(request, db)

    if plugin_name not in all_plugin_names():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown plugin: {plugin_name}",
        )

    if plugin_name in LOCKED_PLUGINS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Plugin '{plugin_name}' is locked and cannot be toggled",
        )

    # Upsert
    row = db.exec(
        select(PluginConfig).where(
            PluginConfig.user_id == user.id,
            PluginConfig.plugin_name == plugin_name,
        )
    ).first()

    if row:
        old_enabled = row.enabled
        row.enabled = body.enabled
        logger.info(
            "plugin_global_update user_id=%s plugin=%s old_enabled=%s new_enabled=%s",
            user.id, plugin_name, old_enabled, body.enabled,
        )
    else:
        row = PluginConfig(
            user_id=user.id,
            plugin_name=plugin_name,
            enabled=body.enabled,
        )
        db.add(row)
        logger.info(
            "plugin_global_create user_id=%s plugin=%s enabled=%s",
            user.id, plugin_name, body.enabled,
        )
    db.commit()
    db.refresh(row)

    return PluginInfo(
        name=plugin_name,
        description=PLUGIN_META.get(plugin_name, ""),
        default_enabled=plugin_name in _DEFAULT_ENABLED,
        locked=plugin_name in LOCKED_PLUGINS,
        user_enabled=row.enabled,
    )


# ---------------------------------------------------------------------------
# Per-conversation plugin state
# ---------------------------------------------------------------------------


@router.get(
    "/conversations/{conversation_id}/plugins",
    response_model=list[ConversationPluginState],
)
async def list_conversation_plugins(
    conversation_id: str,
    request: Request,
    db: Session = Depends(get_session),
) -> list[ConversationPluginState]:
    """Get the effective plugin state for a specific conversation.

    Shows global setting, any per-conversation override, and the effective
    (resolved) enabled state.
    """
    user = get_current_user(request, db)
    global_state = _resolve_global_state(user.id, db)
    conv_state = _resolve_conversation_state(user.id, conversation_id, db)

    result: list[ConversationPluginState] = []
    for name in all_plugin_names():
        desc = PLUGIN_META.get(name, "")
        locked = name in LOCKED_PLUGINS
        global_enabled = global_state.get(name)
        override_enabled = conv_state.get(name)

        if locked:
            effective = True
        elif name in conv_state:
            effective = conv_state[name]
        elif name in global_state:
            effective = global_state[name]
        else:
            effective = name in _DEFAULT_ENABLED

        result.append(
            ConversationPluginState(
                name=name,
                description=desc,
                locked=locked,
                global_enabled=global_enabled,
                override_enabled=override_enabled,
                effective=effective,  # ty:ignore[invalid-argument-type]
            )
        )
    return result


@router.put(
    "/conversations/{conversation_id}/plugins/{plugin_name}",
    response_model=ConversationPluginState,
)
async def set_conversation_plugin_override(
    conversation_id: str,
    plugin_name: str,
    body: PluginUpdateRequest,
    request: Request,
    db: Session = Depends(get_session),
) -> ConversationPluginState:
    """Set or update a per-conversation plugin override.

    Requires session (CSRF).
    """
    user = get_current_user(request, db)

    if plugin_name not in all_plugin_names():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown plugin: {plugin_name}",
        )

    if plugin_name in LOCKED_PLUGINS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Plugin '{plugin_name}' is locked and cannot be overridden",
        )

    # Upsert
    row = db.exec(
        select(PluginConfigConversation).where(
            PluginConfigConversation.user_id == user.id,
            PluginConfigConversation.conversation_id == conversation_id,
            PluginConfigConversation.plugin_name == plugin_name,
        )
    ).first()

    if row:
        row.enabled = body.enabled
        logger.info(
            "plugin_conv_override_update user_id=%s conversation_id=%s plugin=%s enabled=%s",
            user.id, conversation_id, plugin_name, body.enabled,
        )
    else:
        row = PluginConfigConversation(
            user_id=user.id,
            conversation_id=conversation_id,
            plugin_name=plugin_name,
            enabled=body.enabled,
        )
        db.add(row)
        logger.info(
            "plugin_conv_override_create user_id=%s conversation_id=%s plugin=%s enabled=%s",
            user.id, conversation_id, plugin_name, body.enabled,
        )
    db.commit()
    db.refresh(row)
    assert row is not None  # guaranteed by both branches above

    # Recompute effective state
    global_state = _resolve_global_state(user.id, db)
    global_enabled = global_state.get(plugin_name)
    locked = plugin_name in LOCKED_PLUGINS

    effective = True if locked else row.enabled

    return ConversationPluginState(
        name=plugin_name,
        description=PLUGIN_META.get(plugin_name, ""),
        locked=locked,
        global_enabled=global_enabled,
        override_enabled=row.enabled,
        effective=effective,
    )


@router.delete("/conversations/{conversation_id}/plugins/{plugin_name}", status_code=204)
async def delete_conversation_plugin_override(
    conversation_id: str,
    plugin_name: str,
    request: Request,
    db: Session = Depends(get_session),
) -> None:
    """Remove a per-conversation plugin override, reverting to the global setting.

    Requires session (CSRF).
    """
    user = get_current_user(request, db)

    if plugin_name in LOCKED_PLUGINS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Plugin '{plugin_name}' is locked and its override cannot be removed",
        )

    row = db.exec(
        select(PluginConfigConversation).where(
            PluginConfigConversation.user_id == user.id,
            PluginConfigConversation.conversation_id == conversation_id,
            PluginConfigConversation.plugin_name == plugin_name,
        )
    ).first()

    if row is not None:
        db.delete(row)
        db.commit()
        logger.info(
            "plugin_conv_override_delete user_id=%s conversation_id=%s plugin=%s "
            "override removed → reverting to global",
            user.id, conversation_id, plugin_name,
        )

    return None