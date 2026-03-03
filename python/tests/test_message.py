"""Tests for MessageReader / MessageWriter — three-tier message protocol."""

import pytest

from embedded_bridge.framing.message import (
    BINARY_PROTOCOL_V1,
    SOH,
    MessageHandler,
    MessageReader,
    MessageWriter,
    StreamingMessageHandler,
    decode_varint,
    encode_varint,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def encode_bin(payload: bytes) -> bytes:
    """Shorthand: SOH + version + varint length + payload."""
    return (
        bytes([SOH, BINARY_PROTOCOL_V1])
        + encode_varint(len(payload))
        + payload
    )


# ---------------------------------------------------------------------------
# Tier 1: drain() — poll for complete messages
# ---------------------------------------------------------------------------


class TestDrain:
    def test_text_lines(self):
        r = MessageReader()
        r.feed(b"hello\nworld\n")
        assert r.drain() == ["hello", "world"]

    def test_crlf_normalization(self):
        r = MessageReader()
        r.feed(b"hello\r\nworld\r\n")
        assert r.drain() == ["hello", "world"]

    def test_empty_text_line(self):
        r = MessageReader()
        r.feed(b"\n")
        assert r.drain() == [""]

    def test_binary_message(self):
        r = MessageReader()
        r.feed(encode_bin(b"payload"))
        assert r.drain() == [b"payload"]

    def test_empty_binary(self):
        r = MessageReader()
        r.feed(encode_bin(b""))
        assert r.drain() == [b""]

    def test_text_binary_interleaved(self):
        r = MessageReader()
        wire = (
            b"line1\n"
            + encode_bin(b"bin1")
            + b"line2\n"
            + encode_bin(b"bin2")
            + b"line3\n"
        )
        r.feed(wire)
        assert r.drain() == ["line1", b"bin1", "line2", b"bin2", "line3"]

    def test_multiple_binary(self):
        r = MessageReader()
        r.feed(encode_bin(b"a") + encode_bin(b"b"))
        assert r.drain() == [b"a", b"b"]

    def test_split_across_feeds(self):
        r = MessageReader()
        payload = b"chunked"
        full = encode_bin(payload)
        mid = len(full) // 2
        r.feed(full[:mid])
        assert r.drain() == []
        r.feed(full[mid:])
        assert r.drain() == [payload]

    def test_text_split_across_feeds(self):
        r = MessageReader()
        r.feed(b"hel")
        assert r.drain() == []
        r.feed(b"lo\n")
        assert r.drain() == ["hello"]

    def test_varint_split(self):
        r = MessageReader()
        payload = b"A" * 200
        full = encode_bin(payload)
        r.feed(full[:3])
        assert r.drain() == []
        r.feed(full[3:])
        assert r.drain() == [payload]

    def test_byte_at_a_time(self):
        r = MessageReader()
        full = b"text\n" + encode_bin(b"bin") + b"more\n"
        for b in full:
            r.feed(bytes([b]))
        assert r.drain() == ["text", b"bin", "more"]

    def test_large_payload(self):
        r = MessageReader()
        payload = b"X" * 16384
        r.feed(encode_bin(payload))
        assert r.drain() == [payload]

    def test_payload_containing_soh(self):
        r = MessageReader()
        payload = bytes([SOH, SOH, 0x42])
        r.feed(encode_bin(payload))
        assert r.drain() == [payload]

    def test_payload_containing_newlines(self):
        r = MessageReader()
        payload = b"a\nb\nc\n"
        r.feed(encode_bin(payload))
        assert r.drain() == [payload]

    def test_all_byte_values(self):
        r = MessageReader()
        payload = bytes(range(256))
        r.feed(encode_bin(payload))
        assert r.drain() == [payload]

    def test_unknown_version_recovery(self):
        r = MessageReader()
        r.feed(bytes([SOH, 0xFF]) + b"text\n")
        messages = r.drain()
        assert len(messages) == 1
        assert messages[0].endswith("text")

    def test_reset(self):
        r = MessageReader()
        r.feed(bytes([SOH]))
        r.reset()
        r.feed(b"fresh\n")
        assert r.drain() == ["fresh"]

    def test_reset_mid_payload(self):
        r = MessageReader()
        r.feed(encode_bin(b"long payload")[:5])
        r.reset()
        r.feed(b"clean\n")
        assert r.drain() == ["clean"]

    def test_drain_clears(self):
        r = MessageReader()
        r.feed(b"a\n")
        assert r.drain() == ["a"]
        assert r.drain() == []

    def test_soh_terminates_text(self):
        """SOH after text chars delivers the text, then starts binary."""
        r = MessageReader()
        r.feed(b"partial" + encode_bin(b"bin") + b"\n")
        messages = r.drain()
        # "partial" is emitted as text (SOH acts as implicit line end)
        # then binary, then empty line from trailing \n
        assert messages[0] == "partial"
        assert messages[1] == b"bin"


# ---------------------------------------------------------------------------
# Tier 2: MessageHandler — callbacks with complete messages
# ---------------------------------------------------------------------------


class Capture(MessageHandler):
    def __init__(self):
        super().__init__()
        self.texts: list[str] = []
        self.binaries: list[bytes] = []

    def on_text(self, line: str) -> None:
        self.texts.append(line)

    def on_binary(self, payload: bytes) -> None:
        self.binaries.append(payload)


class TestMessageHandler:
    def test_text(self):
        cap = Capture()
        r = MessageReader(cap)
        r.feed(b"hello\nworld\n")
        assert cap.texts == ["hello", "world"]

    def test_binary(self):
        cap = Capture()
        r = MessageReader(cap)
        r.feed(encode_bin(b"data"))
        assert cap.binaries == [b"data"]

    def test_interleaved(self):
        cap = Capture()
        r = MessageReader(cap)
        r.feed(b"text\n" + encode_bin(b"bin") + b"more\n")
        assert cap.texts == ["text", "more"]
        assert cap.binaries == [b"bin"]

    def test_drain_raises_with_handler(self):
        cap = Capture()
        r = MessageReader(cap)
        with pytest.raises(RuntimeError, match="drain.*not available"):
            r.drain()


# ---------------------------------------------------------------------------
# Tier 3: StreamingMessageHandler — chunk-level callbacks
# ---------------------------------------------------------------------------


class StreamCapture(StreamingMessageHandler):
    def __init__(self):
        self.events: list[tuple] = []

    def on_text_data(self, chunk: str) -> None:
        self.events.append(("text_data", chunk))

    def on_text_end(self) -> None:
        self.events.append(("text_end",))

    def on_binary_start(self, length: int) -> None:
        self.events.append(("binary_start", length))

    def on_binary_data(self, chunk: bytes) -> None:
        self.events.append(("binary_data", chunk))

    def on_binary_end(self) -> None:
        self.events.append(("binary_end",))


class TestStreamingHandler:
    def test_text_streaming(self):
        cap = StreamCapture()
        r = MessageReader(cap)
        r.feed(b"hel")
        r.feed(b"lo\n")
        assert cap.events == [
            ("text_data", "hel"),
            ("text_data", "lo"),
            ("text_end",),
        ]

    def test_binary_streaming(self):
        cap = StreamCapture()
        r = MessageReader(cap)
        payload = b"ABCDEF"
        full = encode_bin(payload)
        mid = len(full) - 3  # split in the payload
        r.feed(full[:mid])
        r.feed(full[mid:])
        # Should get binary_start, then two binary_data chunks, then binary_end
        assert cap.events[0] == ("binary_start", 6)
        assert cap.events[-1] == ("binary_end",)
        # Reassemble data chunks
        data_chunks = [e[1] for e in cap.events if e[0] == "binary_data"]
        assert b"".join(data_chunks) == payload

    def test_empty_binary_streaming(self):
        cap = StreamCapture()
        r = MessageReader(cap)
        r.feed(encode_bin(b""))
        assert cap.events == [
            ("binary_start", 0),
            ("binary_end",),
        ]

    def test_large_binary_single_feed(self):
        """Large payload in one feed — delivered as one chunk."""
        cap = StreamCapture()
        r = MessageReader(cap)
        payload = b"X" * 10000
        r.feed(encode_bin(payload))
        assert cap.events == [
            ("binary_start", 10000),
            ("binary_data", payload),
            ("binary_end",),
        ]

    def test_interleaved_streaming(self):
        cap = StreamCapture()
        r = MessageReader(cap)
        r.feed(b"hi\n" + encode_bin(b"AB") + b"bye\n")
        assert cap.events == [
            ("text_data", "hi"),
            ("text_end",),
            ("binary_start", 2),
            ("binary_data", b"AB"),
            ("binary_end",),
            ("text_data", "bye"),
            ("text_end",),
        ]


# ---------------------------------------------------------------------------
# MessageWriter — convenience API
# ---------------------------------------------------------------------------


class TestWriterConvenience:
    def test_write_text(self):
        w = MessageWriter()
        wire = w.write_text("hello")
        assert wire == b"hello\n"

    def test_write_binary(self):
        w = MessageWriter()
        wire = w.write_binary(b"data")
        assert wire[0] == SOH
        assert wire[1] == BINARY_PROTOCOL_V1
        length, consumed = decode_varint(wire[2:])
        assert length == 4
        assert wire[2 + consumed:] == b"data"

    def test_roundtrip(self):
        w = MessageWriter()
        r = MessageReader()
        r.feed(w.write_text("hello"))
        r.feed(w.write_binary(b"world"))
        assert r.drain() == ["hello", b"world"]

    def test_output_callback(self):
        sent: list[bytes] = []
        w = MessageWriter(output=sent.append)
        w.write_text("hi")
        w.write_binary(b"bin")
        assert len(sent) == 2
        assert sent[0] == b"hi\n"


# ---------------------------------------------------------------------------
# MessageWriter — streaming API
# ---------------------------------------------------------------------------


class TestWriterStreaming:
    def test_streaming_text(self):
        w = MessageWriter()
        w.begin_text()
        w.write(b"hel")
        w.write(b"lo")
        wire = w.end()
        assert wire == b"hello\n"

    def test_streaming_binary(self):
        w = MessageWriter()
        w.begin_binary(6)
        w.write(b"ABC")
        w.write(b"DEF")
        wire = w.end()

        r = MessageReader()
        r.feed(wire)
        assert r.drain() == [b"ABCDEF"]

    def test_streaming_roundtrip(self):
        w = MessageWriter()
        r = MessageReader()

        w.begin_text()
        w.write(b"line")
        r.feed(w.end())

        w.begin_binary(3)
        w.write(b"AB")
        w.write(b"C")
        r.feed(w.end())

        assert r.drain() == ["line", b"ABC"]

    def test_streaming_output_callback(self):
        sent: list[bytes] = []
        w = MessageWriter(output=sent.append)
        w.begin_text()
        w.write(b"hi")
        w.end()
        assert len(sent) == 1
        assert sent[0] == b"hi\n"

    def test_nested_begin_raises(self):
        w = MessageWriter()
        w.begin_text()
        with pytest.raises(RuntimeError, match="already in a message"):
            w.begin_text()

    def test_write_without_begin_raises(self):
        w = MessageWriter()
        with pytest.raises(RuntimeError, match="not in a message"):
            w.write(b"data")

    def test_end_without_begin_raises(self):
        w = MessageWriter()
        with pytest.raises(RuntimeError, match="not in a message"):
            w.end()


# ---------------------------------------------------------------------------
# Varint helpers
# ---------------------------------------------------------------------------


class TestVarint:
    def test_roundtrip(self):
        for value in [0, 1, 127, 128, 255, 300, 16383, 16384, 65535, 1_000_000]:
            encoded = encode_varint(value)
            decoded, consumed = decode_varint(encoded)
            assert decoded == value
            assert consumed == len(encoded)

    def test_encoding_sizes(self):
        assert len(encode_varint(0)) == 1
        assert len(encode_varint(127)) == 1
        assert len(encode_varint(128)) == 2
        assert len(encode_varint(16383)) == 2
        assert len(encode_varint(16384)) == 3
