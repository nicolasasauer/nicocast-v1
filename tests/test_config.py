"""Tests for nicocast.config module."""

import os
import tempfile
import pytest

from nicocast.config import Config, DEFAULTS


class TestConfigDefaults:
    """Verify that built-in defaults are returned when no file exists."""

    def setup_method(self):
        # Point at a non-existent file so only built-in defaults are used
        self.cfg = Config(path="/tmp/nonexistent_nicocast_test.conf")

    def test_device_name_default(self):
        assert self.cfg.get("general", "device_name") == "NicoCast"

    def test_rtsp_port_default(self):
        assert self.cfg.getint("miracast", "rtsp_port") == 7236

    def test_rtp_port_default(self):
        assert self.cfg.getint("miracast", "rtp_port") == 1028

    def test_wfd_subelems_default(self):
        subelems = self.cfg.get("wifi", "wfd_subelems")
        assert subelems == "000600111C440032"

    def test_p2p_go_intent_default(self):
        assert self.cfg.getint("wifi", "p2p_go_intent") == 15

    def test_webui_enabled_default(self):
        assert self.cfg.getbool("webui", "enabled") is True

    def test_webui_port_default(self):
        assert self.cfg.getint("webui", "port") == 8080

    def test_hw_decode_default(self):
        assert self.cfg.getbool("display", "hw_decode") is True

    def test_fullscreen_default(self):
        assert self.cfg.getbool("display", "fullscreen") is True

    def test_operation_mode_default(self):
        assert self.cfg.get("general", "operation_mode") == "hybrid"

    def test_operation_mode_in_defaults(self):
        assert "operation_mode" in DEFAULTS["general"]

    def test_unknown_key_raises(self):
        with pytest.raises(KeyError):
            self.cfg.get("general", "nonexistent_key_xyz")

    def test_as_dict_contains_all_sections(self):
        d = self.cfg.as_dict()
        for section in DEFAULTS:
            assert section in d

    def test_as_dict_values_match_get(self):
        d = self.cfg.as_dict()
        assert d["general"]["device_name"] == self.cfg.get("general", "device_name")
        assert d["miracast"]["rtsp_port"] == self.cfg.get("miracast", "rtsp_port")


class TestConfigFileRead:
    """Verify that values from a config file override the defaults."""

    def test_reads_device_name_from_file(self, tmp_path):
        conf = tmp_path / "nicocast.conf"
        conf.write_text(
            "[general]\ndevice_name = MyTestSink\n"
        )
        cfg = Config(path=str(conf))
        assert cfg.get("general", "device_name") == "MyTestSink"

    def test_reads_rtsp_port_from_file(self, tmp_path):
        conf = tmp_path / "nicocast.conf"
        conf.write_text("[miracast]\nrtsp_port = 7777\n")
        cfg = Config(path=str(conf))
        assert cfg.getint("miracast", "rtsp_port") == 7777

    def test_missing_key_falls_back_to_default(self, tmp_path):
        conf = tmp_path / "nicocast.conf"
        conf.write_text("[general]\ndevice_name = Override\n")
        cfg = Config(path=str(conf))
        # rtp_port not in file → should return default
        assert cfg.getint("miracast", "rtp_port") == 1028

    def test_reads_operation_mode_hybrid_from_file(self, tmp_path):
        conf = tmp_path / "nicocast.conf"
        conf.write_text("[general]\noperation_mode = hybrid\n")
        cfg = Config(path=str(conf))
        assert cfg.get("general", "operation_mode") == "hybrid"

    def test_reads_operation_mode_performance_from_file(self, tmp_path):
        conf = tmp_path / "nicocast.conf"
        conf.write_text("[general]\noperation_mode = performance\n")
        cfg = Config(path=str(conf))
        assert cfg.get("general", "operation_mode") == "performance"

    def test_operation_mode_falls_back_to_hybrid_when_missing(self, tmp_path):
        conf = tmp_path / "nicocast.conf"
        conf.write_text("[general]\ndevice_name = SomeDevice\n")
        cfg = Config(path=str(conf))
        # operation_mode not in file → should return default "hybrid"
        assert cfg.get("general", "operation_mode") == "hybrid"


class TestConfigSetAndSave:
    """Verify in-memory set() and save() work correctly."""

    def test_set_changes_value(self):
        cfg = Config(path="/tmp/nonexistent_nicocast_test2.conf")
        cfg.set("general", "device_name", "ChangedName")
        assert cfg.get("general", "device_name") == "ChangedName"

    def test_save_writes_file(self, tmp_path):
        conf = tmp_path / "nicocast.conf"
        cfg = Config(path=str(conf))
        cfg.set("general", "device_name", "SavedName")
        cfg.save()
        assert conf.exists()
        content = conf.read_text()
        assert "SavedName" in content

    def test_save_then_reload(self, tmp_path):
        conf = tmp_path / "nicocast.conf"
        cfg = Config(path=str(conf))
        cfg.set("general", "device_name", "ReloadTest")
        cfg.save()
        cfg2 = Config(path=str(conf))
        assert cfg2.get("general", "device_name") == "ReloadTest"
