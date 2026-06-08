"""Tests for proxy module components."""

import uuid as _uuid

from app.models.log_entry import LogEntry
from app.proxy.assembler import StreamAssembler
from app.proxy.context import ProxyContext
from app.services.openrouter_map import map_to_log_entry

# ---------------------------------------------------------------------------
# StreamAssembler tests
# ---------------------------------------------------------------------------


class TestStreamAssembler:
    def test_accumulates_text_content(self):
        assembler = StreamAssembler(model="gpt-4o")
        assembler.feed(
            {
                "choices": [{"delta": {"content": "Hello"}}],
            }
        )
        assembler.feed(
            {
                "choices": [{"delta": {"content": " world"}}],
            }
        )
        assembler.feed(
            {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
            }
        )
        result = assembler.assemble()
        assert result["choices"][0]["message"]["content"] == "Hello world"
        assert assembler.finish_reason == "stop"

    def test_accumulates_tool_calls(self):
        assembler = StreamAssembler(model="gpt-4o")
        assembler.feed(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_123",
                                    "function": {"name": "get_weather", "arguments": '{"loc'},
                                }
                            ]
                        }
                    }
                ],
            }
        )
        assembler.feed(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": 'ation": "NYC"}'},
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
        )
        result = assembler.assemble()
        tool_calls = result["choices"][0]["message"]["tool_calls"]
        assert len(tool_calls) == 1
        assert tool_calls[0]["id"] == "call_123"
        assert tool_calls[0]["function"]["name"] == "get_weather"
        assert tool_calls[0]["function"]["arguments"] == '{"location": "NYC"}'

    def test_captures_usage(self):
        assembler = StreamAssembler(model="gpt-4o")
        assembler.feed(
            {
                "choices": [{"delta": {"content": "Hi"}}],
            }
        )
        assembler.feed(
            {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
        )
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
        assembler.feed(
            {
                "choices": [{"delta": {"reasoning": "Let me think"}}],
            }
        )
        assembler.feed(
            {
                "choices": [{"delta": {"reasoning": " about this."}}],
            }
        )
        assembler.feed(
            {
                "choices": [{"delta": {"content": "Answer."}}],
            }
        )
        result = assembler.assemble()
        msg = result["choices"][0]["message"]
        assert msg["reasoning"] == "Let me think about this."
        assert msg["content"] == "Answer."

    def test_accumulates_reasoning_details(self):
        assembler = StreamAssembler(model="gpt-4o")
        assembler.feed(
            {
                "choices": [
                    {
                        "delta": {
                            "reasoning_details": [
                                {"index": 0, "type": "reasoning.text", "text": "Step 1"},
                            ]
                        }
                    }
                ],
            }
        )
        assembler.feed(
            {
                "choices": [
                    {
                        "delta": {
                            "reasoning_details": [
                                {"index": 0, "type": "reasoning.text", "text": " done"},
                            ]
                        }
                    }
                ],
            }
        )
        result = assembler.assemble()
        msg = result["choices"][0]["message"]
        assert msg["reasoning_details"] is not None
        assert len(msg["reasoning_details"]) == 1
        assert msg["reasoning_details"][0]["text"] == "Step 1 done"
        assert msg["reasoning_details"][0]["type"] == "reasoning.text"

    def test_reasoning_details_encrypted_block(self):
        assembler = StreamAssembler(model="gpt-4o")
        assembler.feed(
            {
                "choices": [
                    {
                        "delta": {
                            "reasoning_details": [
                                {"index": 0, "type": "reasoning.encrypted", "text": "base64blob=="},
                            ]
                        }
                    }
                ],
            }
        )
        result = assembler.assemble()
        msg = result["choices"][0]["message"]
        assert msg["reasoning_details"][0]["type"] == "reasoning.encrypted"
        assert msg["reasoning_details"][0]["text"] == "base64blob=="

    def test_reasoning_and_tool_calls_together(self):
        assembler = StreamAssembler(model="gpt-4o")
        assembler.feed(
            {
                "choices": [{"delta": {"reasoning": "I need a tool."}}],
            }
        )
        assembler.feed(
            {
                "choices": [
                    {
                        "delta": {
                            "content": "Let me check.",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_x",
                                    "function": {"name": "search", "arguments": '{"q":'},
                                }
                            ],
                        }
                    }
                ],
            }
        )
        assembler.feed(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": '"hi"}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
        )
        result = assembler.assemble()
        msg = result["choices"][0]["message"]
        assert msg["reasoning"] == "I need a tool."
        assert msg["content"] == "Let me check."
        assert len(msg["tool_calls"]) == 1

    def test_reasoning_only_turn(self):
        """Reasoning without any final content should still assemble."""
        assembler = StreamAssembler(model="gpt-4o")
        assembler.feed(
            {
                "choices": [{"delta": {"reasoning": "Just thinking."}}],
            }
        )
        assembler.feed(
            {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
            }
        )
        result = assembler.assemble()
        msg = result["choices"][0]["message"]
        assert msg["reasoning"] == "Just thinking."
        assert "content" not in msg

    def test_preserves_reasoning_tokens_in_usage(self):
        assembler = StreamAssembler(model="gpt-4o")
        assembler.feed(
            {
                "choices": [{"delta": {"reasoning": "Hmm"}}],
            }
        )
        assembler.feed(
            {
                "choices": [{"delta": {"content": "Done"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 15,
                    "total_tokens": 25,
                    "completion_tokens_details": {"reasoning_tokens": 8},
                },
            }
        )
        result = assembler.assemble()
        assert result["usage"]["completion_tokens_details"]["reasoning_tokens"] == 8


# ---------------------------------------------------------------------------
# Conversation ID derivation tests
# ---------------------------------------------------------------------------


class TestConversationIdentity:
    """Conversation identity inference via chain-key prefix matching."""

    # -- explicit / user-field precedence (unchanged) --

    def test_explicit_header_wins(self):
        from app.services.conversation_identity import infer_conversation_id

        cid, ck, _ = infer_conversation_id(
            [{"role": "user", "content": "hi"}],
            _uuid.uuid4(),
            None,  # type: ignore[arg-type]
            explicit="my-custom-id",
        )
        assert cid == "my-custom-id"
        assert ck.chain_key is not None

    def test_user_field(self):
        from app.services.conversation_identity import infer_conversation_id

        cid, ck, _ = infer_conversation_id(
            [{"role": "user", "content": "hi"}],
            _uuid.uuid4(),
            None,  # type: ignore[arg-type]
            user_field="bob",
        )
        assert cid == "or-user-bob"
        assert ck.chain_key is not None

    # -- chain-key prefix matching --

    def test_continuing_turn_chains(self, pg_engine):
        """Turn 2 chains to turn 1 via chain_key prefix."""
        from sqlmodel import Session

        from app.services.conversation_identity import (
            compute_chain_keys,
            infer_conversation_id,
        )

        with Session(pg_engine) as db:
            from app.models.user import User
            from app.security.passwords import hash_password

            uid = _uuid.uuid4()
            db.add(User(id=uid, username="ci_user", password_hash=hash_password("x")))
            db.flush()

            # Turn 1: store entry with its chain_key.
            t1 = [
                {"role": "system", "content": "Be helpful."},
                {"role": "user", "content": "Hello"},
            ]
            ck1 = compute_chain_keys(t1)
            entry1 = LogEntry(
                id=_uuid.uuid4(),
                user_id=uid,
                conversation_id="or-chain-test",
                chain_key=ck1.chain_key,
                chain_prefix_key=ck1.chain_prefix_key,
                provider="openrouter",
                model="gpt-4o",
                request={"params": {}},
                response={"message": {"role": "assistant", "content": "Hi"}},
                message_ids=[],
            )
            db.add(entry1)
            db.flush()

            # Turn 2: system + user1 + assistant1 + user2.
            t2 = [
                {"role": "system", "content": "Be helpful."},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "How are you?"},
            ]
            cid2, ck2, matched = infer_conversation_id(t2, uid, db)
            assert cid2 == "or-chain-test", "Turn 2 must chain to turn 1"
            assert matched == entry1.id

    def test_normalization_ignores_reasoning_extras(self, pg_engine):
        """Stored asst msg has reasoning, resent asst msg is {role,content} only
        — same turn_key, so turn 2 chains.  This directly reproduces the
        regression caused by the prior response-interning fix."""
        from sqlmodel import Session

        from app.services.conversation_identity import compute_chain_keys, infer_conversation_id

        with Session(pg_engine) as db:
            from app.models.user import User
            from app.security.passwords import hash_password

            uid = _uuid.uuid4()
            db.add(User(id=uid, username="ci_norm", password_hash=hash_password("x")))
            db.flush()

            # Turn 1: assistant reply includes reasoning + provider extras.
            t1 = [
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "What is 2+2?"},
                {
                    "role": "assistant",
                    "content": "4",
                    "reasoning": "Let me add: 2+2=4.",
                    "reasoning_details": [{"type": "reasoning.text", "text": "2+2=4"}],
                    "provider_extra": "ignored field",
                },
            ]
            ck1 = compute_chain_keys(t1)
            db.add(
                LogEntry(
                    id=_uuid.uuid4(),
                    user_id=uid,
                    conversation_id="or-norm-test",
                    chain_key=ck1.chain_key,
                    chain_prefix_key=ck1.chain_prefix_key,
                    provider="openrouter",
                    model="gpt-4o",
                    request={"params": {}},
                    response={"message": {"role": "assistant", "content": "4"}},
                    message_ids=[],
                )
            )
            db.flush()

            # Turn 2: client resends stripped assistant (no reasoning, no extras).
            t2 = [
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "What is 2+2?"},
                {"role": "assistant", "content": "4"},  # stripped
                {"role": "user", "content": "Why?"},
            ]
            cid2, _, _ = infer_conversation_id(t2, uid, db)
            assert cid2 == "or-norm-test", (
                "Stripped assistant reply must still chain — normalization "
                "drops reasoning/extras so turn keys match"
            )

    def test_tool_call_normalization(self, pg_engine):
        """Tool-call arguments as JSON-string vs dict produce the same turn_key."""
        from sqlmodel import Session

        from app.services.conversation_identity import turn_key

        with Session(pg_engine) as db:
            from app.models.user import User
            from app.security.passwords import hash_password

            uid = _uuid.uuid4()
            db.add(User(id=uid, username="ci_tool", password_hash=hash_password("x")))
            db.flush()

            tk_string = turn_key(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "function": {"name": "search", "arguments": '{"q":"hello"}'},
                        }
                    ],
                }
            )
            tk_dict = turn_key(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "function": {"name": "search", "arguments": {"q": "hello"}},
                        }
                    ],
                }
            )
            assert tk_string == tk_dict, (
                "Tool-call args as JSON-string vs dict must produce same turn_key"
            )

    def test_unrelated_thread_no_match(self, pg_engine):
        """Different first message (system prompt) → no match."""
        from sqlmodel import Session

        from app.services.conversation_identity import compute_chain_keys, infer_conversation_id

        with Session(pg_engine) as db:
            from app.models.user import User
            from app.security.passwords import hash_password

            uid = _uuid.uuid4()
            db.add(User(id=uid, username="ci_unrel", password_hash=hash_password("x")))
            db.flush()

            t1 = [
                {"role": "system", "content": "You are a pirate."},
                {"role": "user", "content": "Ahoy"},
            ]
            ck1 = compute_chain_keys(t1)
            db.add(
                LogEntry(
                    id=_uuid.uuid4(),
                    user_id=uid,
                    conversation_id="or-pirate",
                    chain_key=ck1.chain_key,
                    chain_prefix_key=ck1.chain_prefix_key,
                    provider="openrouter",
                    model="gpt-4o",
                    request={"params": {}},
                    response={"message": {"role": "assistant", "content": "Arr!"}},
                    message_ids=[],
                )
            )
            db.flush()

            t2 = [
                {"role": "system", "content": "You are helpful."},  # different
                {"role": "user", "content": "Hello"},
            ]
            cid2, _, matched = infer_conversation_id(t2, uid, db)
            assert cid2 != "or-pirate", "Unrelated thread must NOT match"
            assert matched is None

    def test_retry_same_history_no_match(self, pg_engine):
        """A retry of the exact same request history starts a new conversation,
        not a continuation of the previous turn."""
        from sqlmodel import Session

        from app.services.conversation_identity import compute_chain_keys, infer_conversation_id

        with Session(pg_engine) as db:
            from app.models.user import User
            from app.security.passwords import hash_password

            uid = _uuid.uuid4()
            db.add(User(id=uid, username="ci_retry", password_hash=hash_password("x")))
            db.flush()

            t1 = [
                {"role": "user", "content": "Hello"},
            ]
            ck1 = compute_chain_keys(t1)
            db.add(
                LogEntry(
                    id=_uuid.uuid4(),
                    user_id=uid,
                    conversation_id="or-first",
                    chain_key=ck1.chain_key,
                    chain_prefix_key=ck1.chain_prefix_key,
                    provider="openrouter",
                    model="gpt-4o",
                    request={"params": {}},
                    response={"message": {"role": "assistant", "content": "Hi"}},
                    message_ids=[],
                )
            )
            db.flush()

            # Same exact request — proper prefix requires len(candidate) < len(turn_keys)
            cid2, _, matched = infer_conversation_id(t1, uid, db)
            assert cid2 != "or-first", "Retry of identical history must NOT chain"
            assert matched is None

    def test_new_thread_no_match(self):
        """Brand-new thread with no prior entries returns a fresh id."""
        from app.services.conversation_identity import infer_conversation_id

        cid, ck, matched = infer_conversation_id(
            [{"role": "user", "content": "Hello"}],
            _uuid.uuid4(),
            None,  # type: ignore[arg-type]
        )
        assert cid.startswith("or-")
        assert matched is None
        assert ck.chain_key is not None


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
            id=TestMapToLogEntry._USER_ID,
            username="test",
            email="",
            password_hash="",
            is_active=True,
        )
        api_key = ApiKey(
            id=TestMapToLogEntry._KEY_ID,
            user_id=user.id,
            name="test",
            prefix="lsd_abc",
            key_hash="",
            scopes=["proxy:use"],
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
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "4"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 5,
                "total_tokens": 25,
                "cost": 0.0001,
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
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_abc",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": '{"location": "NYC"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        entry = map_to_log_entry(ctx, upstream)
        assert len(entry.tool_calls) == 1
        assert entry.tool_calls[0].name == "get_weather"
        assert entry.tool_calls[0].id == "call_abc"

    def test_logs_original_request_not_mutated(self):
        """Logged messages use the ORIGINAL (pre-interceptor) messages.

        When ctx.original_request_messages is set (the production path),
        map_to_log_entry interns the original content — not the
        post-transform messages sent upstream.
        """
        user, api_key = self._make_user_key()
        # Final messages (post-interceptor with word_count applied).
        final_body = {
            "model": "gpt-4o",
            "messages": [
                {"role": "user", "content": "Hello world\n\n[word_count: 2]"}
            ],
        }
        original_messages = [
            {"role": "user", "content": "Hello world"}
        ]

        ctx = ProxyContext(
            user=user,
            api_key=api_key,
            request_body=final_body,
            request_headers={},
            model="gpt-4o",
            is_stream=False,
            original_request_messages=original_messages,
        )
        upstream = {
            "id": "chatcmpl-wc",
            "choices": [
                {"message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }

        entry = map_to_log_entry(ctx, upstream)
        # The logged request content is the ORIGINAL (pre-interceptor) content.
        assert "Hello world" in entry.request.messages[0].content
        assert "[word_count: 2]" not in entry.request.messages[0].content

    def test_falls_back_to_request_body_when_snapshot_is_none(self):
        """When original_request_messages is None, fall back to request_body."""
        user, api_key = self._make_user_key()
        final_body = {
            "model": "gpt-4o",
            "messages": [
                {"role": "user", "content": "Fallback message"}
            ],
        }

        ctx = ProxyContext(
            user=user,
            api_key=api_key,
            request_body=final_body,
            request_headers={},
            model="gpt-4o",
            is_stream=False,
            original_request_messages=None,
        )
        upstream = {
            "id": "chatcmpl-fb2",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Fallback response"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

        entry = map_to_log_entry(ctx, upstream)
        assert entry.request.messages[0].content == "Fallback message"
        assert entry.response.message.content == "Fallback response"

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
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Hi!"},
                    "finish_reason": "stop",
                }
            ],
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
                    {
                        "role": "user",
                        "content": {"type": "unsupported_future_type", "data": object()},
                    },
                ],
            },
            request_headers={},
            model="gpt-4o",
            is_stream=False,
        )
        upstream = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
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
            "choices": [
                {
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
                }
            ],
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
            "choices": [
                {
                    "message": {"role": "assistant", "content": "More."},
                    "finish_reason": "stop",
                }
            ],
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
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        }

        entry = map_to_log_entry(ctx, upstream)
        assert entry.usage.reasoning_tokens == 0
