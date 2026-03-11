"""Tests for nicocast.rtsp_handler module."""

import socket
import threading
import time
import pytest

from nicocast.config import Config
from nicocast.rtsp_handler import RTSPServer, RTSPSession


def _make_config(*section_key_values):
    """Return a Config instance using only built-in defaults (no file).

    Pass overrides as (section, key, value) tuples.
    """
    cfg = Config(path="/tmp/nonexistent_rtsp_test.conf")
    for section, key, value in section_key_values:
        cfg.set(section, key, value)
    return cfg


class TestRTSPSession:
    """Unit-test the RTSP session parser and response builder."""

    def _make_session(self, recv_data: bytes) -> tuple["RTSPSession", socket.socket]:
        """Create a session connected to a pair of sockets."""
        server_sock, client_sock = socket.socketpair()
        cfg = _make_config()
        session = RTSPSession(server_sock, ("127.0.0.1", 12345), cfg)
        # Pre-fill the buffer with data
        session._buf = recv_data
        return session, client_sock

    def _read_response(self, sock: socket.socket, timeout: float = 2.0) -> str:
        sock.settimeout(timeout)
        chunks = []
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        except socket.timeout:
            pass
        return b"".join(chunks).decode(errors="replace")

    def test_parse_options_request(self):
        raw = (
            b"OPTIONS * RTSP/1.0\r\n"
            b"CSeq: 1\r\n"
            b"Require: org.wfa.wfd1.0\r\n"
            b"\r\n"
        )
        session, _ = self._make_session(raw)
        req = session._recv_request()
        assert req is not None
        assert req["method"] == "OPTIONS"
        assert req["cseq"] == 1

    def test_options_response_contains_public(self):
        raw = (
            b"OPTIONS * RTSP/1.0\r\n"
            b"CSeq: 1\r\n"
            b"Require: org.wfa.wfd1.0\r\n"
            b"\r\n"
        )
        session, client = self._make_session(raw)
        req = session._recv_request()
        session._handle_options(req)
        resp = self._read_response(client)
        assert "200 OK" in resp
        assert "Public:" in resp
        assert "GET_PARAMETER" in resp

    def test_get_parameter_returns_rtp_ports(self):
        body = b"wfd_client_rtp_ports\r\n"
        raw = (
            b"GET_PARAMETER rtsp://localhost/wfd1.0 RTSP/1.0\r\n"
            b"CSeq: 2\r\n"
            b"Content-Type: text/parameters\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"\r\n"
            + body
        )
        session, client = self._make_session(raw)
        req = session._recv_request()
        session._handle_get_parameter(req)
        resp = self._read_response(client)
        assert "200 OK" in resp
        assert "wfd_client_rtp_ports" in resp
        assert "1028" in resp

    def test_get_parameter_returns_video_formats(self):
        body = b"wfd_video_formats\r\n"
        raw = (
            b"GET_PARAMETER rtsp://localhost/wfd1.0 RTSP/1.0\r\n"
            b"CSeq: 3\r\n"
            b"Content-Type: text/parameters\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"\r\n"
            + body
        )
        session, client = self._make_session(raw)
        req = session._recv_request()
        session._handle_get_parameter(req)
        resp = self._read_response(client)
        assert "wfd_video_formats" in resp

    def test_setup_assigns_session_id(self):
        raw = (
            b"SETUP rtsp://localhost/wfd1.0/streamid=0 RTSP/1.0\r\n"
            b"CSeq: 4\r\n"
            b"Transport: RTP/AVP/UDP;unicast;client_port=1028;server_port=1028\r\n"
            b"\r\n"
        )
        session, client = self._make_session(raw)
        req = session._recv_request()
        session._handle_setup(req)
        assert session._session_id is not None
        resp = self._read_response(client)
        assert "200 OK" in resp
        assert "Session:" in resp

    def test_play_triggers_on_start_callback(self):
        cfg = _make_config()
        server_sock, client_sock = socket.socketpair()
        called = {}

        def on_start(rtp_port, vfmt, afmt):
            called["rtp_port"] = rtp_port

        session = RTSPSession(
            server_sock, ("127.0.0.1", 0), cfg, on_start=on_start
        )
        session._session_id = "test123"
        raw = (
            b"PLAY rtsp://localhost/wfd1.0 RTSP/1.0\r\n"
            b"CSeq: 5\r\n"
            b"Session: test123\r\n"
            b"\r\n"
        )
        session._buf = raw
        req = session._recv_request()
        session._handle_play(req)
        assert called.get("rtp_port") == 1028

    def test_teardown_stops_session(self):
        raw = (
            b"TEARDOWN rtsp://localhost/wfd1.0 RTSP/1.0\r\n"
            b"CSeq: 6\r\n"
            b"Session: abc\r\n"
            b"\r\n"
        )
        session, client = self._make_session(raw)
        session._session_id = "abc"
        req = session._recv_request()
        session._handle_teardown(req)
        assert session._running is False

    def test_parse_param_extracts_value(self):
        body = "wfd_trigger_method: SETUP\r\n"
        result = RTSPSession._parse_param(body, "wfd_trigger_method")
        assert result == "SETUP"

    def test_parse_param_missing_returns_none(self):
        body = "wfd_video_formats: 00 00 02 04\r\n"
        result = RTSPSession._parse_param(body, "wfd_trigger_method")
        assert result is None

    def test_parse_client_port(self):
        transport = "RTP/AVP/UDP;unicast;client_port=1028-1029;server_port=1028"
        assert RTSPSession._parse_client_port(transport) == 1028

    def test_set_parameter_trigger_responds_ok(self):
        body = b"wfd_trigger_method: SETUP\r\n"
        raw = (
            b"SET_PARAMETER rtsp://localhost/wfd1.0 RTSP/1.0\r\n"
            b"CSeq: 7\r\n"
            b"Content-Type: text/parameters\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"\r\n"
            + body
        )
        session, client = self._make_session(raw)
        req = session._recv_request()
        session._handle_set_parameter(req)
        resp = self._read_response(client)
        assert "200 OK" in resp

    def test_unknown_method_returns_501(self):
        raw = (
            b"DESCRIBE rtsp://localhost/wfd1.0 RTSP/1.0\r\n"
            b"CSeq: 8\r\n"
            b"\r\n"
        )
        session, client = self._make_session(raw)
        req = session._recv_request()
        session._dispatch(req)
        resp = self._read_response(client)
        assert "501" in resp


class TestRTSPServerLifecycle:
    """Verify the RTSPServer start/stop lifecycle."""

    def test_server_starts_and_accepts_tcp_connection(self):
        cfg = _make_config(
            ("miracast", "rtsp_port", "17236")  # non-privileged port for tests
        )
        srv = RTSPServer(cfg)
        srv.start()
        try:
            # Should be able to connect
            s = socket.create_connection(("127.0.0.1", 17236), timeout=2)
            s.close()
        finally:
            srv.stop()

    def test_server_stop_is_idempotent(self):
        cfg = _make_config(("miracast", "rtsp_port", "17237"))
        srv = RTSPServer(cfg)
        srv.start()
        srv.stop()
        srv.stop()  # Should not raise
