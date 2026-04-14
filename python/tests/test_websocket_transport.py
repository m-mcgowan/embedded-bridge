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


# ── is_connected ─────────────────────────────────────────────────────


class TestIsConnected:
    def test_not_connected_initially(self):
        transport = WebSocketTransport("ws://localhost:8765")
        assert not transport.is_connected()

    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_connected_after_open(self, mock_connect):
        mock_ws = MagicMock()
        mock_connect.return_value = mock_ws

        transport = WebSocketTransport("ws://localhost:8765")
        transport.connect()
        assert transport.is_connected()

    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_not_connected_after_disconnect(self, mock_connect):
        mock_ws = MagicMock()
        mock_connect.return_value = mock_ws

        transport = WebSocketTransport("ws://localhost:8765")
        transport.connect()
        transport.disconnect()
        assert not transport.is_connected()


# ── port_path ────────────────────────────────────────────────────────


class TestPortPath:
    def test_port_path_returns_uri(self):
        transport = WebSocketTransport("ws://localhost:8765")
        assert transport.port_path == "ws://localhost:8765"


# ── Context manager ──────────────────────────────────────────────────


class TestContextManager:
    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_context_manager(self, mock_connect):
        mock_ws = MagicMock()
        mock_connect.return_value = mock_ws

        with WebSocketTransport("ws://localhost:8765") as transport:
            assert transport.port_path == "ws://localhost:8765"

        mock_ws.close.assert_called_once()


# ── repr ─────────────────────────────────────────────────────────────


class TestRepr:
    def test_repr_disconnected(self):
        transport = WebSocketTransport("ws://localhost:8765")
        r = repr(transport)
        assert "ws://localhost:8765" in r
        assert "disconnected" in r

    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_repr_connected(self, mock_connect):
        mock_ws = MagicMock()
        mock_connect.return_value = mock_ws

        transport = WebSocketTransport("ws://localhost:8765")
        transport.connect()
        r = repr(transport)
        assert "connected" in r


# ── Reconnect ────────────────────────────────────────────────────────


class TestReconnect:
    @patch("embedded_bridge.transport.websocket.time")
    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_reconnect_on_read_connection_closed(self, mock_connect, mock_time):
        """With reconnect=True, a ConnectionClosed during read triggers reconnect."""
        from websockets.exceptions import ConnectionClosed

        # First connection: recv raises ConnectionClosed
        mock_ws1 = MagicMock()
        mock_ws1.recv.side_effect = ConnectionClosed(None, None)

        # Second connection: recv succeeds
        mock_ws2 = MagicMock()
        mock_ws2.recv.return_value = b"hello"

        mock_connect.side_effect = [mock_ws1, mock_ws2]
        # monotonic calls:
        #   read(): deadline setup, remaining check (loop iter 1)
        #   _do_reconnect(): deadline setup, while-loop condition check
        #   read(): remaining check (loop iter 2, after reconnect)
        mock_time.monotonic.side_effect = [0.0, 0.0, 0.5, 0.5, 0.5]
        mock_time.sleep = MagicMock()

        transport = WebSocketTransport(
            "ws://localhost:8765",
            reconnect=True,
            reconnect_timeout=5.0,
        )
        transport.connect()
        data = transport.read(timeout=1.0)

        assert data == b"hello"
        assert mock_connect.call_count == 2

    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_no_reconnect_raises_on_read_connection_closed(self, mock_connect):
        """With reconnect=False, ConnectionClosed during read raises ConnectionError."""
        from websockets.exceptions import ConnectionClosed

        mock_ws = MagicMock()
        mock_ws.recv.side_effect = ConnectionClosed(None, None)
        mock_connect.return_value = mock_ws

        transport = WebSocketTransport("ws://localhost:8765")
        transport.connect()

        with pytest.raises(ConnectionError, match="WebSocket connection closed"):
            transport.read(timeout=1.0)

    @patch("embedded_bridge.transport.websocket.time")
    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_reconnect_on_write_connection_closed(self, mock_connect, mock_time):
        """With reconnect=True, a ConnectionClosed during write triggers reconnect."""
        from websockets.exceptions import ConnectionClosed

        # First connection: send raises ConnectionClosed
        mock_ws1 = MagicMock()
        mock_ws1.send.side_effect = ConnectionClosed(None, None)

        # Second connection: send succeeds
        mock_ws2 = MagicMock()
        mock_connect.side_effect = [mock_ws1, mock_ws2]
        mock_time.monotonic.side_effect = [0.0, 0.0, 0.5]
        mock_time.sleep = MagicMock()

        transport = WebSocketTransport(
            "ws://localhost:8765",
            reconnect=True,
            reconnect_timeout=5.0,
        )
        transport.connect()
        transport.write(b"data")

        mock_ws2.send.assert_called_once_with(b"data")

    @patch("embedded_bridge.transport.websocket.time")
    @patch("embedded_bridge.transport.websocket.ws_connect")
    def test_reconnect_timeout_exhausted(self, mock_connect, mock_time):
        """Reconnect gives up after timeout and raises ConnectionError."""
        from websockets.exceptions import ConnectionClosed

        mock_ws1 = MagicMock()
        mock_ws1.recv.side_effect = ConnectionClosed(None, None)
        mock_connect.side_effect = [
            mock_ws1,  # initial connect
            OSError("refused"),  # reconnect attempt 1
            OSError("refused"),  # reconnect attempt 2
        ]
        # monotonic: read deadline, reconnect deadline, attempt1, attempt2 (past deadline)
        mock_time.monotonic.side_effect = [0.0, 0.0, 0.5, 31.0]
        mock_time.sleep = MagicMock()

        transport = WebSocketTransport(
            "ws://localhost:8765",
            reconnect=True,
            reconnect_timeout=30.0,
        )
        transport.connect()

        with pytest.raises(ConnectionError, match="Failed to reconnect"):
            transport.read(timeout=1.0)
