"""Integration tests for WebSocketTransport against a real WebSocket server.

Stands up a localhost ``websockets.sync.server`` in a background thread and
drives ``WebSocketTransport`` against it.  These tests exercise the real
``websockets`` library rather than mocks, validating the wire behavior.
"""

import socket
import threading
import time
from collections.abc import Callable, Iterator

import pytest
from websockets.sync.server import serve

from embedded_bridge.transport.websocket import WebSocketTransport


def _run_server(handler: Callable) -> tuple:
    """Start a websockets sync server on a random port in a daemon thread.

    Returns (server, thread, uri).  Caller is responsible for shutdown.
    """
    server = serve(handler, "localhost", 0)
    port = server.socket.getsockname()[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Brief pause so the server is accepting connections before tests connect.
    time.sleep(0.02)
    return server, thread, f"ws://localhost:{port}"


def _stop_server(server, thread) -> None:
    server.shutdown()
    thread.join(timeout=2.0)


@pytest.fixture
def echo_uri() -> Iterator[str]:
    """Server that echoes every message back (preserving text/binary type)."""

    def handler(ws):
        try:
            for msg in ws:
                ws.send(msg)
        except Exception:
            pass

    server, thread, uri = _run_server(handler)
    try:
        yield uri
    finally:
        _stop_server(server, thread)


@pytest.fixture
def text_echo_uri() -> Iterator[str]:
    """Server that echoes every message back as a text frame (like bridge.py)."""

    def handler(ws):
        try:
            for msg in ws:
                if isinstance(msg, bytes):
                    msg = msg.decode("utf-8")
                ws.send(msg)
        except Exception:
            pass

    server, thread, uri = _run_server(handler)
    try:
        yield uri
    finally:
        _stop_server(server, thread)


# ── Round-trip ────────────────────────────────────────────────────────


class TestRoundTrip:
    def test_binary_frame_round_trip(self, echo_uri):
        with WebSocketTransport(echo_uri) as t:
            t.write(b"hello")
            assert t.read(timeout=2.0) == b"hello"

    def test_text_frame_decoded_as_utf8_bytes(self, text_echo_uri):
        """A server text frame arrives as UTF-8-encoded bytes (bridge.py pattern)."""
        with WebSocketTransport(text_echo_uri) as t:
            t.write(b'{"cmd":"ping"}')
            assert t.read(timeout=2.0) == b'{"cmd":"ping"}'

    def test_non_ascii_text_frame(self, text_echo_uri):
        with WebSocketTransport(text_echo_uri) as t:
            t.write("café → ☕".encode("utf-8"))
            assert t.read(timeout=2.0) == "café → ☕".encode("utf-8")

    def test_multiple_messages_in_sequence(self, echo_uri):
        with WebSocketTransport(echo_uri) as t:
            for payload in (b"first", b"second", b"third"):
                t.write(payload)
                assert t.read(timeout=2.0) == payload


# ── Buffering ─────────────────────────────────────────────────────────


class TestBuffering:
    def test_read_size_limit_buffers_remainder(self, echo_uri):
        """read(size=N) returns N bytes; remainder served from buffer."""
        with WebSocketTransport(echo_uri) as t:
            t.write(b"helloworld")

            first = t.read(size=5, timeout=2.0)
            assert first == b"hello"

            second = t.read(size=5, timeout=2.0)
            assert second == b"world"

    def test_large_payload(self, echo_uri):
        """Large payload survives the round trip intact."""
        payload = bytes(range(256)) * 40  # 10 KiB of varied bytes
        with WebSocketTransport(echo_uri) as t:
            t.write(payload)

            received = bytearray()
            deadline = time.monotonic() + 5.0
            while len(received) < len(payload):
                chunk = t.read(size=len(payload) - len(received), timeout=1.0)
                if chunk:
                    received.extend(chunk)
                if time.monotonic() > deadline:
                    break

            assert bytes(received) == payload


# ── Connection lifecycle ──────────────────────────────────────────────


class TestLifecycle:
    def test_connect_failure_on_unreachable_port(self):
        """Connecting to a port with no server raises ConnectionError."""
        # Bind a socket briefly to claim a port, then release it so we know
        # nothing is listening there.
        with socket.socket() as s:
            s.bind(("localhost", 0))
            port = s.getsockname()[1]

        transport = WebSocketTransport(
            f"ws://localhost:{port}", open_timeout=1.0
        )
        with pytest.raises(ConnectionError, match="Failed to connect"):
            transport.connect()

    def test_server_close_raises_connection_error(self):
        """Server closes the connection → read raises ConnectionError (no reconnect)."""

        def handler(ws):
            ws.close()

        server, thread, uri = _run_server(handler)
        try:
            t = WebSocketTransport(uri)
            t.connect()
            with pytest.raises(ConnectionError):
                t.read(timeout=2.0)
            t.disconnect()
        finally:
            _stop_server(server, thread)

    def test_read_timeout_with_idle_server(self, echo_uri):
        """With a real server that sends nothing, read(timeout) returns b''."""
        with WebSocketTransport(echo_uri) as t:
            data = t.read(timeout=0.2)
            assert data == b""
