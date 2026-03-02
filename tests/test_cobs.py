"""Tests for COBS framing — shared test vectors with C++ implementation."""

from embedded_bridge.framing.cobs import (
    CobsFrameEncoder,
    CobsFramer,
    cobs_decode,
    cobs_encode,
)


class FrameCapture:
    def __init__(self):
        self.frames: list[bytes] = []

    def __call__(self, data: bytes) -> None:
        self.frames.append(data)


def test_encode_no_zeros():
    """Payload without zeros: single code byte + data."""
    encoded = cobs_encode(b"Hi")
    assert encoded == bytes([0x03, ord("H"), ord("i")])


def test_encode_with_zeros():
    """Payload with embedded zeros."""
    encoded = cobs_encode(bytes([0x00]))
    assert encoded == bytes([0x01, 0x01])

    encoded = cobs_encode(bytes([0x01, 0x00, 0x02]))
    assert encoded == bytes([0x02, 0x01, 0x02, 0x02])


def test_encode_empty():
    """Empty payload encodes to single code byte."""
    encoded = cobs_encode(b"")
    assert encoded == bytes([0x01])


def test_decode_roundtrip():
    for payload in [b"", b"A", b"\x00", b"Hello", bytes([0, 1, 0, 2, 0])]:
        encoded = cobs_encode(payload)
        decoded = cobs_decode(encoded)
        assert decoded == payload, f"roundtrip failed for {payload!r}"


def test_decode_invalid_zero():
    """Zero as a code byte is invalid."""
    assert cobs_decode(bytes([0x00])) is None


def test_decode_truncated():
    """Truncated data → None."""
    assert cobs_decode(bytes([0x05, 0x01, 0x02])) is None


def test_frame_roundtrip():
    cap = FrameCapture()
    framer = CobsFramer(cap)

    framer.process_bytes(CobsFrameEncoder.encode(b"Hi"))

    assert len(cap.frames) == 1
    assert cap.frames[0] == b"Hi"


def test_frame_with_zeros():
    cap = FrameCapture()
    framer = CobsFramer(cap)

    payload = bytes([0x00, 0x01, 0x00, 0x02, 0x00])
    framer.process_bytes(CobsFrameEncoder.encode(payload))

    assert len(cap.frames) == 1
    assert cap.frames[0] == payload


def test_multiple_frames():
    cap = FrameCapture()
    framer = CobsFramer(cap)

    wire = CobsFrameEncoder.encode(b"one") + CobsFrameEncoder.encode(b"two")
    framer.process_bytes(wire)

    assert len(cap.frames) == 2
    assert cap.frames[0] == b"one"
    assert cap.frames[1] == b"two"


def test_buffer_overflow_recovers():
    cap = FrameCapture()
    framer = CobsFramer(cap, buf_size=8)

    # Overflow: send lots of non-zero bytes without delimiter
    for _ in range(20):
        framer.process_byte(0x41)
    framer.process_byte(0x00)  # delimiter — discarded due to error

    # Next frame works
    framer.process_bytes(CobsFrameEncoder.encode(b"\x42"))

    assert len(cap.frames) == 1
    assert cap.frames[0] == b"\x42"


def test_reset_clears_state():
    cap = FrameCapture()
    framer = CobsFramer(cap)

    framer.process_byte(0x41)
    framer.process_byte(0x42)
    framer.reset()

    framer.process_bytes(CobsFrameEncoder.encode(b"ok"))

    assert len(cap.frames) == 1
    assert cap.frames[0] == b"ok"


def test_cross_language_vector():
    """Encode in Python, verify raw bytes match C++ expected output."""
    encoded = CobsFrameEncoder.encode(b"A")
    # COBS("A") = [0x02, 0x41] + 0x00 delimiter
    assert encoded == bytes([0x02, ord("A"), 0x00])

    cap = FrameCapture()
    framer = CobsFramer(cap)
    framer.process_bytes(encoded)
    assert cap.frames[0] == b"A"
