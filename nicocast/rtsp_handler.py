"""
Miracast RTSP session handler for NicoCast.

Implements the WFD (Wi-Fi Display) RTSP message exchange (M1–M12) on top of
a plain TCP socket.  The handler runs one session per client connection.

WFD RTSP message sequence (sink is the TCP server on port 7236):
  M1  Source → Sink   OPTIONS *
  M2  Sink   → Source 200 OK  (supported methods)
  M3  Source → Sink   GET_PARAMETER  (query sink capabilities)
  M4  Sink   → Source 200 OK  (capability response)
  M5  Source → Sink   SET_PARAMETER  (chosen parameters)
  M6  Sink   → Source 200 OK
  M7  Source → Sink   SET_PARAMETER  wfd_trigger_method: SETUP
  M8  Sink   → Source 200 OK
  M9  Source → Sink   SETUP          (RTP transport)
  M10 Sink   → Source 200 OK  (session ID)
  M11 Source → Sink   PLAY
  M12 Sink   → Source 200 OK  → streaming begins
  …
  Mx  Either → Either TEARDOWN
"""

import socket
import threading
import logging
import time
import re

logger = logging.getLogger(__name__)

# Methods we support (advertised in M2)
_SUPPORTED_METHODS = (
    "org.wfa.wfd1.0, GET_PARAMETER, SET_PARAMETER, SETUP, PLAY, TEARDOWN, PAUSE"
)

# Default RTSP/RTP parameters (overridden by SET_PARAMETER from source)
_DEFAULT_VIDEO_FORMAT = (
    "00 00 02 04 0000FFFF 00000000 00000000 00 0000 0000 00 none none"
)
_DEFAULT_AUDIO_CODECS = "LPCM 00000003 00, AAC 00000007 00"


class RTSPServer:
    """TCP server that accepts Miracast RTSP connections on *port*."""

    def __init__(self, config, on_session_start=None, on_session_end=None):
        self.config = config
        self.on_session_start = on_session_start  # callback(session)
        self.on_session_end = on_session_end       # callback(session)
        self._port = config.getint("miracast", "rtsp_port")
        self._running = False
        self._server_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._active_session: "RTSPSession | None" = None

    def start(self, bind_host: str = "0.0.0.0") -> None:
        """Start the RTSP server.

        The server must listen on all interfaces (0.0.0.0) by default because
        the source device connects via the Wi-Fi Direct P2P virtual interface
        (e.g. p2p-wlan0-0) whose IP address is not known at startup.

        For deployments where the P2P group interface IP is known in advance,
        pass it explicitly as *bind_host* to restrict access to that interface.
        """
        self._running = True
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((bind_host, self._port))
        self._server_sock.listen(1)
        self._server_sock.settimeout(1.0)
        self._thread = threading.Thread(
            target=self._accept_loop, daemon=True, name="rtsp-server"
        )
        self._thread.start()
        logger.info("RTSP server listening on %s:%d", bind_host, self._port)

    def stop(self) -> None:
        self._running = False
        if self._active_session:
            self._active_session.close()
        if self._server_sock:
            self._server_sock.close()

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            logger.info("RTSP connection from %s", addr)
            session = RTSPSession(
                conn, addr, self.config,
                on_start=self.on_session_start,
                on_end=self.on_session_end,
            )
            self._active_session = session
            t = threading.Thread(
                target=session.handle, daemon=True, name=f"rtsp-{addr[0]}"
            )
            t.start()


class RTSPSession:
    """Handles a single Miracast RTSP client session."""

    def __init__(self, conn: socket.socket, addr, config,
                 on_start=None, on_end=None):
        self.conn = conn
        self.addr = addr
        self.config = config
        self.on_start = on_start  # callback(rtp_port, video_fmt, audio_fmt)
        self.on_end = on_end

        self._rtp_port = config.getint("miracast", "rtp_port")
        self._session_id: str | None = None
        self._cseq = 0
        self._running = True
        self._buf = b""

        # Negotiated parameters (updated from SET_PARAMETER)
        self.negotiated_video_format: str = config.get("miracast", "video_formats")
        self.negotiated_audio_codecs: str = config.get("miracast", "audio_codecs")
        self.presentation_url: str = "rtsp://localhost/wfd1.0"
        self.source_rtp_port: int | None = None

    # ─── Public ───────────────────────────────────────────────────────────────

    def handle(self) -> None:
        """Main loop: read requests, dispatch, send responses."""
        self.conn.settimeout(
            float(self.config.get("miracast", "session_timeout"))
        )
        try:
            while self._running:
                request = self._recv_request()
                if request is None:
                    break
                self._dispatch(request)
        except socket.timeout:
            logger.info("RTSP session timed out (%s)", self.addr)
        except ConnectionResetError:
            logger.info("RTSP connection reset by %s", self.addr)
        except Exception as exc:
            logger.error("RTSP session error: %s", exc)
        finally:
            self._teardown()

    def close(self) -> None:
        self._running = False
        try:
            self.conn.close()
        except Exception:
            pass

    # ─── Request dispatch ─────────────────────────────────────────────────────

    def _dispatch(self, request: dict) -> None:
        method = request.get("method", "")
        logger.debug("RTSP %s (CSeq %s)", method, request.get("cseq"))

        if method == "OPTIONS":
            self._handle_options(request)
        elif method == "GET_PARAMETER":
            self._handle_get_parameter(request)
        elif method == "SET_PARAMETER":
            self._handle_set_parameter(request)
        elif method == "SETUP":
            self._handle_setup(request)
        elif method == "PLAY":
            self._handle_play(request)
        elif method == "PAUSE":
            self._handle_pause(request)
        elif method == "TEARDOWN":
            self._handle_teardown(request)
        else:
            logger.warning("Unknown RTSP method: %s", method)
            self._send_response(request["cseq"], 501, "Not Implemented")

    # ─── Message handlers (M1/M2 through M11/M12) ────────────────────────────

    def _handle_options(self, req: dict) -> None:
        """M2: Respond to OPTIONS with our supported method list."""
        self._send_response(
            req["cseq"], 200, "OK",
            extra_headers={"Public": _SUPPORTED_METHODS},
        )

    def _handle_get_parameter(self, req: dict) -> None:
        """M4: Respond to GET_PARAMETER with our WFD capabilities."""
        requested = [
            ln.strip()
            for ln in req.get("body", "").splitlines()
            if ln.strip()
        ]
        body_lines: list[str] = []

        for param in requested:
            if param == "wfd_client_rtp_ports":
                body_lines.append(
                    f"wfd_client_rtp_ports: RTP/AVP/UDP;unicast "
                    f"{self._rtp_port} 0 mode=play"
                )
            elif param == "wfd_video_formats":
                fmt = self.config.get("miracast", "video_formats")
                body_lines.append(f"wfd_video_formats: {fmt}")
            elif param == "wfd_audio_codecs":
                codecs = self.config.get("miracast", "audio_codecs")
                body_lines.append(f"wfd_audio_codecs: {codecs}")
            elif param == "wfd_3d_formats":
                body_lines.append("wfd_3d_formats: none")
            elif param == "wfd_content_protection":
                body_lines.append("wfd_content_protection: none")
            elif param == "wfd_display_edid":
                body_lines.append("wfd_display_edid: none")
            elif param == "wfd_coupled_sink":
                body_lines.append("wfd_coupled_sink: none")
            elif param == "wfd_uibc_capability":
                body_lines.append("wfd_uibc_capability: none")
            elif param == "wfd_standby_resume_capability":
                body_lines.append("wfd_standby_resume_capability: none")
            elif param == "wfd_connector_type":
                body_lines.append("wfd_connector_type: 05")  # HDMI
            else:
                body_lines.append(f"{param}: none")

        body = "\r\n".join(body_lines) + "\r\n"
        self._send_response(req["cseq"], 200, "OK", body=body)

    def _handle_set_parameter(self, req: dict) -> None:
        """M6/M8: Handle SET_PARAMETER (session params or trigger)."""
        body = req.get("body", "")

        # Check for trigger method first
        trigger = self._parse_param(body, "wfd_trigger_method")
        if trigger:
            logger.info("WFD trigger: %s", trigger)
            self._send_response(req["cseq"], 200, "OK")
            # SETUP will be sent by the source shortly
            return

        # Parse negotiated parameters
        video_fmt = self._parse_param(body, "wfd_video_formats")
        if video_fmt and video_fmt != "none":
            self.negotiated_video_format = video_fmt
            logger.info("Negotiated video format: %s", video_fmt)

        audio = self._parse_param(body, "wfd_audio_codecs")
        if audio and audio != "none":
            self.negotiated_audio_codecs = audio
            logger.info("Negotiated audio: %s", audio)

        url = self._parse_param(body, "wfd_presentation_url")
        if url:
            self.presentation_url = url.split()[0]
            logger.info("Presentation URL: %s", self.presentation_url)

        self._send_response(req["cseq"], 200, "OK")

    def _handle_setup(self, req: dict) -> None:
        """M10: Handle SETUP – extract source RTP port, reply with session."""
        transport_hdr = req.get("headers", {}).get("transport", "")
        self.source_rtp_port = self._parse_client_port(transport_hdr)

        self._session_id = str(int(time.time()))[-8:]

        timeout = self.config.get("miracast", "session_timeout")
        transport = (
            f"RTP/AVP/UDP;unicast;"
            f"client_port={self._rtp_port};"
            f"server_port={self._rtp_port}"
        )
        self._send_response(
            req["cseq"], 200, "OK",
            extra_headers={
                "Transport": transport,
                "Session": f"{self._session_id};timeout={timeout}",
            },
        )

    def _handle_play(self, req: dict) -> None:
        """M12: Handle PLAY – streaming starts, notify display pipeline."""
        self._send_response(
            req["cseq"], 200, "OK",
            extra_headers={
                "Session": self._session_id or "0",
                "RTP-Info": f"url={self.presentation_url}/streamid=0",
            },
        )
        logger.info(
            "Streaming started! RTP video → port %d", self._rtp_port
        )
        if self.on_start:
            try:
                self.on_start(
                    self._rtp_port,
                    self.negotiated_video_format,
                    self.negotiated_audio_codecs,
                )
            except Exception as exc:
                logger.error("on_session_start callback error: %s", exc)

    def _handle_pause(self, req: dict) -> None:
        self._send_response(
            req["cseq"], 200, "OK",
            extra_headers={"Session": self._session_id or "0"},
        )

    def _handle_teardown(self, req: dict) -> None:
        self._send_response(
            req["cseq"], 200, "OK",
            extra_headers={"Session": self._session_id or "0"},
        )
        self._running = False

    # ─── Teardown ─────────────────────────────────────────────────────────────

    def _teardown(self) -> None:
        logger.info("RTSP session ended (%s)", self.addr)
        if self.on_end:
            try:
                self.on_end(self)
            except Exception as exc:
                logger.error("on_session_end callback error: %s", exc)
        try:
            self.conn.close()
        except Exception:
            pass

    # ─── RTSP I/O helpers ─────────────────────────────────────────────────────

    def _recv_request(self) -> dict | None:
        """Read one complete RTSP request from the socket."""
        while self._running:
            # Look for the end-of-headers marker
            idx = self._buf.find(b"\r\n\r\n")
            if idx != -1:
                header_bytes = self._buf[:idx]
                self._buf = self._buf[idx + 4:]
                return self._parse_request(header_bytes)
            chunk = self.conn.recv(4096)
            if not chunk:
                return None
            self._buf += chunk
        return None

    def _parse_request(self, header_bytes: bytes) -> dict:
        """Parse RTSP request headers into a dict."""
        lines = header_bytes.decode(errors="replace").splitlines()
        if not lines:
            return {}
        request_line = lines[0].split()
        if len(request_line) < 2:
            return {}

        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()

        cseq = int(headers.get("cseq", "0"))
        content_length = int(headers.get("content-length", "0"))

        body = ""
        if content_length > 0:
            while len(self._buf) < content_length:
                chunk = self.conn.recv(4096)
                if not chunk:
                    break
                self._buf += chunk
            body = self._buf[:content_length].decode(errors="replace")
            self._buf = self._buf[content_length:]

        return {
            "method": request_line[0],
            "uri": request_line[1] if len(request_line) > 1 else "*",
            "cseq": cseq,
            "headers": headers,
            "body": body,
        }

    def _send_response(
        self,
        cseq: int,
        status: int,
        reason: str,
        body: str = "",
        extra_headers: dict | None = None,
    ) -> None:
        """Serialise and send an RTSP response."""
        lines = [f"RTSP/1.0 {status} {reason}"]
        lines.append(f"CSeq: {cseq}")
        lines.append("Server: NicoCast/1.0")
        if extra_headers:
            for k, v in extra_headers.items():
                lines.append(f"{k}: {v}")
        if body:
            encoded = body.encode()
            lines.append(f"Content-Type: text/parameters")
            lines.append(f"Content-Length: {len(encoded)}")
            lines.append("")
            raw = "\r\n".join(lines).encode() + b"\r\n" + encoded
        else:
            lines.append("Content-Length: 0")
            lines.append("")
            raw = "\r\n".join(lines).encode() + b"\r\n"

        logger.debug("RTSP → %d %s (CSeq %d)", status, reason, cseq)
        try:
            self.conn.sendall(raw)
        except OSError as exc:
            logger.error("Failed to send RTSP response: %s", exc)

    # ─── Parsing helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_param(body: str, key: str) -> str | None:
        """Extract a parameter value from a WFD RTSP body."""
        for line in body.splitlines():
            if line.startswith(key + ":"):
                return line[len(key) + 1:].strip()
        return None

    @staticmethod
    def _parse_client_port(transport: str) -> int | None:
        """Extract the client_port from a Transport header."""
        m = re.search(r"client_port=(\d+)", transport)
        return int(m.group(1)) if m else None
