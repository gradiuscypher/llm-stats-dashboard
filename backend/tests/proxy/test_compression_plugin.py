"""Tests for CompressionPlugin."""

from unittest.mock import MagicMock, patch

import pytest

from app.proxy.interceptor import RequestInterceptor, TransformContext
from app.proxy.plugins.compression import CompressionPlugin


class TestCompressionPlugin:
    """Unit tests for the compression plugin — Headroom is mocked."""

    def test_pass_through_empty_messages(self):
        """Empty message list returns unchanged."""
        plugin = CompressionPlugin()
        ctx = TransformContext(model="gpt-4o", user_id="user-1")
        result = plugin.transform_request([], ctx)
        assert result == []
        assert "compression" not in ctx.request_metadata

    def test_returns_compressed_messages(self):
        """Plugin returns compressed messages from Headroom."""
        original = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
        ]
        compressed = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello!"},
        ]

        mock_result = MagicMock()
        mock_result.messages = compressed
        mock_result.tokens_before = 20
        mock_result.tokens_after = 15
        mock_result.tokens_saved = 5
        mock_result.compression_ratio = 0.25
        mock_result.transforms_applied = ["CacheAligner"]

        # Patch headroom.compress before it's lazily imported inside the plugin.
        with patch("headroom.compress", return_value=mock_result):
            plugin = CompressionPlugin()
            ctx = TransformContext(model="gpt-4o", user_id="user-1")
            result = plugin.transform_request(original, ctx)

        assert result == compressed
        assert ctx.request_metadata["compression"] == {
            "tokens_before": 20,
            "tokens_after": 15,
            "tokens_saved": 5,
            "compression_ratio": 0.25,
            "transforms_applied": ["CacheAligner"],
        }

    def test_metrics_written_to_request_metadata(self):
        """Compression metrics are stored in ctx.request_metadata."""
        original = [{"role": "user", "content": "x" * 1000}]
        compressed = [{"role": "user", "content": "x" * 100}]

        mock_result = MagicMock()
        mock_result.messages = compressed
        mock_result.tokens_before = 500
        mock_result.tokens_after = 200
        mock_result.tokens_saved = 300
        mock_result.compression_ratio = 0.6
        mock_result.transforms_applied = ["Kompress", "CacheAligner"]

        with patch("headroom.compress", return_value=mock_result):
            plugin = CompressionPlugin()
            ctx = TransformContext(model="gpt-4o", user_id="user-1")
            plugin.transform_request(original, ctx)

        comp = ctx.request_metadata.get("compression")
        assert comp is not None
        assert comp["tokens_before"] == 500
        assert comp["tokens_after"] == 200
        assert comp["tokens_saved"] == 300
        assert comp["compression_ratio"] == 0.6
        assert comp["transforms_applied"] == ["Kompress", "CacheAligner"]

    def test_fail_open_on_error(self):
        """If Headroom raises, original messages are returned unchanged."""
        original = [{"role": "user", "content": "Hello!"}]

        with patch(
            "headroom.compress",
            side_effect=RuntimeError("ONNX inference failed"),
        ):
            plugin = CompressionPlugin()
            ctx = TransformContext(model="gpt-4o", user_id="user-1")
            result = plugin.transform_request(original, ctx)

        assert result == original
        assert "compression" not in ctx.request_metadata

    def test_fail_open_when_compress_raises(self):
        """If compress() raises any exception, original messages are returned."""
        original = [{"role": "user", "content": "Hello!"}]

        # Test with a generic exception (beyond the RuntimeError tested above).
        with patch(
            "headroom.compress",
            side_effect=ValueError("unexpected message format"),
        ):
            plugin = CompressionPlugin()
            ctx = TransformContext(model="gpt-4o", user_id="user-1")
            result = plugin.transform_request(original, ctx)

        assert result == original
        assert "compression" not in ctx.request_metadata

    def test_integration_with_interceptor(self):
        """Compression plugin works within the interceptor pipeline."""
        original = [
            {"role": "system", "content": "You are a helpful assistant." * 10},
            {"role": "user", "content": "Hello!"},
        ]
        compressed = [
            {"role": "system", "content": "compressed system prompt"},
            {"role": "user", "content": "Hello!"},
        ]

        mock_result = MagicMock()
        mock_result.messages = compressed
        mock_result.tokens_before = 100
        mock_result.tokens_after = 50
        mock_result.tokens_saved = 50
        mock_result.compression_ratio = 0.5
        mock_result.transforms_applied = ["Kompress"]

        with patch("headroom.compress", return_value=mock_result):
            plugin = CompressionPlugin()
            tctx = TransformContext(
                model="gpt-4o", user_id="user-1", request_metadata={}
            )
            interceptor = RequestInterceptor([plugin])
            result = interceptor.run(original, tctx)

        assert result.final_messages == compressed
        assert len(result.diffs) > 0  # diffs should be generated for modified messages

        # Check that compression metrics are on the transform context.
        assert tctx.request_metadata.get("compression") is not None


class TestCompressionPluginConfig:
    """Tests for config-driven CompressConfig construction."""

    def test_config_uses_settings(self):
        """CompressConfig is built from app settings."""
        import os

        from app.config import Settings

        # Use explicit settings to verify field mapping.
        s = Settings(
            compression_compress_user_messages=True,
            compression_compress_system_messages=False,
            compression_protect_recent=2,
            compression_target_ratio=0.5,
            compression_min_tokens=500,
            compression_kompress_model="disabled",
            headroom_telemetry=False,
        )

        assert s.compression_compress_user_messages is True
        assert s.compression_compress_system_messages is False
        assert s.compression_protect_recent == 2
        assert s.compression_target_ratio == 0.5
        assert s.compression_min_tokens == 500
        assert s.compression_kompress_model == "disabled"

        # Telemetry should be disabled by default.
        assert os.environ.get("HEADROOM_TELEMETRY") == "off"
        assert s.headroom_telemetry is False
