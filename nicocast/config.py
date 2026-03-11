"""
Configuration management for NicoCast.

Settings are stored in an INI-style config file. The module searches for the
config file in the following order:
  1. Path given via --config CLI argument
  2. /etc/nicocast/nicocast.conf   (system-wide install)
  3. <repo-root>/config/nicocast.conf  (development / portable install)
"""

import os
import configparser
import logging

logger = logging.getLogger(__name__)

# Absolute path of the repo root (parent of this file's directory)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Search locations, in priority order
_DEFAULT_PATHS = [
    "/etc/nicocast/nicocast.conf",
    os.path.join(_REPO_ROOT, "config", "nicocast.conf"),
]

# ─── Built-in defaults ────────────────────────────────────────────────────────
DEFAULTS: dict[str, dict[str, str]] = {
    "general": {
        # Friendly name shown to source devices (Android Smart View, etc.)
        "device_name": "NicoCast",
        # WPS PIN used when connection_method = pin
        "pin": "12345678",
        # Connection method: pbc (push-button) | pin
        "connection_method": "pbc",
        # Log level: DEBUG | INFO | WARNING | ERROR
        "log_level": "INFO",
        # Path to the persistent log file (empty = file logging disabled)
        "log_file": "/var/log/nicocast/nicocast.log",
        # Maximum size of a single log file in bytes before rotation (default 5 MB)
        "log_max_bytes": "5242880",
        # Number of rotated log files to keep
        "log_backup_count": "5",
    },
    "wifi": {
        # Physical wireless interface used for P2P
        "interface": "wlan0",
        # wpa_supplicant control socket path for the interface above
        "wpa_supplicant_socket": "/var/run/wpa_supplicant/wlan0",
        # P2P Group-Owner intent (0–15; 15 = always become GO)
        "p2p_go_intent": "15",
        # WFD (Wi-Fi Display) subelement hex string:
        #   00          – subelement ID 0 (Device Information)
        #   06          – data length 6 bytes  (1-byte len encoding used by wpas)
        #   0011        – WFD Device Info: primary sink (bits[1:0]=01) +
        #                 session available (bit 4=1)  → 0x0011
        #   1C44        – RTSP control port 7236 (0x1C44)
        #   0032        – max throughput 50 Mbps (0x0032)
        "wfd_subelems": "000600111C440032",
        # Preferred 2.4 GHz channel for the P2P group (1–13)
        "channel": "6",
        # Maximum time (seconds) to wait for a P2P peer to appear
        "p2p_find_timeout": "120",
    },
    "miracast": {
        # TCP port the RTSP server listens on (standard: 7236)
        "rtsp_port": "7236",
        # UDP port the sink uses for incoming RTP video
        "rtp_port": "1028",
        # UDP port for RTCP (set to 0 to disable)
        "rtcp_port": "1029",
        # UDP port for RTP audio (set to 0 to disable audio)
        "audio_rtp_port": "1030",
        # Advertised video formats for GET_PARAMETER response
        # Format: native profile level cea_res vesa_res hh_res latency
        #         min_slice_size slice_enc_params frame_rate_ctrl max_hres max_vres
        # CEA bitmap 0x0000FFFF → resolutions up to 1280×720p60
        "video_formats": (
            "00 00 02 04 0000FFFF 00000000 00000000 "
            "00 0000 0000 00 none none"
        ),
        # Advertised audio codecs
        # LPCM 00000003 00 = 44.1 kHz + 48 kHz, 2 ch
        # AAC  00000007 00 = 48 kHz modes, 2 ch
        "audio_codecs": "LPCM 00000003 00, AAC 00000007 00",
        # RTSP session timeout in seconds
        "session_timeout": "60",
        # RTP jitter buffer latency in ms (GStreamer rtpjitterbuffer)
        "jitter_buffer_ms": "200",
    },
    "display": {
        # Video output sink: auto | kmssink | fbdevsink | ximagesink | fakesink
        # "kmssink" is recommended for Raspberry Pi OS Lite (no desktop).
        # "auto" lets GStreamer choose; use it if you run a desktop environment.
        "video_sink": "kmssink",
        # Force full-screen output (true/false)
        "fullscreen": "true",
        # Audio output: auto | hdmi | headphone | disabled
        "audio_output": "hdmi",
        # Use hardware H.264 decoder when available (true/false)
        "hw_decode": "true",
        # Extra GStreamer pipeline parameters (appended verbatim)
        "extra_pipeline_opts": "",
    },
    "webui": {
        # Enable the settings web interface (true/false)
        "enabled": "true",
        # Port the web UI listens on
        "port": "8080",
        # Bind address (0.0.0.0 = all interfaces)
        "bind": "0.0.0.0",
    },
}


class Config:
    """Thin wrapper around :class:`configparser.ConfigParser`."""

    def __init__(self, path: str | None = None):
        self._parser = configparser.ConfigParser()
        self._path = path
        self._load()

    # ─── Public API ───────────────────────────────────────────────────────────

    def get(self, section: str, key: str) -> str:
        """Return a config value, falling back to the built-in default."""
        try:
            return self._parser.get(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            try:
                return DEFAULTS[section][key]
            except KeyError:
                raise KeyError(f"No config value for [{section}] {key}")

    def getint(self, section: str, key: str) -> int:
        return int(self.get(section, key))

    def getbool(self, section: str, key: str) -> bool:
        return self.get(section, key).lower() in ("true", "1", "yes", "on")

    def set(self, section: str, key: str, value: str) -> None:
        """Update a value in memory (does not persist to disk automatically)."""
        if not self._parser.has_section(section):
            self._parser.add_section(section)
        self._parser.set(section, key, str(value))

    def save(self) -> None:
        """Persist current in-memory config to disk."""
        target = self._path or self._find_writable_path()
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as fh:
            self._parser.write(fh)
        logger.info("Config saved to %s", target)

    def as_dict(self) -> dict:
        """Return the entire config as a nested dict (with defaults merged)."""
        result: dict = {}
        for section, defaults in DEFAULTS.items():
            result[section] = {}
            for key in defaults:
                result[section][key] = self.get(section, key)
        return result

    @property
    def path(self) -> str | None:
        return self._path

    # ─── Private helpers ──────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load config from file.  Missing file → use built-in defaults."""
        # Seed parser with built-in defaults
        for section, kvs in DEFAULTS.items():
            self._parser[section] = kvs

        if self._path:
            paths_to_try = [self._path]
        else:
            paths_to_try = _DEFAULT_PATHS

        for p in paths_to_try:
            if os.path.exists(p):
                self._parser.read(p)
                self._path = p
                logger.info("Loaded config from %s", p)
                return

        logger.info("No config file found – using built-in defaults")

    @staticmethod
    def _find_writable_path() -> str:
        """Return the first writable location from the search list."""
        for p in _DEFAULT_PATHS:
            try:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                return p
            except PermissionError:
                continue
        raise RuntimeError("No writable location found for config file")
