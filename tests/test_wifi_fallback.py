"""Tests for the WiFi fallback-to-saved-network feature (wifi_p2p.WiFiP2P)."""

import threading
import time
from unittest.mock import MagicMock, call, patch

import pytest

from nicocast.config import Config
from nicocast.wifi_p2p import WiFiP2P


def _make_config(**wifi_overrides):
    """Return a Config with built-in defaults, applying optional [wifi] overrides."""
    cfg = Config(path="/tmp/nonexistent_fallback_test.conf")
    for key, value in wifi_overrides.items():
        cfg.set("wifi", key, value)
    return cfg


def _make_wifi(config=None) -> WiFiP2P:
    if config is None:
        config = _make_config()
    return WiFiP2P(config)


# ─── Config defaults ──────────────────────────────────────────────────────────


class TestFallbackConfigDefault:
    def test_default_timeout_is_300(self):
        cfg = _make_config()
        assert cfg.getint("wifi", "wifi_fallback_timeout") == 300

    def test_override_timeout(self):
        cfg = _make_config(wifi_fallback_timeout="60")
        assert cfg.getint("wifi", "wifi_fallback_timeout") == 60

    def test_disabled_when_zero(self):
        cfg = _make_config(wifi_fallback_timeout="0")
        assert cfg.getint("wifi", "wifi_fallback_timeout") == 0


# ─── Timer lifecycle ──────────────────────────────────────────────────────────


class TestFallbackTimerLifecycle:
    """Verify the timer is started, cancelled, and fires correctly."""

    def test_timer_started_on_start(self):
        """_start_fallback_timer should schedule a threading.Timer."""
        wifi = _make_wifi(_make_config(wifi_fallback_timeout="60"))
        with patch.object(wifi, "_wpa_cli", return_value="OK"), \
             patch.object(wifi, "_start_event_monitor"), \
             patch.object(wifi, "_wait_for_socket", return_value=True):
            wifi.start()

        assert wifi._fallback_timer is not None
        assert wifi._fallback_timer.is_alive()
        wifi._cancel_fallback_timer()  # cleanup

    def test_no_timer_when_disabled(self):
        """Timer should NOT be started when wifi_fallback_timeout=0."""
        wifi = _make_wifi(_make_config(wifi_fallback_timeout="0"))
        with patch.object(wifi, "_wpa_cli", return_value="OK"), \
             patch.object(wifi, "_start_event_monitor"), \
             patch.object(wifi, "_wait_for_socket", return_value=True):
            wifi.start()

        assert wifi._fallback_timer is None

    def test_timer_cancelled_on_stop(self):
        """stop() must cancel the timer so it doesn't fire after shutdown."""
        wifi = _make_wifi(_make_config(wifi_fallback_timeout="60"))
        with patch.object(wifi, "_wpa_cli", return_value="OK"), \
             patch.object(wifi, "_start_event_monitor"), \
             patch.object(wifi, "_wait_for_socket", return_value=True), \
             patch.object(wifi, "_stop_dnsmasq"):
            wifi.start()
            wifi.stop()

        assert wifi._fallback_timer is None

    def test_timer_cancelled_on_device_connected(self):
        """AP-STA-CONNECTED event must cancel the fallback timer."""
        wifi = _make_wifi(_make_config(wifi_fallback_timeout="60"))
        wifi._running = True
        wifi._start_fallback_timer()

        assert wifi._fallback_timer is not None

        with patch.object(wifi, "_wait_for_peer_ip"):
            wifi._dispatch_event("AP-STA-CONNECTED aa:bb:cc:dd:ee:ff")

        assert wifi._fallback_timer is None
        assert wifi.connected_peer == "aa:bb:cc:dd:ee:ff"


# ─── reconnect_saved_wifi ─────────────────────────────────────────────────────


class TestReconnectSavedWifi:
    """Unit-test the reconnect_saved_wifi() method."""

    def test_calls_p2p_stop_find(self):
        wifi = _make_wifi()
        with patch.object(wifi, "_wpa_cli", return_value="OK") as mock_cli, \
             patch.object(wifi, "_stop_dnsmasq"):
            wifi.reconnect_saved_wifi()
        assert call("P2P_STOP_FIND") in mock_cli.call_args_list

    def test_removes_group_if_present(self):
        wifi = _make_wifi()
        wifi.group_iface = "p2p-wlan0-0"
        with patch.object(wifi, "_wpa_cli", return_value="OK") as mock_cli, \
             patch.object(wifi, "_stop_dnsmasq"):
            wifi.reconnect_saved_wifi()
        assert call("P2P_GROUP_REMOVE", "p2p-wlan0-0") in mock_cli.call_args_list

    def test_no_group_remove_when_no_group(self):
        wifi = _make_wifi()
        wifi.group_iface = None
        with patch.object(wifi, "_wpa_cli", return_value="OK") as mock_cli, \
             patch.object(wifi, "_stop_dnsmasq"):
            wifi.reconnect_saved_wifi()
        for c in mock_cli.call_args_list:
            assert "P2P_GROUP_REMOVE" not in str(c)

    def test_disables_wfd_advertisement(self):
        wifi = _make_wifi()
        with patch.object(wifi, "_wpa_cli", return_value="OK") as mock_cli, \
             patch.object(wifi, "_stop_dnsmasq"):
            wifi.reconnect_saved_wifi()
        assert call("SET", "wifi_display", "0") in mock_cli.call_args_list

    def test_issues_disconnect_then_reconnect(self):
        wifi = _make_wifi()
        with patch.object(wifi, "_wpa_cli", return_value="OK") as mock_cli, \
             patch.object(wifi, "_stop_dnsmasq"):
            wifi.reconnect_saved_wifi()
        commands = [c.args for c in mock_cli.call_args_list]
        assert ("DISCONNECT",) in commands
        assert ("RECONNECT",) in commands
        # RECONNECT must come after DISCONNECT
        assert commands.index(("RECONNECT",)) > commands.index(("DISCONNECT",))


# ─── Fallback timeout fires ───────────────────────────────────────────────────


class TestFallbackTimeout:
    """Verify the full fallback flow when the timer fires."""

    def test_fallback_callback_invoked(self):
        """The registered on_fallback callback must be called when timer fires."""
        wifi = _make_wifi(_make_config(wifi_fallback_timeout="1"))
        cb = MagicMock()
        wifi.on_fallback(cb)

        with patch.object(wifi, "reconnect_saved_wifi"):
            wifi._on_fallback_timeout()

        cb.assert_called_once()

    def test_reconnect_called_on_timeout(self):
        """reconnect_saved_wifi() must be invoked when the timer fires."""
        wifi = _make_wifi()
        with patch.object(wifi, "reconnect_saved_wifi") as mock_reconnect:
            wifi._on_fallback_timeout()
        mock_reconnect.assert_called_once()

    def test_callback_exception_does_not_propagate(self):
        """Exceptions in the fallback callback must be caught and logged."""
        wifi = _make_wifi()
        bad_cb = MagicMock(side_effect=RuntimeError("boom"))
        wifi.on_fallback(bad_cb)

        with patch.object(wifi, "reconnect_saved_wifi"):
            # Should not raise
            wifi._on_fallback_timeout()

        bad_cb.assert_called_once()

    def test_timer_fires_after_short_timeout(self):
        """Integration: timer fires and calls reconnect_saved_wifi within timeout."""
        fired = threading.Event()
        wifi = _make_wifi(_make_config(wifi_fallback_timeout="1"))
        wifi.on_fallback(lambda: fired.set())

        with patch.object(wifi, "reconnect_saved_wifi"):
            wifi._start_fallback_timer()

        assert fired.wait(timeout=3), "Fallback timer did not fire within 3 s"
