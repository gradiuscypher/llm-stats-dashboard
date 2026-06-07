"""Tests for proxy module components."""

from app.models.log_entry import LogEntry
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

    # --- Reasoning accumulation tests ---

    def test_accumulates_reasoning_text(self):
        assembler = StreamAssembler(model="gpt-4o")
        assembler.feed({
            "choices": [{"delta": {"reasoning": "Let me think"}}],
        })
        assembler.feed({
            "choices": [{"delta": {"reasoning": " about this."}}],
        })
        assembler.feed({
            "choices": [{"delta": {"content": "Answer."}}],
        })
        result = assembler.assemble()
        msg = result["choices"][0]["message"]
        assert msg["reasoning"] == "Let me think about this."
        assert msg["content"] == "Answer."

    def test_accumulates_reasoning_details(self):
        assembler = StreamAssembler(model="gpt-4o")
        assembler.feed({
            "choices": [{
                "delta": {
                    "reasoning_details": [
                        {"index": 0, "type": "reasoning.text", "text": "Step 1"},
                    ]
                }
            }],
        })
        assembler.feed({
            "choices": [{
                "delta": {
                    "reasoning_details": [
                        {"index": 0, "type": "reasoning.text", "text": " done"},
                    ]
                }
            }],
        })
        result = assembler.assemble()
        msg = result["choices"][0]["message"]
        assert msg["reasoning_details"] is not None
        assert len(msg["reasoning_details"]) == 1
        assert msg["reasoning_details"][0]["text"] == "Step 1 done"
        assert msg["reasoning_details"][0]["type"] == "reasoning.text"

    def test_reasoning_details_encrypted_block(self):
        assembler = StreamAssembler(model="gpt-4o")
        assembler.feed({
            "choices": [{
                "delta": {
                    "reasoning_details": [
                        {"index": 0, "type": "reasoning.encrypted", "text": "base64blob=="},
                    ]
                }
            }],
        })
        result = assembler.assemble()
        msg = result["choices"][0]["message"]
        assert msg["reasoning_details"][0]["type"] == "reasoning.encrypted"
        assert msg["reasoning_details"][0]["text"] == "base64blob=="

    def test_reasoning_and_tool_calls_together(self):
        assembler = StreamAssembler(model="gpt-4o")
        assembler.feed({
            "choices": [{"delta": {"reasoning": "I need a tool."}}],
        })
        assembler.feed({
            "choices": [{
                "delta": {
                    "content": "Let me check.",
                    "tool_calls": [{
                        "index": 0,
                        "id": "call_x",
                        "function": {"name": "search", "arguments": '{"q":'},
                    }],
                }
            }],
        })
        assembler.feed({
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "function": {"arguments": '"hi"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        })
        result = assembler.assemble()
        msg = result["choices"][0]["message"]
        assert msg["reasoning"] == "I need a tool."
        assert msg["content"] == "Let me check."
        assert len(msg["tool_calls"]) == 1

    def test_reasoning_only_turn(self):
        """Reasoning without any final content should still assemble."""
        assembler = StreamAssembler(model="gpt-4o")
        assembler.feed({
            "choices": [{"delta": {"reasoning": "Just thinking."}}],
        })
        assembler.feed({
            "choices": [{"delta": {}, "finish_reason": "stop"}],
        })
        result = assembler.assemble()
        msg = result["choices"][0]["message"]
        assert msg["reasoning"] == "Just thinking."
        assert "content" not in msg

    def test_preserves_reasoning_tokens_in_usage(self):
        assembler = StreamAssembler(model="gpt-4o")
        assembler.feed({
            "choices": [{"delta": {"reasoning": "Hmm"}}],
        })
        assembler.feed({
            "choices": [{"delta": {"content": "Done"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 15,
                "total_tokens": 25,
                "completion_tokens_details": {"reasoning_tokens": 8},
            },
        })
        result = assembler.assemble()
        assert result["usage"]["completion_tokens_details"]["reasoning_tokens"] == 8


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

    def test_fallback_mints_unique_ids(self):
        """Without DB context, each call gets a fresh UUID — no merging."""
        body = {
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello"},
            ]
        }
        cid1 = derive_conversation_id(body, "lsd_abc")
        cid2 = derive_conversation_id(body, "lsd_abc")
        assert cid1 != cid2, "Each call without DB context must get a unique id"
        assert cid1.startswith("or-")
        assert cid2.startswith("or-")

    def test_different_keys_never_share_buckets(self):
        body = {
            "messages": [{"role": "user", "content": "hi"}],
        }
        cid_a = derive_conversation_id(body, "lsd_aaa")
        cid_b = derive_conversation_id(body, "lsd_bbb")
        assert cid_a != cid_b

    def test_empty_messages_fallback(self):
        cid = derive_conversation_id({"messages": []}, "lsd_abc")
        assert cid.startswith("or-")

    def test_with_db_prefix_chain_inherits_conversation(self, pg_engine):
        """Turn 2 extending turn 1's messages gets the same conversation_id."""
        import uuid as _uuid

        from sqlmodel import Session

        with Session(pg_engine) as db:
            from app.models.user import User
            from app.security.passwords import hash_password
            uid = _uuid.uuid4()
            db.add(User(id=uid, username="pc_user", password_hash=hash_password("x")))
            db.flush()

            from app.services.messages import intern_messages

            # Turn 1: [user msg]
            msgs1 = [{"role": "user", "content": "Hello"}]
            ids1 = intern_messages(msgs1, uid, db)

            entry1 = LogEntry(
                id=_uuid.uuid4(),
                user_id=uid,
                conversation_id="or-abc123",
                message_ids=ids1,
                provider="openrouter",
                model="gpt-4o",
                request={"params": {}},
                response={"message": {"role": "assistant", "content": "Hi"}},
            )
            db.add(entry1)
            db.flush()

            # Turn 2: [user msg, assistant msg, new user msg]
            msgs2 = [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "How are you?"},
            ]
            ids2 = intern_messages(msgs2, uid, db)

            cid = derive_conversation_id(
                {"messages": msgs2},
                "lsd_abc",
                message_ids=ids2,
                user_id=uid,
                db=db,
            )
            assert cid == "or-abc123", "Turn 2 must inherit turn 1's conversation_id"

    def test_with_db_equal_length_not_a_prefix(self, pg_engine):
        """Retry of first message (same length) does NOT chain — new conversation."""
        import uuid as _uuid

        from sqlmodel import Session

        with Session(pg_engine) as db:
            from app.models.user import User
            from app.security.passwords import hash_password
            uid = _uuid.uuid4()
            db.add(User(id=uid, username="pc_user2", password_hash=hash_password("x")))
            db.flush()

            from app.services.messages import intern_messages

            msgs1 = [{"role": "user", "content": "Hello"}]
            ids1 = intern_messages(msgs1, uid, db)

            entry1 = LogEntry(
                id=_uuid.uuid4(),
                user_id=uid,
                conversation_id="or-abc123",
                message_ids=ids1,
                provider="openrouter",
                model="gpt-4o",
                request={"params": {}},
                response={"message": {"role": "assistant", "content": "Hi"}},
            )
            db.add(entry1)
            db.flush()

            # Same message — equal length, not a *proper* prefix
            ids2 = intern_messages(msgs1, uid, db)
            cid = derive_conversation_id(
                {"messages": msgs1},
                "lsd_abc",
                message_ids=ids2,
                user_id=uid,
                db=db,
            )
            assert cid != "or-abc123", (
                "Equal-length retry must NOT chain — it's a new conversation"
            )
            assert cid.startswith("or-")


# ---------------------------------------------------------------------------
# OpenRouter mapping tests
# ---------------------------------------------------------------------------

class TestMapToLogEntry:
    _USER_ID = "00000000-0000-0000-0000-000000000001"
    _KEY_ID = "00000000-0000-0000-0000-000000000002"

    @staticmethod
    def _make_user_key():
        from app.models.api_key import ApiKey
        from app.models.user import User
        user = User(
            id=TestMapToLogEntry._USER_ID, username="test",
            email="", password_hash="", is_active=True,
        )
        api_key = ApiKey(
            id=TestMapToLogEntry._KEY_ID, user_id=user.id, name="test",
            prefix="lsd_abc", key_hash="", scopes=["proxy:use"],
        )
        return user, api_key

    def test_basic_mapping(self):
        user, api_key = self._make_user_key()

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
            "usage": {
                "prompt_tokens": 20, "completion_tokens": 5,
                "total_tokens": 25, "cost": 0.0001,
            },
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
        user, api_key = self._make_user_key()

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

    def test_developer_role_accepted_verbatim(self):
        user, api_key = self._make_user_key()

        ctx = ProxyContext(
            user=user,
            api_key=api_key,
            request_body={
                "model": "deepseek/deepseek-v4-flash",
                "messages": [
                    {"role": "developer", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Hello"},
                ],
            },
            request_headers={},
            model="deepseek/deepseek-v4-flash",
            is_stream=False,
        )
        upstream = {
            "choices": [{
                "message": {"role": "assistant", "content": "Hi!"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        entry = map_to_log_entry(ctx, upstream)
        assert entry.status == "ok"
        assert entry.request.messages[0].role == "developer"
        assert entry.request.messages[0].content == "You are a helpful assistant."

    def test_unrecognized_message_falls_back_to_plaintext(self):
        user, api_key = self._make_user_key()

        ctx = ProxyContext(
            user=user,
            api_key=api_key,
            request_body={
                "model": "gpt-4o",
                "messages": [
                    {"role": "user", "content":
                        {"type": "unsupported_future_type", "data": object()}},
                ],
            },
            request_headers={},
            model="gpt-4o",
            is_stream=False,
        )
        upstream = {
            "choices": [{
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }],
            "usage": {},
        }

        entry = map_to_log_entry(ctx, upstream)
        assert entry.status == "ok"
        # Should have fallen back to a plaintext JSON string
        assert isinstance(entry.request.messages[0].content, str)
        assert "unsupported_future_type" in entry.request.messages[0].content

    # --- Reasoning tests ---

    def test_maps_reasoning_in_response(self):
        user, api_key = self._make_user_key()

        ctx = ProxyContext(
            user=user,
            api_key=api_key,
            request_body={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Explain relativity."}],
            },
            request_headers={},
            model="gpt-4o",
            is_stream=False,
        )
        upstream = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Here you go.",
                    "reasoning": "Let me break this down step by step.",
                    "reasoning_details": [
                        {"type": "reasoning.text", "text": "Step 1: define"},
                        {"type": "reasoning.text", "text": "Step 2: apply"},
                    ],
                },
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
                "completion_tokens_details": {"reasoning_tokens": 15},
            },
        }

        entry = map_to_log_entry(ctx, upstream)
        assert entry.status == "ok"
        assert entry.response.message.reasoning == "Let me break this down step by step."
        assert entry.response.message.reasoning_details is not None
        assert len(entry.response.message.reasoning_details) == 2
        assert entry.response.message.reasoning_details[0]["type"] == "reasoning.text"
        assert entry.usage.reasoning_tokens == 15

    def test_maps_reasoning_in_request_messages(self):
        user, api_key = self._make_user_key()

        ctx = ProxyContext(
            user=user,
            api_key=api_key,
            request_body={
                "model": "gpt-4o",
                "messages": [
                    {"role": "user", "content": "Keep going."},
                    {
                        "role": "assistant",
                        "content": "Earlier answer.",
                        "reasoning": "Earlier thinking.",
                        "reasoning_details": [{"type": "reasoning.text", "text": "prior"}],
                    },
                ],
            },
            request_headers={},
            model="gpt-4o",
            is_stream=False,
        )
        upstream = {
            "choices": [{
                "message": {"role": "assistant", "content": "More."},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        }

        entry = map_to_log_entry(ctx, upstream)
        # Request assistant message retains reasoning
        req_asst = entry.request.messages[1]
        assert req_asst.role == "assistant"
        assert req_asst.reasoning == "Earlier thinking."
        assert req_asst.reasoning_details is not None
        assert req_asst.reasoning_details[0]["type"] == "reasoning.text"

    def test_reasoning_tokens_defaults_to_zero(self):
        user, api_key = self._make_user_key()

        ctx = ProxyContext(
            user=user,
            api_key=api_key,
            request_body={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hi"}],
            },
            request_headers={},
            model="gpt-4o",
            is_stream=False,
        )
        upstream = {
            "choices": [{
                "message": {"role": "assistant", "content": "Hello"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        }

        entry = map_to_log_entry(ctx, upstream)
        assert entry.usage.reasoning_tokens == 0
