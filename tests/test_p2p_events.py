"""Tests for Wi-Fi Direct P2P event handling in wifi_p2p.WiFiP2P.

Covers the Samsung Smart View connection flow:
  P2P-DEVICE-FOUND         → log only, do NOT auto-connect
  P2P-PROV-DISC-PBC-REQ    → accept the connection (user tapped NicoCast in Smart View)
  P2P-PROV-DISC-SHOW-PIN   → accept with configured PIN
  P2P-INVITATION-RECEIVED  → accept persistent group invitation
"""

import time
import threading
from unittest.mock import MagicMock, call, patch

import pytest

from nicocast.config import Config
from nicocast.wifi_p2p import WiFiP2P

PEER = "aa:bb:cc:dd:ee:ff"


def _make_config(**wifi_overrides):
    cfg = Config(path="/tmp/nonexistent_p2p_events_test.conf")
    for key, value in wifi_overrides.items():
        cfg.set("wifi", key, value)
    return cfg


def _make_wifi(config=None) -> WiFiP2P:
    if config is None:
        config = _make_config()
    return WiFiP2P(config)


# ─── P2P-DEVICE-FOUND ────────────────────────────────────────────────────────


class TestP2PDeviceFound:
    """P2P-DEVICE-FOUND must NOT trigger an auto-connect.

    Samsung Smart View discovers the sink passively; the actual connection is
    initiated by the user tapping on the sink, which generates
    P2P-PROV-DISC-PBC-REQ.  Auto-connecting on device-found races with the UI
    and causes the connection attempt to be rejected by Smart View.
    """

    def test_device_found_does_not_call_accept_connection(self):
        wifi = _make_wifi()
        wifi._running = True
        with patch.object(wifi, "accept_connection") as mock_accept:
            wifi._dispatch_event(f"P2P-DEVICE-FOUND {PEER} p2p_dev_addr={PEER}")
        mock_accept.assert_not_called()

    def test_device_found_does_not_call_wpa_cli_connect(self):
        wifi = _make_wifi()
        wifi._running = True
        with patch.object(wifi, "_wpa_cli") as mock_cli:
            wifi._dispatch_event(f"P2P-DEVICE-FOUND {PEER} p2p_dev_addr={PEER}")
        # No P2P_CONNECT command should be issued
        for c in mock_cli.call_args_list:
            assert "P2P_CONNECT" not in str(c)

    def test_device_found_with_no_mac_is_safe(self):
        """Malformed event with no MAC must not raise."""
        wifi = _make_wifi()
        wifi._running = True
        wifi._dispatch_event("P2P-DEVICE-FOUND")  # no MAC


# ─── P2P-PROV-DISC-PBC-REQ ───────────────────────────────────────────────────


class TestProvDiscPbcReq:
    """P2P-PROV-DISC-PBC-REQ is the event fired when the user taps NicoCast
    in Samsung Smart View.  It must trigger accept_connection() immediately.
    """

    def test_pbc_req_calls_accept_connection(self):
        wifi = _make_wifi()
        wifi._running = True
        with patch.object(wifi, "accept_connection") as mock_accept:
            wifi._dispatch_event(
                f"P2P-PROV-DISC-PBC-REQ {PEER} p2p_dev_addr={PEER} "
                f"name='Samsung Galaxy S24'"
            )
            # accept_connection is called in a thread – wait briefly
            time.sleep(0.1)
        mock_accept.assert_called_once_with(PEER)

    def test_pbc_req_uses_peer_address(self):
        """The MAC from the event (not a hard-coded value) must be passed."""
        other_peer = "11:22:33:44:55:66"
        wifi = _make_wifi()
        wifi._running = True
        with patch.object(wifi, "accept_connection") as mock_accept:
            wifi._dispatch_event(
                f"P2P-PROV-DISC-PBC-REQ {other_peer} p2p_dev_addr={other_peer}"
            )
            time.sleep(0.1)
        mock_accept.assert_called_once_with(other_peer)

    def test_pbc_req_with_no_mac_is_safe(self):
        """Malformed event with no MAC must not raise."""
        wifi = _make_wifi()
        wifi._running = True
        wifi._dispatch_event("P2P-PROV-DISC-PBC-REQ")

    def test_pbc_req_accept_connection_issues_p2p_connect(self):
        """accept_connection() must call P2P_CONNECT … pbc."""
        wifi = _make_wifi()
        with patch.object(wifi, "_wpa_cli", return_value="OK") as mock_cli:
            wifi.accept_connection(PEER)
        args_list = [c.args for c in mock_cli.call_args_list]
        assert any("P2P_CONNECT" in str(a) for a in args_list)
        assert any("pbc" in str(a) for a in args_list)


# ─── P2P-PROV-DISC-SHOW-PIN ──────────────────────────────────────────────────


class TestProvDiscShowPin:
    """P2P-PROV-DISC-SHOW-PIN is fired when the source wants PIN-based auth.
    NicoCast must still call accept_connection() so it responds within the
    PBC / PIN window.
    """

    def test_show_pin_calls_accept_connection(self):
        wifi = _make_wifi()
        wifi._running = True
        with patch.object(wifi, "accept_connection") as mock_accept:
            wifi._dispatch_event(
                f"P2P-PROV-DISC-SHOW-PIN {PEER} 12345678 p2p_dev_addr={PEER}"
            )
            time.sleep(0.1)
        mock_accept.assert_called_once_with(PEER)

    def test_show_pin_with_no_mac_is_safe(self):
        wifi = _make_wifi()
        wifi._running = True
        wifi._dispatch_event("P2P-PROV-DISC-SHOW-PIN")


# ─── P2P-INVITATION-RECEIVED ─────────────────────────────────────────────────


class TestInvitationReceived:
    """P2P-INVITATION-RECEIVED enables fast reconnection via a persistent group."""

    def test_invitation_calls_p2p_invite(self):
        wifi = _make_wifi()
        wifi._running = True
        with patch.object(wifi, "_wpa_cli") as mock_cli:
            wifi._dispatch_event(
                f"P2P-INVITATION-RECEIVED sa={PEER} persistent=2 "
                f"freq=2437 go_dev_addr={PEER}"
            )
        mock_cli.assert_called_once_with(
            "P2P_INVITE", "persistent=2", f"peer={PEER}"
        )

    def test_invitation_missing_persistent_does_not_raise(self):
        """Event without persistent= or sa= must log a warning, not raise."""
        wifi = _make_wifi()
        wifi._running = True
        with patch.object(wifi, "_wpa_cli") as mock_cli:
            wifi._dispatch_event("P2P-INVITATION-RECEIVED no_useful_tokens")
        mock_cli.assert_not_called()


# ─── P2P group lifecycle after events ────────────────────────────────────────


class TestGroupLifecycle:
    """Verify that group removal restarts P2P_FIND so the sink is discoverable
    again by Samsung Smart View after a session ends.
    """

    def test_group_removed_restarts_p2p_find(self):
        wifi = _make_wifi()
        wifi._running = True
        wifi.group_iface = "p2p-wlan0-0"
        with patch.object(wifi, "_wpa_cli") as mock_cli, \
             patch.object(wifi, "_stop_dnsmasq"):
            wifi._dispatch_event("P2P-GROUP-REMOVED p2p-wlan0-0 GO reason=0")
        assert call("P2P_FIND") in mock_cli.call_args_list

    def test_group_removed_clears_state(self):
        wifi = _make_wifi()
        wifi._running = True
        wifi.group_iface = "p2p-wlan0-0"
        wifi.connected_peer = PEER
        wifi.peer_ip = "192.168.49.2"
        with patch.object(wifi, "_wpa_cli"), \
             patch.object(wifi, "_stop_dnsmasq"):
            wifi._dispatch_event("P2P-GROUP-REMOVED p2p-wlan0-0 GO reason=0")
        assert wifi.group_iface is None
        assert wifi.connected_peer is None
        assert wifi.peer_ip is None


# ─── start() socket wait ──────────────────────────────────────────────────────


class TestStartSocketWait:
    """start() must wait for the wpa_supplicant socket before issuing SET commands."""

    def test_start_waits_for_socket(self):
        wifi = _make_wifi()
        with patch.object(wifi, "_wait_for_socket", return_value=True) as mock_wait, \
             patch.object(wifi, "_wpa_cli", return_value="OK"), \
             patch.object(wifi, "_start_event_monitor"):
            wifi.start()
            wifi._cancel_fallback_timer()
        mock_wait.assert_called_once()

    def test_start_continues_even_if_socket_absent(self):
        """start() must not raise if the socket never appears (degraded mode)."""
        wifi = _make_wifi()
        with patch.object(wifi, "_wait_for_socket", return_value=False), \
             patch.object(wifi, "_wpa_cli", return_value="OK"), \
             patch.object(wifi, "_start_event_monitor"):
            wifi.start()   # must not raise
            wifi._cancel_fallback_timer()
