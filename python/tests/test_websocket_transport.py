"""Tests for WebSocketTransport.

Uses unittest.mock to patch websockets — no real server needed.
"""

import pytest
from unittest.mock import MagicMock, patch

from embedded_bridge.transport.base import Transport
from embedded_bridge.transport.websocket import WebSocketTransport


# ── Protocol compliance ──────────────────────────────────────────────


class TestProtocol:
    def test_satisfies_transport_protocol(self):
        """WebSocketTransport satisfies the Transport protocol."""
        transport = WebSocketTransport("ws://localhost:8765")
        assert isinstance(transport, Transport)


# ── Connect / disconnect ─────────────────────────────────────────────


class TestConnectDisconnect:
    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_connect_opens_websocket(self, mock_connect):
        mock_ws = MagicMock()
        mock_connect.return_value = mock_ws

        transport = WebSocketTransport("ws://localhost:8765", open_timeout=5.0)
        transport.connect()

        mock_connect.assert_called_once_with(
            "ws://localhost:8765", open_timeout=5.0
        )

    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_connect_idempotent(self, mock_connect):
        """Calling connect() when already connected is a no-op."""
        mock_ws = MagicMock()
        mock_connect.return_value = mock_ws

        transport = WebSocketTransport("ws://localhost:8765")
        transport.connect()
        transport.connect()  # Second call should be no-op

        assert mock_connect.call_count == 1

    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_connect_failure_raises_connection_error(self, mock_connect):
        mock_connect.side_effect = OSError("Connection refused")

        transport = WebSocketTransport("ws://localhost:9999")
        with pytest.raises(ConnectionError, match="Connection refused"):
            transport.connect()

    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_disconnect_closes(self, mock_connect):
        mock_ws = MagicMock()
        mock_connect.return_value = mock_ws

        transport = WebSocketTransport("ws://localhost:8765")
        transport.connect()
        transport.disconnect()

        mock_ws.close.assert_called_once()

    def test_disconnect_when_not_connected(self):
        """Disconnect when not connected doesn't raise."""
        transport = WebSocketTransport("ws://localhost:8765")
        transport.disconnect()  # Should not raise

    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_disconnect_clears_buffer(self, mock_connect):
        mock_ws = MagicMock()
        mock_connect.return_value = mock_ws

        transport = WebSocketTransport("ws://localhost:8765")
        transport.connect()
        transport._buffer.extend(b"stale data")
        transport.disconnect()

        assert len(transport._buffer) == 0


# ── Read ─────────────────────────────────────────────────────────────


class TestRead:
    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_read_text_frame(self, mock_connect):
        """Text frames are decoded to bytes via UTF-8."""
        mock_ws = MagicMock()
        mock_ws.recv.return_value = '{"cmd":"ping"}'
        mock_connect.return_value = mock_ws

        transport = WebSocketTransport("ws://localhost:8765")
        transport.connect()
        data = transport.read(timeout=1.0)

        assert data == b'{"cmd":"ping"}'

    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_read_binary_frame(self, mock_connect):
        """Binary frames are returned as-is."""
        mock_ws = MagicMock()
        mock_ws.recv.return_value = b"\x01\x02\x03"
        mock_connect.return_value = mock_ws

        transport = WebSocketTransport("ws://localhost:8765")
        transport.connect()
        data = transport.read(timeout=1.0)

        assert data == b"\x01\x02\x03"

    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_read_with_size_limit(self, mock_connect):
        """read(size=N) returns at most N bytes, buffers the rest."""
        mock_ws = MagicMock()
        mock_ws.recv.return_value = b"hello world"
        mock_connect.return_value = mock_ws

        transport = WebSocketTransport("ws://localhost:8765")
        transport.connect()

        first = transport.read(size=5, timeout=1.0)
        assert first == b"hello"

        # Remaining bytes served from buffer without another recv
        second = transport.read(size=6, timeout=1.0)
        assert second == b" world"
        assert mock_ws.recv.call_count == 1  # Only one recv needed

    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_read_timeout_returns_empty(self, mock_connect):
        """Read returns empty bytes when timeout expires with no data."""
        mock_ws = MagicMock()
        mock_ws.recv.side_effect = TimeoutError
        mock_connect.return_value = mock_ws

        transport = WebSocketTransport("ws://localhost:8765")
        transport.connect()
        data = transport.read(timeout=0.1)

        assert data == b""

    def test_read_not_connected_raises(self):
        transport = WebSocketTransport("ws://localhost:8765")
        with pytest.raises(ConnectionError, match="Not connected"):
            transport.read(timeout=0)

    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_read_serves_buffer_before_recv(self, mock_connect):
        """Buffered data is returned without calling recv."""
        mock_ws = MagicMock()
        mock_connect.return_value = mock_ws

        transport = WebSocketTransport("ws://localhost:8765")
        transport.connect()
        transport._buffer.extend(b"buffered")

        data = transport.read(timeout=1.0)
        assert data == b"buffered"
        mock_ws.recv.assert_not_called()


# ── Write ────────────────────────────────────────────────────────────


class TestWrite:
    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_write_sends_data(self, mock_connect):
        mock_ws = MagicMock()
        mock_connect.return_value = mock_ws

        transport = WebSocketTransport("ws://localhost:8765")
        transport.connect()
        transport.write(b'{"cmd":"ping"}\n')

        mock_ws.send.assert_called_once_with(b'{"cmd":"ping"}\n')

    def test_write_not_connected_raises(self):
        transport = WebSocketTransport("ws://localhost:8765")
        with pytest.raises(ConnectionError, match="Not connected"):
            transport.write(b"data")

    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_write_connection_closed_raises(self, mock_connect):
        """Write raises ConnectionError when connection is lost (no reconnect)."""
        from websockets.exceptions import ConnectionClosed

        mock_ws = MagicMock()
        mock_ws.send.side_effect = ConnectionClosed(None, None)
        mock_connect.return_value = mock_ws

        transport = WebSocketTransport("ws://localhost:8765")
        transport.connect()

        with pytest.raises(ConnectionError, match="WebSocket write failed"):
            transport.write(b"data")
