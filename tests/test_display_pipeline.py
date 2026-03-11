"""Tests for nicocast.display_pipeline module."""

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from nicocast.config import Config
from nicocast.display_pipeline import DisplayPipeline


def _make_config(*section_key_values):
    """Return a Config instance using only built-in defaults (no file).

    Pass overrides as (section, key, value) tuples.
    """
    cfg = Config(path="/tmp/nonexistent_display_test.conf")
    for section, key, value in section_key_values:
        cfg.set(section, key, value)
    return cfg


class TestDisplayPipelineConstruction:
    """Unit-test pipeline string construction (no actual GStreamer needed)."""

    def test_build_pipeline_contains_udpsrc(self):
        cfg = _make_config()
        dp = DisplayPipeline(cfg)
        pipeline = dp._build_pipeline(1028, "", "")
        assert "udpsrc" in pipeline
        assert "1028" in pipeline

    def test_build_pipeline_contains_rtph264depay(self):
        cfg = _make_config()
        dp = DisplayPipeline(cfg)
        pipeline = dp._build_pipeline(1028, "", "")
        assert "rtph264depay" in pipeline
        assert "h264parse" in pipeline

    def test_build_pipeline_uses_configured_jitter(self):
        cfg = _make_config(("miracast", "jitter_buffer_ms", "500"))
        dp = DisplayPipeline(cfg)
        pipeline = dp._build_pipeline(1028, "", "")
        assert "latency=500" in pipeline

    def test_select_video_sink_auto(self):
        result = DisplayPipeline._select_video_sink("auto", True)
        assert "autovideosink" in result
        assert "fullscreen=true" in result

    def test_select_video_sink_kmssink(self):
        result = DisplayPipeline._select_video_sink("kmssink", True)
        assert "kmssink" in result

    def test_select_video_sink_fakesink(self):
        result = DisplayPipeline._select_video_sink("fakesink", False)
        assert "fakesink" in result

    def test_select_video_sink_no_fullscreen(self):
        result = DisplayPipeline._select_video_sink("auto", False)
        assert "fullscreen" not in result

    def test_hw_decode_false_uses_avdec(self):
        cfg = _make_config(("display", "hw_decode", "false"))
        dp = DisplayPipeline(cfg)
        # Mock gst_element_exists to return False for hw elements
        with patch.object(dp, "_gst_element_exists", return_value=False):
            decoder = dp._select_video_decoder(hw_decode=False)
        assert decoder == "avdec_h264"

    def test_build_pipeline_aac_audio_branch(self):
        cfg = _make_config(
            ("miracast", "audio_rtp_port", "1030"),
            ("display", "audio_output", "hdmi"),
        )
        dp = DisplayPipeline(cfg)
        pipeline = dp._build_pipeline(1028, "", "AAC 00000007 00")
        assert "1030" in pipeline
        assert "rtpmp4adepay" in pipeline

    def test_build_pipeline_disabled_audio(self):
        cfg = _make_config(("display", "audio_output", "disabled"))
        dp = DisplayPipeline(cfg)
        pipeline = dp._build_pipeline(1028, "", "AAC 00000007 00")
        # Audio branch should not be present when disabled
        assert "rtpmp4adepay" not in pipeline

    def test_build_pipeline_zero_audio_port_no_audio(self):
        cfg = _make_config(("miracast", "audio_rtp_port", "0"))
        dp = DisplayPipeline(cfg)
        pipeline = dp._build_pipeline(1028, "", "AAC 00000007 00")
        assert "rtpmp4adepay" not in pipeline

    def test_build_pipeline_lpcm_audio_branch(self):
        cfg = _make_config(
            ("miracast", "audio_rtp_port", "1030"),
            ("display", "audio_output", "hdmi"),
        )
        dp = DisplayPipeline(cfg)
        pipeline = dp._build_pipeline(1028, "", "LPCM 00000003 00")
        assert "rtpL16depay" in pipeline


class TestDisplayPipelineLifecycle:
    """Test start/stop without a real GStreamer installation."""

    def test_start_fails_gracefully_without_gstreamer(self):
        cfg = _make_config()
        dp = DisplayPipeline(cfg)
        with patch("shutil.which", return_value=None):
            # Should log an error but not raise
            dp.start(1028)
        assert not dp.is_running()

    def test_stop_when_not_running_is_safe(self):
        cfg = _make_config()
        dp = DisplayPipeline(cfg)
        dp.stop()  # Should not raise

    def test_is_running_false_initially(self):
        cfg = _make_config()
        dp = DisplayPipeline(cfg)
        assert dp.is_running() is False

    def test_gst_element_exists_returns_false_for_unknown(self):
        # gst-inspect-1.0 is unlikely to be installed in the test environment
        result = DisplayPipeline._gst_element_exists("__nonexistent_element__")
        assert result is False
