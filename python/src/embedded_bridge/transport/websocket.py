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
        """Open the WebSocket connection.

        Raises:
            ConnectionError: If the connection cannot be established.
        """
        if self._ws is not None:
            return

        logger.info("Connecting to %s", self._uri)
        try:
            self._ws = ws_connect(self._uri, open_timeout=self._open_timeout)
        except (WebSocketException, OSError, TimeoutError) as e:
            raise ConnectionError(
                f"Failed to connect to {self._uri}: {e}"
            ) from e

    def disconnect(self) -> None:
        """Close the WebSocket connection."""
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._buffer.clear()
        logger.info("Disconnected from %s", self._uri)

    def read(self, size: int = -1, timeout: float | None = None) -> bytes:
        """Read bytes from the WebSocket.

        WebSocket messages (text or binary) are buffered as raw bytes.
        Text frames are UTF-8 encoded.

        Args:
            size: Max bytes to read.  ``-1`` means read all available.
            timeout: Max seconds to wait for data.  ``None`` blocks
                indefinitely.  ``0`` returns immediately with whatever
                is buffered.

        Returns:
            Bytes read (may be empty if timeout expires with no data).

        Raises:
            ConnectionError: If not connected.
        """
        ws = self._ensure_connected()

        # Serve from buffer if available
        if self._buffer:
            return self._drain_buffer(size)

        # Nothing buffered — receive a WebSocket message
        if timeout is not None:
            deadline = time.monotonic() + timeout
        else:
            deadline = None

        while True:
            remaining = None
            if deadline is not None:
                remaining = max(0.0, deadline - time.monotonic())
                if remaining == 0.0 and not self._buffer:
                    return b""

            try:
                msg = ws.recv(timeout=remaining)
                if isinstance(msg, str):
                    self._buffer.extend(msg.encode("utf-8"))
                else:
                    self._buffer.extend(msg)
                return self._drain_buffer(size)

            except TimeoutError:
                return b""
            except ConnectionClosed as e:
                if self._reconnect:
                    logger.warning(
                        "Connection closed, attempting reconnect: %s", e
                    )
                    self._do_reconnect()
                    ws = self._ensure_connected()
                else:
                    raise ConnectionError(
                        f"WebSocket connection closed: {e}"
                    ) from e

    def _drain_buffer(self, size: int) -> bytes:
        """Extract bytes from the internal buffer."""
        if size == -1 or size >= len(self._buffer):
            data = bytes(self._buffer)
            self._buffer.clear()
            return data
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        return data

    def _ensure_connected(self):
        """Return the open WebSocket or raise ConnectionError."""
        if self._ws is None:
            raise ConnectionError("Not connected. Call connect() first.")
        return self._ws

    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def is_connected(self) -> bool:
        raise NotImplementedError

    @property
    def port_path(self) -> str | None:
        return self._uri
