"""Plugin API schemas."""

import uuid

from pydantic import BaseModel


class PluginInfo(BaseModel):
    """Static plugin metadata + per-user enabled state."""

    name: str
    description: str
    default_enabled: bool
    locked: bool = False  # true for logging (always-on)
    user_enabled: bool | None = None  # None = no user config row (use default)


class PluginUpdateRequest(BaseModel):
    """Request body for updating per-user global plugin state."""

    enabled: bool


class ConversationPluginState(BaseModel):
    """State of one plugin for a specific conversation."""

    name: str
    description: str
    locked: bool = False
    global_enabled: bool | None = None
    override_enabled: bool | None = None  # None = no per-conversation override
    effective: bool  # resolved enabled state for this conversation


class PluginConfigPublic(BaseModel):
    """Public representation of a plugin_config row (for API responses)."""

    id: uuid.UUID
    plugin_name: str
    enabled: bool

    model_config = {"from_attributes": True}


class PluginConversationOverridePublic(BaseModel):
    """Public representation of a per-conversation override row."""

    id: uuid.UUID
    conversation_id: str
    plugin_name: str
    enabled: bool

    model_config = {"from_attributes": True}