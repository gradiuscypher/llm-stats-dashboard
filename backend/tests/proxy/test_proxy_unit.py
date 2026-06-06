"""Tests for proxy module components."""

from app.proxy.assembler import StreamAssembler
from app.proxy.context import ProxyContext
from app.services.openrouter_map import derive_conversation_id, map_to_log_entry

# ---------------------------------------------------------------------------
# StreamAssembler tests
# ---------------------------------------------------------------------------

class TestStreamAssembler:
    def test_accumulates_text_content(self):
        assembler = StreamAssembler(model="gpt-4o")
        assembler.feed({
            "choices": [{"delta": {"content": "Hello"}}],
        })
        assembler.feed({
            "choices": [{"delta": {"content": " world"}}],
        })
        assembler.feed({
            "choices": [{"delta": {}, "finish_reason": "stop"}],
        })
        result = assembler.assemble()
        assert result["choices"][0]["message"]["content"] == "Hello world"
        assert assembler.finish_reason == "stop"

    def test_accumulates_tool_calls(self):
        assembler = StreamAssembler(model="gpt-4o")
        assembler.feed({
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": "call_123",
                        "function": {"name": "get_weather", "arguments": '{"loc'},
                    }]
                }
            }],
        })
        assembler.feed({
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "function": {"arguments": 'ation": "NYC"}'},
                    }]
                },
                "finish_reason": "tool_calls",
            }],
        })
        result = assembler.assemble()
        tool_calls = result["choices"][0]["message"]["tool_calls"]
        assert len(tool_calls) == 1
        assert tool_calls[0]["id"] == "call_123"
        assert tool_calls[0]["function"]["name"] == "get_weather"
        assert tool_calls[0]["function"]["arguments"] == '{"location": "NYC"}'

    def test_captures_usage(self):
        assembler = StreamAssembler(model="gpt-4o")
        assembler.feed({
            "choices": [{"delta": {"content": "Hi"}}],
        })
        assembler.feed({
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
        result = assembler.assemble()
        assert result["usage"]["prompt_tokens"] == 10
        assert result["usage"]["completion_tokens"] == 5
        assert assembler.usage["total_tokens"] == 15

    def test_empty_stream_assembles(self):
        assembler = StreamAssembler(model="gpt-4o")
        result = assembler.assemble()
        assert result["object"] == "chat.completion"
        assert result["model"] == "gpt-4o"
        assert result["choices"][0]["message"]["role"] == "assistant"


# ---------------------------------------------------------------------------
# Conversation ID derivation tests
# ---------------------------------------------------------------------------

class TestDeriveConversationId:
    def test_explicit_header_wins(self):
        cid = derive_conversation_id(
            {"messages": [{"role": "user", "content": "hi"}]},
            "lsd_abc",
            explicit="my-custom-id",
        )
        assert cid == "my-custom-id"

    def test_user_field(self):
        cid = derive_conversation_id(
            {"user": "bob", "messages": [{"role": "user", "content": "hi"}]},
            "lsd_abc",
        )
        assert cid == "or-user-bob"

    def test_derived_is_deterministic(self):
        body = {
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello"},
            ]
        }
        cid1 = derive_conversation_id(body, "lsd_abc")
        cid2 = derive_conversation_id(body, "lsd_abc")
        assert cid1 == cid2
        assert cid1.startswith("or-derived-")

    def test_different_keys_never_share_buckets(self):
        body = {
            "messages": [{"role": "user", "content": "hi"}],
        }
        cid_a = derive_conversation_id(body, "lsd_aaa")
        cid_b = derive_conversation_id(body, "lsd_bbb")
        assert cid_a != cid_b

    def test_empty_messages_fallback(self):
        cid = derive_conversation_id({"messages": []}, "lsd_abc")
        assert cid.startswith("or-derived-")


# ---------------------------------------------------------------------------
# OpenRouter mapping tests
# ---------------------------------------------------------------------------

class TestMapToLogEntry:
    def test_basic_mapping(self):
        from app.models.api_key import ApiKey
        from app.models.user import User
        user = User(id="00000000-0000-0000-0000-000000000001", username="test", email="", password_hash="", is_active=True)
        api_key = ApiKey(id="00000000-0000-0000-0000-000000000002", user_id=user.id, name="test", prefix="lsd_abc", key_hash="", scopes=["proxy:use"])

        ctx = ProxyContext(
            user=user,
            api_key=api_key,
            request_body={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "Be helpful."},
                    {"role": "user", "content": "What is 2+2?"},
                ],
                "temperature": 0.7,
            },
            request_headers={},
            model="gpt-4o",
            is_stream=False,
        )
        upstream = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "model": "gpt-4o",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "4"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25, "cost": 0.0001},
        }

        entry = map_to_log_entry(ctx, upstream)
        assert entry.provider == "openrouter"
        assert entry.model == "gpt-4o"
        assert entry.request.messages[0].role == "system"
        assert entry.request.messages[1].role == "user"
        assert entry.request.params == {"temperature": 0.7}
        assert entry.response.message.role == "assistant"
        assert entry.response.message.content == "4"
        assert entry.response.finish_reason == "stop"
        assert entry.usage.prompt_tokens == 20
        assert entry.usage.completion_tokens == 5
        assert entry.cost.total == 0.0001
        assert entry.status == "ok"
        assert entry.conversation_id is not None
        assert entry.latency_ms is not None

    def test_mapping_with_tool_calls(self):
        from app.models.api_key import ApiKey
        from app.models.user import User
        user = User(id="00000000-0000-0000-0000-000000000001", username="test", email="", password_hash="", is_active=True)
        api_key = ApiKey(id="00000000-0000-0000-0000-000000000002", user_id=user.id, name="test", prefix="lsd_abc", key_hash="", scopes=["proxy:use"])

        ctx = ProxyContext(
            user=user,
            api_key=api_key,
            request_body={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Weather in NYC?"}],
            },
            request_headers={},
            model="gpt-4o",
            is_stream=False,
        )
        upstream = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc",
                        "function": {"name": "get_weather", "arguments": '{"location": "NYC"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        entry = map_to_log_entry(ctx, upstream)
        assert len(entry.tool_calls) == 1
        assert entry.tool_calls[0].name == "get_weather"
        assert entry.tool_calls[0].id == "call_abc"
