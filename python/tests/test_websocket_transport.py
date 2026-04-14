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
