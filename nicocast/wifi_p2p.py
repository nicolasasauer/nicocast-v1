"""
Wi-Fi Direct P2P controller for NicoCast.

Uses wpa_supplicant's UNIX control socket for commands and events, with
wpa_cli as a convenient subprocess wrapper for simple commands.

Key responsibilities:
  • Advertise WFD (Wi-Fi Display) Information Elements so Android devices
    discover NicoCast as a Miracast sink via Samsung Smart View, etc.
  • Act as P2P Group Owner (GO intent = 15) so no existing AP is needed.
  • Monitor P2P events and notify the rest of the application.
  • Run a DHCP server (dnsmasq) on the P2P group interface so the source
    device gets an IP address automatically.
"""

import os
import socket
import subprocess
import threading
import time
import logging
import ipaddress

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

# Subnet used for the P2P group (sink = .1, source gets .2–.254 via DHCP)
P2P_SUBNET = "192.168.49"
P2P_GO_IP = f"{P2P_SUBNET}.1"
P2P_DHCP_RANGE_START = f"{P2P_SUBNET}.2"
P2P_DHCP_RANGE_END = f"{P2P_SUBNET}.254"
P2P_NETMASK = "255.255.255.0"

# Candidates for the P2P group interface created by wpa_supplicant
_P2P_IFACE_CANDIDATES = ["p2p-wlan0-0", "p2p-wlan0-1", "p2p0", "wlan1"]


class WiFiP2P:
    """Manages Wi-Fi Direct P2P using wpa_supplicant."""

    def __init__(self, config):
        self.config = config
        self.iface = config.get("wifi", "interface")
        self.wpa_socket_path = config.get("wifi", "wpa_supplicant_socket")
        self._running = False
        self._event_thread: threading.Thread | None = None
        self._event_callbacks: list = []
        # UNIX datagram socket used for event monitoring
        self._evt_sock: socket.socket | None = None
        self._evt_sock_path = f"/tmp/nicocast_p2p_evt_{os.getpid()}"
        # State
        self.connected_peer: str | None = None  # MAC of connected peer
        self.group_iface: str | None = None     # e.g. p2p-wlan0-0
        self.peer_ip: str | None = None         # IP assigned to peer
        self._dnsmasq_proc: subprocess.Popen | None = None
        # Fallback-to-WiFi timer (fires if no Miracast device connects in time)
        self._fallback_timer: threading.Timer | None = None
        self._fallback_callbacks: list = []

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Configure Wi-Fi Direct and start advertising as a Miracast sink."""
        logger.info("Starting Wi-Fi Direct P2P…")

        device_name = self.config.get("general", "device_name")
        wfd_subelems = self.config.get("wifi", "wfd_subelems")
        go_intent = self.config.get("wifi", "p2p_go_intent")

        # Basic wpa_supplicant settings
        self._wpa_cli("SET", "device_name", device_name)
        self._wpa_cli("SET", "device_type", "7-0050F204-1")  # Display
        self._wpa_cli("SET", "config_methods", "keypad display push_button")
        self._wpa_cli("SET", "p2p_go_intent", go_intent)

        # Advertise as a WFD (Miracast) sink
        self._wpa_cli("SET", "wfd_subelems", wfd_subelems)
        self._wpa_cli("SET", "wifi_display", "1")

        # Start monitoring wpa_supplicant events (background thread)
        self._running = True
        self._start_event_monitor()

        # Start P2P device discovery so peers can find us
        self._wpa_cli("P2P_FIND")

        # Start the fallback timer (reconnect to saved WiFi if no device connects)
        self._start_fallback_timer()

        logger.info("Wi-Fi Direct P2P started, advertising as '%s'", device_name)
        self._log_startup_diagnostics()

    def _log_startup_diagnostics(self) -> None:
        """Log wpa_supplicant / WFD state for debugging Samsung Smart View issues."""
        logger.info("─── Wi-Fi Direct / WFD diagnostics ───")
        status = self._wpa_cli("STATUS")
        if status:
            for line in status.splitlines():
                logger.info("  wpa status: %s", line)
        wifi_display = self._wpa_cli("GET", "wifi_display")
        logger.info("  wifi_display = %s", wifi_display)
        wfd = self._wpa_cli("GET", "wfd_subelems")
        logger.info("  wfd_subelems = %s", wfd)
        dev_name = self._wpa_cli("GET", "device_name")
        logger.info("  device_name  = %s", dev_name)
        dev_type = self._wpa_cli("GET", "device_type")
        logger.info("  device_type  = %s", dev_type)
        if wifi_display and wifi_display.strip() == "0":
            logger.warning(
                "wifi_display is 0 – this device will NOT appear in Samsung Smart View. "
                "Check that wifi_display=1 is present in "
                "/etc/wpa_supplicant/wpa_supplicant-p2p.conf (re-run setup_wpa_supplicant.sh "
                "to regenerate it)."
            )
        elif wifi_display and wifi_display.strip() == "1":
            logger.info(
                "wifi_display=1 confirmed – device should be visible in Samsung Smart View "
                "as a Miracast sink (look for '%s').", self.config.get("general", "device_name")
            )
        logger.info("──────────────────────────────────────")

    def stop(self) -> None:
        """Tear down P2P and clean up."""
        logger.info("Stopping Wi-Fi Direct P2P…")
        self._running = False
        self._cancel_fallback_timer()
        self._wpa_cli("P2P_STOP_FIND")
        if self.group_iface:
            self._wpa_cli("P2P_GROUP_REMOVE", self.group_iface)
        self._stop_dnsmasq()
        # Close event socket
        if self._evt_sock:
            try:
                self._evt_sock.close()
            except Exception:
                pass
        try:
            os.unlink(self._evt_sock_path)
        except FileNotFoundError:
            pass

    def on_event(self, callback) -> None:
        """Register a callback for P2P events.

        The callback receives a single string argument (the event message).
        """
        self._event_callbacks.append(callback)

    def on_fallback(self, callback) -> None:
        """Register a callback invoked when the WiFi fallback timer fires.

        The callback receives no arguments.
        """
        self._fallback_callbacks.append(callback)

    def accept_connection(self, peer_addr: str) -> bool:
        """Accept a P2P connection from *peer_addr* (MAC address).

        Uses the configured connection method (pbc or pin).
        Returns True if wpa_supplicant accepted the command.
        """
        method = self.config.get("general", "connection_method")
        go_intent = self.config.get("wifi", "p2p_go_intent")

        if method == "pin":
            pin = self.config.get("general", "pin")
            result = self._wpa_cli(
                "P2P_CONNECT", peer_addr, pin, "display",
                f"go_intent={go_intent}",
            )
        else:
            result = self._wpa_cli(
                "P2P_CONNECT", peer_addr, "pbc",
                f"go_intent={go_intent}",
            )

        return result is not None and "FAIL" not in result

    def get_my_ip(self) -> str | None:
        """Return the IP address assigned to the P2P group interface."""
        iface = self.group_iface or self._find_group_iface()
        if not iface:
            return None
        return self._get_iface_ip(iface)

    # ─── wpa_supplicant event monitoring ──────────────────────────────────────

    def _start_event_monitor(self) -> None:
        self._event_thread = threading.Thread(
            target=self._event_loop, daemon=True, name="wpa-events"
        )
        self._event_thread.start()

    def _event_loop(self) -> None:
        """Receive and dispatch wpa_supplicant events."""
        # Remove stale socket file
        try:
            os.unlink(self._evt_sock_path)
        except FileNotFoundError:
            pass

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.bind(self._evt_sock_path)

        # Wait for wpa_supplicant socket to appear
        for _ in range(30):
            if os.path.exists(self.wpa_socket_path):
                break
            logger.debug("Waiting for wpa_supplicant socket…")
            time.sleep(1)
        else:
            logger.error(
                "wpa_supplicant socket not found at %s", self.wpa_socket_path
            )
            return

        sock.connect(self.wpa_socket_path)
        sock.settimeout(1.0)
        sock.send(b"ATTACH")

        self._evt_sock = sock
        logger.debug("Attached to wpa_supplicant event socket")

        while self._running:
            try:
                data = sock.recv(4096)
                msg = data.decode(errors="replace").strip()
                # Strip priority prefix like <3>
                if msg.startswith("<"):
                    idx = msg.find(">")
                    msg = msg[idx + 1:].strip() if idx >= 0 else msg
                self._dispatch_event(msg)
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    logger.error("Event socket closed unexpectedly")
                break

        try:
            sock.send(b"DETACH")
            sock.close()
            os.unlink(self._evt_sock_path)
        except Exception:
            pass

    def _dispatch_event(self, event: str) -> None:
        """Handle a single wpa_supplicant event string."""
        logger.debug("WPA event: %s", event)

        # ── P2P peer found ──────────────────────────────────────────────────
        if event.startswith("P2P-DEVICE-FOUND"):
            # Example: P2P-DEVICE-FOUND aa:bb:cc:dd:ee:ff p2p_dev_addr=...
            parts = event.split()
            if len(parts) >= 2:
                peer_addr = parts[1]
                logger.info("P2P peer found: %s – auto-accepting", peer_addr)
                threading.Thread(
                    target=self.accept_connection,
                    args=(peer_addr,),
                    daemon=True,
                ).start()

        # ── P2P group started (we are the GO) ───────────────────────────────
        elif event.startswith("P2P-GROUP-STARTED"):
            self._handle_group_started(event)

        # ── P2P group removed ───────────────────────────────────────────────
        elif event.startswith("P2P-GROUP-REMOVED"):
            logger.info("P2P group removed")
            self.group_iface = None
            self.connected_peer = None
            self.peer_ip = None
            self._stop_dnsmasq()
            # Restart discovery
            if self._running:
                self._wpa_cli("P2P_FIND")

        # ── New station (source device) joined the P2P group ────────────────
        elif event.startswith("AP-STA-CONNECTED"):
            parts = event.split()
            if len(parts) >= 2:
                self.connected_peer = parts[1]
                logger.info("Source device connected: %s", self.connected_peer)
                # A device connected – cancel the fallback-to-WiFi timer
                self._cancel_fallback_timer()
                # Give DHCP a moment to assign an IP
                threading.Thread(
                    target=self._wait_for_peer_ip, daemon=True
                ).start()

        # ── Source device left ───────────────────────────────────────────────
        elif event.startswith("AP-STA-DISCONNECTED"):
            logger.info("Source device disconnected")
            self.connected_peer = None
            self.peer_ip = None

        # Forward all events to registered callbacks
        for cb in self._event_callbacks:
            try:
                cb(event)
            except Exception as exc:
                logger.error("Event callback error: %s", exc, exc_info=True)

    def _handle_group_started(self, event: str) -> None:
        """Parse P2P-GROUP-STARTED event and configure the new interface."""
        # Example: P2P-GROUP-STARTED p2p-wlan0-0 GO ssid="..." freq=2437 ...
        parts = event.split()
        if len(parts) < 3:
            return
        iface = parts[1]
        role = parts[2]  # "GO" or "client"

        logger.info("P2P group started on interface '%s' (role: %s)", iface, role)
        self.group_iface = iface

        if role == "GO":
            # Assign a static IP to the GO interface
            self._configure_go_interface(iface)
            # Start a DHCP server so the source device gets an IP
            self._start_dnsmasq(iface)
        else:
            logger.info("We are a P2P client – waiting for IP via DHCP")
            threading.Thread(
                target=self._wait_for_own_ip, args=(iface,), daemon=True
            ).start()

    # ─── Fallback-to-WiFi timer ───────────────────────────────────────────────

    def _start_fallback_timer(self) -> None:
        """Start the one-shot fallback timer based on *wifi_fallback_timeout*."""
        timeout = self.config.getint("wifi", "wifi_fallback_timeout")
        if timeout <= 0:
            logger.debug("WiFi fallback timer disabled (wifi_fallback_timeout=0)")
            return
        logger.info(
            "WiFi fallback timer started – will reconnect to saved WiFi in %d s "
            "if no Miracast device connects",
            timeout,
        )
        self._fallback_timer = threading.Timer(timeout, self._on_fallback_timeout)
        self._fallback_timer.daemon = True
        self._fallback_timer.start()

    def _cancel_fallback_timer(self) -> None:
        """Cancel the fallback timer if it is still pending."""
        if self._fallback_timer is not None:
            self._fallback_timer.cancel()
            self._fallback_timer = None
            logger.debug("WiFi fallback timer cancelled")

    def _on_fallback_timeout(self) -> None:
        """Called by the timer when no Miracast device connected in time."""
        self._fallback_timer = None
        logger.warning(
            "No Miracast device connected within the configured timeout – "
            "falling back to saved Wi-Fi network"
        )
        self.reconnect_saved_wifi()
        for cb in self._fallback_callbacks:
            try:
                cb()
            except Exception as exc:
                logger.error("Fallback callback error: %s", exc, exc_info=True)

    def reconnect_saved_wifi(self) -> None:
        """Stop P2P and reconnect wpa_supplicant to the saved Wi-Fi network."""
        logger.info("Reconnecting to saved Wi-Fi network…")
        self._wpa_cli("P2P_STOP_FIND")
        if self.group_iface:
            self._wpa_cli("P2P_GROUP_REMOVE", self.group_iface)
        self._stop_dnsmasq()
        # Reset WFD advertisement so we no longer appear as a Miracast sink
        self._wpa_cli("SET", "wifi_display", "0")
        # Ask wpa_supplicant to (re-)connect using its stored network profiles
        self._wpa_cli("DISCONNECT")
        self._wpa_cli("RECONNECT")
        logger.info("Reconnect command sent – wpa_supplicant will now join saved network")

    # ─── Network helpers ──────────────────────────────────────────────────────

    def _configure_go_interface(self, iface: str) -> None:
        """Assign the GO static IP address to *iface*."""
        try:
            subprocess.run(
                ["ip", "addr", "flush", "dev", iface],
                check=True, capture_output=True,
            )
            subprocess.run(
                [
                    "ip", "addr", "add",
                    f"{P2P_GO_IP}/{ipaddress.IPv4Network(f'0.0.0.0/{P2P_NETMASK}').prefixlen}",
                    "dev", iface,
                ],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["ip", "link", "set", iface, "up"],
                check=True, capture_output=True,
            )
            logger.info("Assigned %s to %s", P2P_GO_IP, iface)
        except subprocess.CalledProcessError as exc:
            logger.error("Failed to configure GO interface: %s", exc.stderr)

    def _start_dnsmasq(self, iface: str) -> None:
        """Start a minimal dnsmasq DHCP server on *iface*."""
        self._stop_dnsmasq()
        cmd = [
            "dnsmasq",
            "--no-daemon",
            f"--interface={iface}",
            "--bind-interfaces",
            f"--dhcp-range={P2P_DHCP_RANGE_START},{P2P_DHCP_RANGE_END},1h",
            "--no-resolv",
            "--no-hosts",
            f"--pid-file=/tmp/nicocast_dnsmasq_{iface}.pid",
        ]
        logger.info("Starting dnsmasq on %s", iface)
        try:
            self._dnsmasq_proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except FileNotFoundError:
            logger.warning(
                "dnsmasq not found; source device may not get a DHCP address. "
                "Install it with: sudo apt install dnsmasq"
            )

    def _stop_dnsmasq(self) -> None:
        if self._dnsmasq_proc and self._dnsmasq_proc.poll() is None:
            self._dnsmasq_proc.terminate()
            try:
                self._dnsmasq_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._dnsmasq_proc.kill()
            self._dnsmasq_proc = None

    def _wait_for_peer_ip(self) -> None:
        """Poll ARP table until the connected peer has an IP, then store it."""
        for _ in range(30):
            time.sleep(1)
            if not self.connected_peer or not self.group_iface:
                return
            ip = self._get_peer_ip_from_arp(self.connected_peer)
            if ip:
                self.peer_ip = ip
                logger.info("Source device IP: %s", ip)
                return
        logger.warning("Could not determine source device IP from ARP table")

    def _wait_for_own_ip(self, iface: str) -> None:
        """Wait until the DHCP server assigns an IP to *iface* (client mode)."""
        for _ in range(30):
            time.sleep(1)
            ip = self._get_iface_ip(iface)
            if ip:
                logger.info("Got IP %s on %s", ip, iface)
                return
        logger.warning("Did not get an IP on %s after 30 s", iface)

    # ─── Low-level helpers ────────────────────────────────────────────────────

    def _wpa_cli(self, *args, timeout: int = 10) -> str | None:
        """Run a wpa_cli command and return its stdout."""
        cmd = ["wpa_cli", "-i", self.iface] + [str(a) for a in args]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            output = result.stdout.strip()
            if output.startswith("FAIL"):
                logger.warning("wpa_cli %s → %s", " ".join(args), output)
            else:
                logger.debug("wpa_cli %s → %s", " ".join(args), output)
            return output
        except subprocess.TimeoutExpired:
            logger.error("wpa_cli timeout: %s", " ".join(args))
            return None
        except FileNotFoundError:
            logger.error("wpa_cli not found – is wpasupplicant installed?")
            return None
        except Exception as exc:
            logger.error("wpa_cli error: %s", exc)
            return None

    def _find_group_iface(self) -> str | None:
        for candidate in _P2P_IFACE_CANDIDATES:
            if self._get_iface_ip(candidate) is not None:
                return candidate
        return None

    @staticmethod
    def _get_iface_ip(iface: str) -> str | None:
        try:
            result = subprocess.run(
                ["ip", "-4", "addr", "show", iface],
                capture_output=True, text=True,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("inet "):
                    return line.split()[1].split("/")[0]
        except Exception:
            pass
        return None

    @staticmethod
    def _get_peer_ip_from_arp(mac: str) -> str | None:
        """Look up an IP address in the ARP table by MAC address."""
        try:
            result = subprocess.run(
                ["arp", "-n"], capture_output=True, text=True
            )
            mac_lower = mac.lower()
            for line in result.stdout.splitlines():
                if mac_lower in line.lower():
                    parts = line.split()
                    if parts:
                        return parts[0]
        except Exception:
            pass
        return None
