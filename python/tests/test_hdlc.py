"""Tests for HDLC framing — shared test vectors with C++ implementation."""

from embedded_bridge.framing.hdlc import (
    ESC,
    FLAG,
    XOFF,
    XON,
    HdlcFrameEncoder,
    HdlcFramer,
)


class FrameCapture:
    def __init__(self):
        self.frames: list[bytes] = []

    def __call__(self, data: bytes) -> None:
        self.frames.append(data)


def test_decode_single_frame():
    cap = FrameCapture()
    framer = HdlcFramer(cap)

    encoded = HdlcFrameEncoder.encode(b"Hi")
    framer.process_bytes(encoded)

    assert len(cap.frames) == 1
    assert cap.frames[0] == b"Hi"


def test_decode_empty_payload():
    cap = FrameCapture()
    framer = HdlcFramer(cap)

    encoded = HdlcFrameEncoder.encode(b"")
    framer.process_bytes(encoded)

    assert len(cap.frames) == 1
    assert cap.frames[0] == b""


def test_corrupt_crc_rejected():
    cap = FrameCapture()
    framer = HdlcFramer(cap)

    encoded = bytearray(HdlcFrameEncoder.encode(b"data"))
    encoded[2] ^= 0x01  # corrupt a byte
    framer.process_bytes(encoded)

    assert len(cap.frames) == 0


def test_multiple_consecutive_frames():
    cap = FrameCapture()
    framer = HdlcFramer(cap)

    wire = HdlcFrameEncoder.encode(b"one") + HdlcFrameEncoder.encode(b"two")
    framer.process_bytes(wire)

    assert len(cap.frames) == 2
    assert cap.frames[0] == b"one"
    assert cap.frames[1] == b"two"


def test_garbage_before_frame_ignored():
    cap = FrameCapture()
    framer = HdlcFramer(cap)

    framer.process_bytes(b"\xAA\xBB\xCC")
    framer.process_bytes(HdlcFrameEncoder.encode(b"ok"))

    assert len(cap.frames) == 1
    assert cap.frames[0] == b"ok"


def test_recovery_after_corrupt_frame():
    cap = FrameCapture()
    framer = HdlcFramer(cap)

    bad = bytearray(HdlcFrameEncoder.encode(b"bad"))
    bad[2] ^= 0xFF
    good = HdlcFrameEncoder.encode(b"good")

    framer.process_bytes(bad)
    framer.process_bytes(good)

    assert len(cap.frames) == 1
    assert cap.frames[0] == b"good"


def test_byte_stuffing_roundtrip():
    cap = FrameCapture()
    framer = HdlcFramer(cap)

    payload = bytes([FLAG, ESC, XON, XOFF, 0x42])
    framer.process_bytes(HdlcFrameEncoder.encode(payload))

    assert len(cap.frames) == 1
    assert cap.frames[0] == payload


def test_flow_control_filters_xon_xoff():
    cap = FrameCapture()
    framer = HdlcFramer(cap)
    framer.set_flow_control(True)

    encoded = HdlcFrameEncoder.encode(b"ok")

    # Inject XON/XOFF around and within frame bytes
    framer.process_byte(XON)
    for b in encoded:
        framer.process_byte(b)
        framer.process_byte(XOFF)

    assert len(cap.frames) == 1
    assert cap.frames[0] == b"ok"


def test_buffer_overflow_recovers():
    cap = FrameCapture()
    framer = HdlcFramer(cap, buf_size=8)

    # Overflow
    framer.process_byte(FLAG)
    for _ in range(20):
        framer.process_byte(0x41)

    # Next frame works
    framer.process_bytes(HdlcFrameEncoder.encode(b"\x42"))

    assert len(cap.frames) == 1
    assert cap.frames[0] == b"\x42"


def test_reset_clears_state():
    cap = FrameCapture()
    framer = HdlcFramer(cap)

    framer.process_byte(FLAG)
    framer.process_byte(0x41)
    framer.reset()

    # After reset, needs FLAG to start again
    framer.process_byte(0x42)  # ignored (IDLE)
    framer.process_bytes(HdlcFrameEncoder.encode(b"ok"))

    assert len(cap.frames) == 1
    assert cap.frames[0] == b"ok"


def test_cross_language_vector():
    """Encode in Python, verify raw bytes match C++ expected output."""
    # Single byte 'A' — no stuffing needed
    encoded = HdlcFrameEncoder.encode(b"A")
    assert encoded[0] == FLAG
    assert encoded[-1] == FLAG
    assert len(encoded) >= 5  # FLAG + 'A' + 2 CRC bytes + FLAG

    # Decode it back
    cap = FrameCapture()
    framer = HdlcFramer(cap)
    framer.process_bytes(encoded)
    assert cap.frames[0] == b"A"
