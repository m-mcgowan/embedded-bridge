"""WebSocket transport for communicating with embedded devices over WebSocket.

Connects to a WebSocket server (e.g. a serial-to-WebSocket bridge) and
provides a synchronous byte-stream interface matching the Transport protocol.

Requires ``websockets>=12.0``::

    pip install embedded-bridge[websocket]

Usage::

    transport = WebSocketTransport("ws://localhost:8765")
    transport.connect()
    data = transport.read(timeout=1.0)
    transport.write(b'{\"cmd\":\"ping\"}\\n')
    transport.disconnect()
"""

import logging
import time

try:
    from websockets.sync.client import connect as ws_connect
    from websockets.exceptions import ConnectionClosed, WebSocketException
except ImportError as e:
    raise ImportError(
        "websockets is required for WebSocketTransport. "
        "Install with: pip install embedded-bridge[websocket]"
    ) from e

logger = logging.getLogger(__name__)


class WebSocketTransport:
    """WebSocket transport — bidirectional byte stream over WebSocket.

    Receives WebSocket messages (text or binary) and buffers them as raw
    bytes, presenting a byte-stream interface consistent with the Transport
    protocol.  Text frames are UTF-8 encoded before buffering.

    Args:
        uri: WebSocket URI (e.g. ``ws://localhost:8765``).
        open_timeout: Timeout in seconds for the opening handshake.
        reconnect: Whether to attempt reconnect on connection loss.
        reconnect_interval: Seconds between reconnect attempts.
        reconnect_timeout: Max seconds to wait for reconnect.
    """

    def __init__(
        self,
        uri: str,
        *,
        open_timeout: float = 10.0,
        reconnect: bool = False,
        reconnect_interval: float = 1.0,
        reconnect_timeout: float = 30.0,
    ) -> None:
        self._uri = uri
        self._open_timeout = open_timeout
        self._reconnect = reconnect
        self._reconnect_interval = reconnect_interval
        self._reconnect_timeout = reconnect_timeout
        self._ws = None
        self._buffer = bytearray()

    def connect(self) -> None:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError

    def read(self, size: int = -1, timeout: float | None = None) -> bytes:
        raise NotImplementedError

    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def is_connected(self) -> bool:
        raise NotImplementedError

    @property
    def port_path(self) -> str | None:
        return self._uri
