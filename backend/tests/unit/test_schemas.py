"""Unit tests for log entry schema validation."""

import pytest
from pydantic import ValidationError

from app.schemas.log_entry import LogEntryCreate


def _valid_payload(**kwargs) -> dict:
    base = {
        "provider": "openai",
        "model": "gpt-4o",
        "request": {
            "messages": [{"role": "user", "content": "Hello"}]
        },
        "response": {
            "message": {"role": "assistant", "content": "Hi there!"}
        },
    }
    base.update(kwargs)
    return base


def test_valid_minimal_entry():
    entry = LogEntryCreate(**_valid_payload())
    assert entry.provider == "openai"
    assert entry.status == "ok"
    assert entry.tool_calls == []


def test_error_status_requires_error_field():
    with pytest.raises(ValidationError, match="error"):
        LogEntryCreate(**_valid_payload(status="error"))


def test_error_status_with_error_field_passes():
    entry = LogEntryCreate(**_valid_payload(status="error", error="timeout"))
    assert entry.error == "timeout"


def test_multipart_content():
    entry = LogEntryCreate(**_valid_payload(
        request={"messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}
    ))
    assert isinstance(entry.request.messages[0].content, list)


def test_metadata_passthrough():
    entry = LogEntryCreate(**_valid_payload(metadata={"custom_tag": "ci-run-42"}))
    assert entry.metadata["custom_tag"] == "ci-run-42"


def test_conversation_id_optional():
    entry = LogEntryCreate(**_valid_payload())
    assert entry.conversation_id is None
    entry2 = LogEntryCreate(**_valid_payload(conversation_id="session-abc"))
    assert entry2.conversation_id == "session-abc"
