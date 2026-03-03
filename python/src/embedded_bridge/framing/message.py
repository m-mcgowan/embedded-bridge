"""MessageReader / MessageWriter — application-layer message protocol.

Wire protocol::

    printable text...\\n                          ← text message
    \\x01\\x01<varint length><payload bytes>       ← binary message

SOH (0x01) signals "binary message follows".  The next byte is a protocol
version (currently 1), then a varint-encoded length, then exactly that many
payload bytes.  Any other leading byte starts a text message that ends at
``\\n``.

Three consumption tiers (reader):

1. **drain()** — poll for complete ``str`` / ``bytes`` messages
2. **MessageHandler subclass** — callbacks with complete messages
3. **StreamingMessageHandler subclass** — chunk-level callbacks for
   zero-copy / low-memory processing

Two production tiers (writer):

1. **write_text() / write_binary()** — whole messages, returns bytes
2. **begin_text() / begin_binary() / write() / end()** — streaming
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum, auto
from typing import Protocol, runtime_checkable

SOH: int = 0x01
BINARY_PROTOCOL_V1: int = 0x01

# Maximum varint bytes (5 bytes = up to 4 GB, matching protobuf uint32)
_VARINT_MAX_BYTES = 5


# ---------------------------------------------------------------------------
# Streaming handler protocol (tier 3)
# ---------------------------------------------------------------------------


class StreamingMessageHandler:
    """Override to handle message data as it streams in.

    Text and binary messages share the same lifecycle::

        on_text_data(chunk)   ...   on_text_end()
        on_binary_start(len)  on_binary_data(chunk) ...  on_binary_end()

    Default implementations do nothing — override what you need.
    """

    def on_text_data(self, chunk: str) -> None:
        """Text bytes decoded as UTF-8, delivered as they arrive."""

    def on_text_end(self) -> None:
        """End of text message (``\\n`` received)."""

    def on_binary_start(self, length: int) -> None:
        """Binary message header received; *length* payload bytes will follow."""

    def on_binary_data(self, chunk: bytes) -> None:
        """A chunk of binary payload data."""

    def on_binary_end(self) -> None:
        """All payload bytes for the current binary message have been delivered."""


# ---------------------------------------------------------------------------
# Buffering handler (tier 2)
# ---------------------------------------------------------------------------


class MessageHandler(StreamingMessageHandler):
    """Override to handle complete messages.

    Buffers streaming chunks internally and delivers whole messages
    via :meth:`on_text` and :meth:`on_binary`.
    """

    def __init__(self) -> None:
        self._text_buf: list[str] = []
        self._binary_buf = bytearray()

    def on_text(self, line: str) -> None:
        """Called with a complete text line (no trailing newline)."""

    def on_binary(self, payload: bytes) -> None:
        """Called with a complete binary payload."""

    # -- StreamingMessageHandler plumbing --

    def on_text_data(self, chunk: str) -> None:
        self._text_buf.append(chunk)

    def on_text_end(self) -> None:
        line = "".join(self._text_buf)
        self._text_buf.clear()
        if line.endswith("\r"):
            line = line[:-1]
        self.on_text(line)

    def on_binary_start(self, length: int) -> None:
        self._binary_buf.clear()

    def on_binary_data(self, chunk: bytes) -> None:
        self._binary_buf.extend(chunk)

    def on_binary_end(self) -> None:
        self.on_binary(bytes(self._binary_buf))
        self._binary_buf.clear()


# ---------------------------------------------------------------------------
# Drain handler (tier 1) — used internally by MessageReader.drain()
# ---------------------------------------------------------------------------


class _DrainHandler(MessageHandler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[bytes | str] = []

    def on_text(self, line: str) -> None:
        self.messages.append(line)

    def on_binary(self, payload: bytes) -> None:
        self.messages.append(payload)


# ---------------------------------------------------------------------------
# MessageReader
# ---------------------------------------------------------------------------


class _State(Enum):
    IDLE = auto()
    TEXT = auto()
    VERSION = auto()
    LENGTH = auto()
    PAYLOAD = auto()


class MessageReader:
    """Parses a byte stream into text and binary messages.

    The reader dispatches to a :class:`StreamingMessageHandler` (or subclass)
    as data arrives.  For simple use, omit the handler and use :meth:`drain`
    to poll for complete messages.

    Parameters
    ----------
    handler:
        Optional handler for streaming or buffered callbacks.  If ``None``,
        an internal buffering handler is used and messages are available
        via :meth:`drain`.
    """

    def __init__(
        self,
        handler: StreamingMessageHandler | None = None,
    ) -> None:
        if handler is None:
            self._drain_handler = _DrainHandler()
            self._handler: StreamingMessageHandler = self._drain_handler
        else:
            self._drain_handler = None
            self._handler = handler
        self._state = _State.IDLE
        self._varint_value = 0
        self._varint_shift = 0
        self._varint_bytes_read = 0
        self._payload_remaining = 0

    def feed(self, data: bytes) -> None:
        """Feed raw bytes from the transport."""
        i = 0
        n = len(data)
        while i < n:
            c = data[i]

            if self._state is _State.IDLE:
                if c == SOH:
                    self._state = _State.VERSION
                    i += 1
                elif c == ord("\n"):
                    # Empty text message
                    self._handler.on_text_end()
                    i += 1
                else:
                    # Start of text — find extent of text chunk
                    self._state = _State.TEXT
                    i = self._feed_text(data, i)

            elif self._state is _State.TEXT:
                i = self._feed_text(data, i)

            elif self._state is _State.VERSION:
                if c == BINARY_PROTOCOL_V1:
                    self._state = _State.LENGTH
                    self._varint_value = 0
                    self._varint_shift = 0
                    self._varint_bytes_read = 0
                else:
                    # Unknown version — discard and return to idle
                    self._state = _State.IDLE
                i += 1

            elif self._state is _State.LENGTH:
                self._process_varint_byte(c)
                i += 1

            elif self._state is _State.PAYLOAD:
                i = self._feed_payload(data, i)

    def drain(self) -> list[bytes | str]:
        """Return complete messages accumulated since the last drain.

        Only works when no explicit handler was provided.
        """
        if self._drain_handler is None:
            raise RuntimeError(
                "drain() is not available when a handler is provided"
            )
        messages = self._drain_handler.messages
        self._drain_handler.messages = []
        return messages

    def reset(self) -> None:
        self._state = _State.IDLE
        self._varint_value = 0
        self._varint_shift = 0
        self._varint_bytes_read = 0
        self._payload_remaining = 0
        if self._drain_handler is not None:
            self._drain_handler.messages.clear()
            self._drain_handler._text_buf.clear()
            self._drain_handler._binary_buf.clear()

    def _feed_text(self, data: bytes, start: int) -> int:
        """Deliver text data up to the next newline or SOH. Returns new index."""
        n = len(data)
        i = start
        while i < n:
            c = data[i]
            if c == ord("\n"):
                # Deliver any text bytes before the newline
                if i > start:
                    chunk = data[start:i].decode("utf-8", errors="replace")
                    self._handler.on_text_data(chunk)
                self._handler.on_text_end()
                self._state = _State.IDLE
                return i + 1
            elif c == SOH:
                # Deliver text bytes before SOH, then switch
                if i > start:
                    chunk = data[start:i].decode("utf-8", errors="replace")
                    self._handler.on_text_data(chunk)
                self._handler.on_text_end()
                self._state = _State.VERSION
                return i + 1
            i += 1

        # No delimiter found — deliver what we have as a chunk
        if i > start:
            chunk = data[start:i].decode("utf-8", errors="replace")
            self._handler.on_text_data(chunk)
        return i

    def _feed_payload(self, data: bytes, start: int) -> int:
        """Deliver binary payload data. Returns new index."""
        available = len(data) - start
        take = min(available, self._payload_remaining)
        if take > 0:
            chunk = data[start : start + take]
            self._handler.on_binary_data(chunk)
            self._payload_remaining -= take
        if self._payload_remaining == 0:
            self._handler.on_binary_end()
            self._state = _State.IDLE
        return start + take

    def _process_varint_byte(self, c: int) -> None:
        self._varint_value |= (c & 0x7F) << self._varint_shift
        self._varint_shift += 7
        self._varint_bytes_read += 1

        if (c & 0x80) == 0:
            length = self._varint_value
            self._handler.on_binary_start(length)
            if length == 0:
                self._handler.on_binary_end()
                self._state = _State.IDLE
            else:
                self._payload_remaining = length
                self._state = _State.PAYLOAD
        elif self._varint_bytes_read >= _VARINT_MAX_BYTES:
            self._state = _State.IDLE


# ---------------------------------------------------------------------------
# MessageWriter
# ---------------------------------------------------------------------------


class MessageWriter:
    """Formats text and binary messages into wire-protocol bytes.

    **Convenience API** (whole messages)::

        wire = writer.write_text("hello")
        wire = writer.write_binary(payload)

    **Streaming API** (chunked)::

        writer.begin_text()
        writer.write(b"hel")
        writer.write(b"lo")
        wire = writer.end()

        writer.begin_binary(1024)
        writer.write(chunk1)
        writer.write(chunk2)
        wire = writer.end()

    If constructed with an ``output`` callable, formatted bytes are sent
    there directly instead of being returned.
    """

    def __init__(self, output: Callable[[bytes], None] | None = None) -> None:
        self._output = output
        self._buf = bytearray()
        self._in_message = False

    # -- Convenience API (whole messages) --

    def write_text(self, line: str) -> bytes:
        """Format a text message. Returns wire bytes."""
        wire = line.encode("utf-8") + b"\n"
        if self._output is not None:
            self._output(wire)
        return wire

    def write_binary(self, payload: bytes) -> bytes:
        """Format a binary message. Returns wire bytes."""
        header = bytes([SOH, BINARY_PROTOCOL_V1]) + encode_varint(len(payload))
        wire = header + payload
        if self._output is not None:
            self._output(wire)
        return wire

    # -- Streaming API --

    def begin_text(self) -> None:
        """Start a streaming text message."""
        if self._in_message:
            raise RuntimeError("already in a message")
        self._in_message = True
        self._buf.clear()

    def begin_binary(self, length: int) -> None:
        """Start a streaming binary message of *length* payload bytes."""
        if self._in_message:
            raise RuntimeError("already in a message")
        self._in_message = True
        self._buf.clear()
        header = bytes([SOH, BINARY_PROTOCOL_V1]) + encode_varint(length)
        self._buf.extend(header)

    def write(self, data: bytes) -> None:
        """Append data to the current streaming message."""
        if not self._in_message:
            raise RuntimeError("not in a message — call begin_text/begin_binary first")
        self._buf.extend(data)

    def end(self) -> bytes:
        """Finish the current message. Returns wire bytes."""
        if not self._in_message:
            raise RuntimeError("not in a message")
        self._in_message = False
        # Text messages need trailing newline
        if len(self._buf) == 0 or self._buf[0] != SOH:
            self._buf.extend(b"\n")
        wire = bytes(self._buf)
        self._buf.clear()
        if self._output is not None:
            self._output(wire)
        return wire


# ---------------------------------------------------------------------------
# Varint helpers
# ---------------------------------------------------------------------------


def encode_varint(value: int) -> bytes:
    """Encode an unsigned integer as a protobuf-style varint (LEB128)."""
    if value < 0:
        raise ValueError("varint must be non-negative")
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0x7F)
    return bytes(out)


def decode_varint(data: bytes | bytearray) -> tuple[int, int]:
    """Decode a varint from a byte buffer.

    Returns ``(value, bytes_consumed)``.
    Raises ``ValueError`` on truncated or overlong varint.
    """
    value = 0
    shift = 0
    for i, b in enumerate(data):
        if i >= _VARINT_MAX_BYTES:
            raise ValueError("varint too long")
        value |= (b & 0x7F) << shift
        shift += 7
        if (b & 0x80) == 0:
            return value, i + 1
    raise ValueError("truncated varint")
