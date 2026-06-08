"""Plugin configuration models — per-user global and per-conversation overrides."""

import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel, UniqueConstraint

from app.utils.time import utcnow


class PluginConfig(SQLModel, table=True):
    """Per-user global plugin enable/disable state.

    A missing row means "use the plugin's default based on PROXY_PLUGINS env".
    The `logging` plugin is locked enabled at the resolver level regardless of
    this row's value.
    """

    __tablename__ = "plugin_config"

    __table_args__ = (
        UniqueConstraint("user_id", "plugin_name", name="uq_plugin_config_user_plugin"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    plugin_name: str = Field(max_length=64)
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column_kwargs={"onupdate": utcnow},
    )


class PluginConfigConversation(SQLModel, table=True):
    """Per-conversation plugin override.

    When a row exists for (user_id, conversation_id, plugin_name), its `enabled`
    value beats both the user-global PluginConfig and the default.
    """

    __tablename__ = "plugin_config_conversation"

    __table_args__ = (
        UniqueConstraint(
            "user_id", "conversation_id", "plugin_name",
            name="uq_plugin_config_conv_user_conv_plugin",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    conversation_id: str = Field(max_length=256, index=True)
    plugin_name: str = Field(max_length=64)
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column_kwargs={"onupdate": utcnow},
    )