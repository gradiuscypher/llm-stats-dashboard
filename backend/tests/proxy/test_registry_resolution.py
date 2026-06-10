"""Tests for resolve_pipeline() plugin resolution logic.

Focus: the per-conversation override is the highest-priority layer and must be
able to *introduce* (enable) a plugin that is absent from PROXY_PLUGINS and
disabled/absent at the global level — not merely turn one off.

Note: logging is no longer a plugin (it's a built-in sink called directly by the
router). Tests that previously asserted on logging's presence are updated.
"""

import uuid

import pytest

from app.models.plugin_config import PluginConfig, PluginConfigConversation
from app.models.user import User
from app.proxy import registry
from app.proxy.registry import resolve_pipeline
from app.security.passwords import hash_password


@pytest.fixture(name="session")
def session_fixture(pg_session):
    """Use the Postgres test DB — the schema relies on PG-only column types."""
    yield pg_session


@pytest.fixture(name="uid")
def uid_fixture(session):
    """Create a user and return its id (plugin_config rows FK to users)."""
    user = User(
        username=f"resolver-{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("x"),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user.id


@pytest.fixture(autouse=True)
def _env_default_compression_only(monkeypatch):
    """Default deployment: PROXY_PLUGINS = 'compression' (logging is a sink).

    session_tracking is always default-enabled regardless of PROXY_PLUGINS.
    """
    monkeypatch.setattr(registry, "_DEFAULT_ENABLED", {"compression", "session_tracking"})
    from app.config import settings

    monkeypatch.setattr(settings, "proxy_plugins", "compression")


def _names(pipeline) -> list[str]:
    return [p.name for p in pipeline]


def test_compression_always_present_by_default(session, uid):
    """compression is the default in the env config."""
    pipeline = resolve_pipeline(uid, "conv-1", session)
    assert _names(pipeline) == ["compression"]


def test_conversation_override_can_enable_plugin(session, uid):
    """word_count enabled only at conversation level.

    Global is disabled, env does not include it — the conversation override must
    still turn it ON.
    """
    conv = "0557e40b-d295-4a5c-aefc-ed234262526b"

    session.add(PluginConfig(user_id=uid, plugin_name="word_count", enabled=False))
    session.add(
        PluginConfigConversation(
            user_id=uid,
            conversation_id=conv,
            plugin_name="word_count",
            enabled=True,
        )
    )
    session.commit()

    pipeline = resolve_pipeline(uid, conv, session)
    assert "word_count" in _names(pipeline)
    # compression stays on by default
    assert "compression" in _names(pipeline)


def test_conversation_override_can_disable_global_enabled(session, uid):
    """A conversation-level disable beats a global enable."""
    conv = "conv-disable"

    session.add(PluginConfig(user_id=uid, plugin_name="word_count", enabled=True))
    session.add(
        PluginConfigConversation(
            user_id=uid,
            conversation_id=conv,
            plugin_name="word_count",
            enabled=False,
        )
    )
    session.commit()

    pipeline = resolve_pipeline(uid, conv, session)
    assert "word_count" not in _names(pipeline)


def test_global_enable_applies_without_conversation_override(session, uid):
    session.add(PluginConfig(user_id=uid, plugin_name="word_count", enabled=True))
    session.commit()

    pipeline = resolve_pipeline(uid, "some-conv", session)
    assert "word_count" in _names(pipeline)


def test_conversation_override_isolated_to_its_conversation(session, uid):
    """Enabling word_count for conv A must not enable it for conv B."""
    session.add(
        PluginConfigConversation(
            user_id=uid,
            conversation_id="conv-A",
            plugin_name="word_count",
            enabled=True,
        )
    )
    session.commit()

    assert "word_count" in _names(resolve_pipeline(uid, "conv-A", session))
    assert "word_count" not in _names(resolve_pipeline(uid, "conv-B", session))


def test_is_plugin_enabled_default_true(session, uid):
    """session_tracking is default-enabled when no rows exist."""
    assert registry.is_plugin_enabled("session_tracking", uid, "any-conv", session) is True


def test_is_plugin_enabled_global_off(session, uid):
    """A global PluginConfig row with enabled=False overrides default."""
    session.add(PluginConfig(user_id=uid, plugin_name="session_tracking", enabled=False))
    session.commit()
    assert registry.is_plugin_enabled("session_tracking", uid, "any-conv", session) is False


def test_is_plugin_enabled_conv_override_beats_global(session, uid):
    """Per-conversation override beats both global and default."""
    conv = "conv-override-beats"
    session.add(PluginConfig(user_id=uid, plugin_name="session_tracking", enabled=False))
    session.add(
        PluginConfigConversation(
            user_id=uid, conversation_id=conv,
            plugin_name="session_tracking", enabled=True,
        )
    )
    session.commit()
    assert registry.is_plugin_enabled("session_tracking", uid, conv, session) is True


def test_is_plugin_enabled_conv_off_beats_global_on(session, uid):
    """Per-conversation DISABLE beats global enable."""
    conv = "conv-disable-override"
    session.add(PluginConfig(user_id=uid, plugin_name="session_tracking", enabled=True))
    session.add(
        PluginConfigConversation(
            user_id=uid, conversation_id=conv,
            plugin_name="session_tracking", enabled=False,
        )
    )
    session.commit()
    assert registry.is_plugin_enabled("session_tracking", uid, conv, session) is False


def test_all_plugin_names_includes_session_tracking(session, uid):
    """all_plugin_names() returns both transforms and body-level plugins."""
    names = registry.all_plugin_names()
    assert "session_tracking" in names
    assert "compression" in names
    assert "word_count" in names


def test_locked_plugins_still_can_be_disabled(session, uid):
    """LOCKED_PLUGINS is empty now (logging is no longer a plugin).
    Previously logging was locked; now compression is default but not locked."""
    session.add(PluginConfig(user_id=uid, plugin_name="compression", enabled=False))
    session.add(
        PluginConfigConversation(
            user_id=uid,
            conversation_id="conv-x",
            plugin_name="compression",
            enabled=False,
        )
    )
    session.commit()

    # compression can now be disabled (no longer locked)
    assert "compression" not in _names(resolve_pipeline(uid, "conv-x", session))
