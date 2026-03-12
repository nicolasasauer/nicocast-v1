"""
NicoCast – main entry point.

Orchestrates the three main subsystems:
  1. Wi-Fi Direct P2P  (wifi_p2p.WiFiP2P)
  2. RTSP / Miracast session server  (rtsp_handler.RTSPServer)
  3. GStreamer display pipeline  (display_pipeline.DisplayPipeline)
  4. Settings web UI  (web_ui.WebUI)

Usage:
    python -m nicocast [--config /path/to/nicocast.conf] [--no-webui]

Or via the installed entry point:
    nicocast [--config ...] [--no-webui]
"""

import argparse
import logging
import logging.handlers
import os
import signal
import sys
import threading
import time

from .config import Config
from .wifi_p2p import WiFiP2P
from .rtsp_handler import RTSPServer
from .display_pipeline import DisplayPipeline
from .web_ui import WebUI


def _setup_logging(level_str: str, log_file: str = "", max_bytes: int = 5242880, backup_count: int = 5) -> None:
    level = getattr(logging, level_str.upper(), logging.INFO)
    fmt = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    # Console handler (stderr) – always active
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))

    handlers: list[logging.Handler] = [console_handler]

    # Persistent rotating file handler – active when log_file is configured
    if log_file:
        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
            handlers.append(file_handler)
        except OSError as exc:
            # Log the warning via the console handler that is already set up
            logging.basicConfig(level=level, format=fmt, datefmt=datefmt)
            logging.getLogger(__name__).warning(
                "Could not open log file '%s': %s – file logging disabled",
                log_file, exc,
            )
            return

    logging.basicConfig(level=level, handlers=handlers, format=fmt, datefmt=datefmt)


class NicoCast:
    """Top-level application controller."""

    def __init__(self, config: Config):
        self.config = config
        self._running = False
        self._restart_requested = False

        self.wifi = WiFiP2P(config)
        self.rtsp = RTSPServer(
            config,
            on_session_start=self._on_session_start,
            on_session_end=self._on_session_end,
        )
        self.display = DisplayPipeline(config)
        self.webui = WebUI(
            config,
            status_provider=self._get_status,
            restart_callback=self._request_restart,
        )

        # State accessible by the web UI
        self._streaming = False

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start all subsystems."""
        log_level = self.config.get("general", "log_level")
        log_file = self.config.get("general", "log_file")
        log_max_bytes = self.config.getint("general", "log_max_bytes")
        log_backup_count = self.config.getint("general", "log_backup_count")
        _setup_logging(log_level, log_file, log_max_bytes, log_backup_count)
        logger = logging.getLogger(__name__)

        device_name = self.config.get("general", "device_name")
        logger.info("═══════════════════════════════════════")
        logger.info("  NicoCast starting – device: '%s'", device_name)
        logger.info("═══════════════════════════════════════")
        logger.info("Config file : %s", self.config.path or "(built-in defaults)")
        logger.info("Log file    : %s", log_file or "(file logging disabled)")
        logger.info("Log level   : %s", log_level)

        # Log the active operation mode and emit a warning for hybrid mode because
        # NetworkManager's background Wi-Fi scanning can increase video latency.
        operation_mode = self.config.get("general", "operation_mode").lower().strip()
        if operation_mode == "performance":
            logger.info(
                "Operation mode: PERFORMANCE (P2P-only). "
                "NetworkManager is stopped by the pre-start script."
            )
        else:
            logger.warning(
                "Operation mode: HYBRID (STA + P2P). "
                "NetworkManager is active – background Wi-Fi scanning by "
                "NetworkManager may increase video latency. "
                "Switch to 'performance' mode with: sudo toggle-mode.sh"
            )

        self._running = True

        # Register P2P event handler
        self.wifi.on_event(self._on_p2p_event)
        # Register WiFi fallback handler
        self.wifi.on_fallback(self._on_wifi_fallback)

        # Start subsystems
        self.wifi.start()
        self.rtsp.start()
        self.webui.start()

        logger.info(
            "Ready. Waiting for connections on RTSP port %s …",
            self.config.get("miracast", "rtsp_port"),
        )

    def stop(self) -> None:
        """Stop all subsystems."""
        logger = logging.getLogger(__name__)
        logger.info("Shutting down NicoCast…")
        self._running = False
        self.display.stop()
        self.rtsp.stop()
        self.wifi.stop()
        self.webui.stop()
        logger.info("NicoCast stopped.")

    def run(self) -> None:
        """Start and block until a shutdown signal is received."""
        self.start()
        try:
            while self._running:
                if self._restart_requested:
                    self._do_restart()
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    # ─── Callbacks ────────────────────────────────────────────────────────────

    def _on_p2p_event(self, event: str) -> None:
        logger = logging.getLogger(__name__)
        # If the group was removed while we were streaming, stop the pipeline
        if event.startswith("P2P-GROUP-REMOVED") and self._streaming:
            logger.info("P2P group removed – stopping display pipeline")
            self.display.stop()
            self._streaming = False

    def _on_wifi_fallback(self) -> None:
        """Called when the WiFi fallback timer fires (no Miracast device connected)."""
        logger = logging.getLogger(__name__)
        logger.warning(
            "WiFi fallback triggered – stopping Miracast subsystems so the "
            "device is reachable via SSH on the saved Wi-Fi network"
        )
        # Stop RTSP and display pipeline; WiFiP2P already issued RECONNECT.
        self.display.stop()
        self.rtsp.stop()
        self._streaming = False
        self._running = False

    def _on_session_start(
        self, rtp_port: int, video_format: str, audio_codecs: str
    ) -> None:
        """Called when an RTSP PLAY command is received (streaming begins)."""
        logger = logging.getLogger(__name__)
        logger.info("Session start: rtp_port=%d", rtp_port)
        self._streaming = True
        self.display.start(rtp_port, video_format, audio_codecs)

    def _on_session_end(self, session) -> None:
        """Called when an RTSP session ends (TEARDOWN or disconnect)."""
        logger = logging.getLogger(__name__)
        logger.info("Session ended")
        self._streaming = False
        self.display.stop()

    # ─── Status & restart ─────────────────────────────────────────────────────

    def _get_status(self) -> dict:
        return {
            "connected": self.wifi.connected_peer is not None,
            "streaming": self._streaming,
            "peer_ip": self.wifi.peer_ip,
            "peer_mac": self.wifi.connected_peer,
            "group_iface": self.wifi.group_iface,
            "my_ip": self.wifi.get_my_ip(),
            "device_name": self.config.get("general", "device_name"),
        }

    def _request_restart(self) -> None:
        self._restart_requested = True

    def _do_restart(self) -> None:
        logger = logging.getLogger(__name__)
        logger.info("Restarting NicoCast…")
        self._restart_requested = False
        self.stop()
        time.sleep(1)
        # Re-read config
        self.config._load()
        # Re-create subsystems
        self.wifi = WiFiP2P(self.config)
        self.rtsp = RTSPServer(
            self.config,
            on_session_start=self._on_session_start,
            on_session_end=self._on_session_end,
        )
        self.display = DisplayPipeline(self.config)
        self.webui = WebUI(
            self.config,
            status_provider=self._get_status,
            restart_callback=self._request_restart,
        )
        self._streaming = False
        self._running = True
        self.start()


# ─── CLI entry point ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="NicoCast – Miracast sink for Raspberry Pi Zero 2W"
    )
    parser.add_argument(
        "--config", "-c",
        metavar="FILE",
        default=None,
        help="Path to nicocast.conf (default: auto-detected)",
    )
    parser.add_argument(
        "--no-webui",
        action="store_true",
        default=False,
        help="Disable the settings web UI",
    )
    args = parser.parse_args()

    config = Config(path=args.config)

    if args.no_webui:
        config.set("webui", "enabled", "false")

    app = NicoCast(config)

    # Handle SIGTERM / SIGINT gracefully
    def _sig_handler(signum, frame):
        app._running = False

    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    app.run()


if __name__ == "__main__":
    main()
