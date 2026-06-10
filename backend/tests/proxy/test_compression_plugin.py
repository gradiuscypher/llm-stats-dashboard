"""Tests for CompressionPlugin (pipeline-based implementation)."""

from unittest.mock import MagicMock, patch

import pytest

from app.proxy.interceptor import RequestInterceptor, TransformContext
from app.proxy.plugins.compression import CompressionPlugin


@pytest.fixture(autouse=True)
def reset_pipeline_singleton():
    """Reset the module-level pipeline singleton between tests.

    This ensures each test builds its own mock pipeline.
    """
    import app.proxy.plugins.compression as mod

    mod._pipeline = None


class FakeTransformResult:
    """Fake TransformResult matching Headroom's shape."""

    def __init__(self, messages, tokens_before, tokens_after, transforms_applied):
        self.messages = messages
        self.tokens_before = tokens_before
        self.tokens_after = tokens_after
        self.transforms_applied = transforms_applied


class TestCompressionPluginPipeline:
    """Unit tests for the pipeline-based compression plugin."""

    def test_pass_through_empty_messages(self):
        """Empty message list returns unchanged."""
        plugin = CompressionPlugin()
        ctx = TransformContext(model="gpt-4o", user_id="user-1")
        result = plugin.transform_request([], ctx)
        assert result == []
        assert "compression" not in ctx.request_metadata

    def test_returns_compressed_messages(self):
        """Plugin returns compressed messages from pipeline."""
        original = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
        ]
        compressed = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello!"},
        ]

        fake_result = FakeTransformResult(
            messages=compressed,
            tokens_before=20,
            tokens_after=15,
            transforms_applied=["router:tool_result:text"],
        )

        mock_pipeline = MagicMock()
        mock_pipeline.apply.return_value = fake_result

        with patch(
            "app.proxy.plugins.compression._get_pipeline",
            return_value=mock_pipeline,
        ):
            plugin = CompressionPlugin()
            ctx = TransformContext(model="gpt-4o", user_id="user-1")
            result = plugin.transform_request(original, ctx)

        assert result == compressed
        comp = ctx.request_metadata["compression"]
        assert comp["tokens_before"] == 20
        assert comp["tokens_after"] == 15
        assert comp["tokens_saved"] == 5
        assert comp["compression_ratio"] == 0.25
        assert comp["transforms_applied"] == ["router:tool_result:text"]

    def test_metrics_written_to_request_metadata(self):
        """Compression metrics are stored in ctx.request_metadata."""
        original = [{"role": "user", "content": "x" * 1000}]
        compressed = [{"role": "user", "content": "x" * 100}]

        fake_result = FakeTransformResult(
            messages=compressed,
            tokens_before=500,
            tokens_after=200,
            transforms_applied=["Kompress", "CacheAligner"],
        )

        mock_pipeline = MagicMock()
        mock_pipeline.apply.return_value = fake_result

        with patch(
            "app.proxy.plugins.compression._get_pipeline",
            return_value=mock_pipeline,
        ):
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
        """If pipeline.apply() raises, original messages are returned unchanged."""
        original = [{"role": "user", "content": "Hello!"}]

        mock_pipeline = MagicMock()
        mock_pipeline.apply.side_effect = RuntimeError("ONNX inference failed")

        with patch(
            "app.proxy.plugins.compression._get_pipeline",
            return_value=mock_pipeline,
        ):
            plugin = CompressionPlugin()
            ctx = TransformContext(model="gpt-4o", user_id="user-1")
            result = plugin.transform_request(original, ctx)

        assert result == original
        assert "compression" not in ctx.request_metadata

    def test_fail_open_when_build_pipeline_raises(self):
        """If _get_pipeline raises, original messages are returned unchanged."""
        original = [{"role": "user", "content": "Hello!"}]

        with patch(
            "app.proxy.plugins.compression._get_pipeline",
            side_effect=ImportError("headroom not installed"),
        ):
            plugin = CompressionPlugin()
            ctx = TransformContext(model="gpt-4o", user_id="user-1")
            result = plugin.transform_request(original, ctx)

        assert result == original
        assert "compression" not in ctx.request_metadata

    def test_passthrough_when_optimize_disabled(self, monkeypatch):
        """When compression_optimize=False, messages pass through unchanged."""
        original = [{"role": "user", "content": "Hello!"}]
        monkeypatch.setattr(
            "app.config.settings.compression_optimize", False
        )

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

        fake_result = FakeTransformResult(
            messages=compressed,
            tokens_before=100,
            tokens_after=50,
            transforms_applied=["router:system:kompress"],
        )

        mock_pipeline = MagicMock()
        mock_pipeline.apply.return_value = fake_result

        with patch(
            "app.proxy.plugins.compression._get_pipeline",
            return_value=mock_pipeline,
        ):
            plugin = CompressionPlugin()
            tctx = TransformContext(
                model="gpt-4o", user_id="user-1", request_metadata={}
            )
            interceptor = RequestInterceptor([plugin])
            result = interceptor.run(original, tctx)

        assert result.final_messages == compressed
        assert len(result.diffs) > 0
        assert tctx.request_metadata.get("compression") is not None


class TestCompressionPluginConfig:
    """Tests for config-driven CompressConfig construction."""

    def test_config_uses_settings(self):
        """CompressConfig is built from app settings."""
        from app.config import Settings

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
        assert s.headroom_telemetry is False

    def test_tier2_settings_have_defaults(self):
        """Tier-2 pipeline settings use safe defaults."""
        from app.config import Settings

        s = Settings()

        assert s.compression_cache_aligner_enabled is False
        assert s.compression_intercept_tools is False
        assert s.compression_ccr_enabled is True
        assert s.compression_ccr_inject_marker is True
        assert s.compression_smartcrusher_max_items == 15
        assert s.compression_smartcrusher_min_tokens == 200
        assert s.compression_smartcrusher_profile == "moderate"
        assert s.compression_read_compress_superseded is False
        assert s.compression_protect_analysis_context is True
        assert s.compression_optimize is True
        assert s.compression_model_limit == 200000

    def test_pipeline_build_function_exists(self):
        """Smoke test: _build_pipeline is callable."""
        import app.proxy.plugins.compression as mod

        assert callable(mod._build_pipeline)

    def test_profile_name_accepted_values(self):
        """All valid profile names are accepted by Settings."""
        from app.config import Settings

        for profile in ("conservative", "moderate", "aggressive"):
            s = Settings(compression_smartcrusher_profile=profile)
            assert s.compression_smartcrusher_profile == profile
