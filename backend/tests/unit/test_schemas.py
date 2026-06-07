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


def test_reasoning_fields_on_request_message():
    entry = LogEntryCreate(**_valid_payload(
        request={
            "messages": [{
                "role": "assistant",
                "content": "Earlier answer.",
                "reasoning": "Earlier thinking.",
                "reasoning_details": [{"type": "reasoning.text", "text": "step 1"}],
            }]
        }
    ))
    assert entry.request.messages[0].reasoning == "Earlier thinking."
    assert entry.request.messages[0].reasoning_details is not None
    assert entry.request.messages[0].reasoning_details[0]["type"] == "reasoning.text"


def test_reasoning_fields_on_response_message():
    entry = LogEntryCreate(**_valid_payload(
        response={
            "message": {
                "role": "assistant",
                "content": "Here.",
                "reasoning": "I thought about it.",
                "reasoning_details": [{"type": "redacted"}],
            }
        },
    ))
    assert entry.response.message.reasoning == "I thought about it."
    assert entry.response.message.reasoning_details is not None
    assert len(entry.response.message.reasoning_details) == 1


def test_reasoning_tokens_in_usage():
    from app.schemas.log_entry import UsagePayload
    usage = UsagePayload(
        prompt_tokens=10, completion_tokens=5, total_tokens=15, reasoning_tokens=3,
    )
    assert usage.reasoning_tokens == 3


def test_reasoning_tokens_defaults_to_zero():
    from app.schemas.log_entry import UsagePayload
    usage = UsagePayload()
    assert usage.reasoning_tokens == 0
