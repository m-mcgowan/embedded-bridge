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
