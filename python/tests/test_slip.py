"""Tests for SLIP framing — shared test vectors with C++ implementation."""

from embedded_bridge.framing.slip import (
    END,
    ESC,
    ESC_END,
    ESC_ESC,
    SlipFrameEncoder,
    SlipFramer,
)


class FrameCapture:
    def __init__(self):
        self.frames: list[bytes] = []

    def __call__(self, data: bytes) -> None:
        self.frames.append(data)


def test_decode_single_frame():
    cap = FrameCapture()
    framer = SlipFramer(cap)

    framer.process_bytes(SlipFrameEncoder.encode(b"Hi"))

    assert len(cap.frames) == 1
    assert cap.frames[0] == b"Hi"


def test_empty_frame_ignored():
    """Back-to-back ENDs produce no frame (SLIP has no empty-payload concept)."""
    cap = FrameCapture()
    framer = SlipFramer(cap)

    framer.process_bytes(bytes([END, END, END]))

    assert len(cap.frames) == 0


def test_multiple_consecutive_frames():
    cap = FrameCapture()
    framer = SlipFramer(cap)

    wire = SlipFrameEncoder.encode(b"one") + SlipFrameEncoder.encode(b"two")
    framer.process_bytes(wire)

    assert len(cap.frames) == 2
    assert cap.frames[0] == b"one"
    assert cap.frames[1] == b"two"


def test_byte_stuffing_roundtrip():
    cap = FrameCapture()
    framer = SlipFramer(cap)

    payload = bytes([END, ESC, 0x42])
    framer.process_bytes(SlipFrameEncoder.encode(payload))

    assert len(cap.frames) == 1
    assert cap.frames[0] == payload


def test_invalid_escape_rejected():
    """Invalid escape sequence (ESC followed by non-ESC_END/ESC_ESC) → error."""
    cap = FrameCapture()
    framer = SlipFramer(cap)

    framer.process_byte(END)  # start
    framer.process_byte(0x41)
    framer.process_byte(ESC)
    framer.process_byte(0x42)  # invalid escape
    framer.process_byte(END)  # end

    assert len(cap.frames) == 0


def test_recovery_after_error():
    cap = FrameCapture()
    framer = SlipFramer(cap)

    # Bad frame with invalid escape
    framer.process_byte(END)
    framer.process_byte(ESC)
    framer.process_byte(0x42)  # invalid
    framer.process_byte(END)

    # Good frame
    framer.process_bytes(SlipFrameEncoder.encode(b"ok"))

    assert len(cap.frames) == 1
    assert cap.frames[0] == b"ok"


def test_buffer_overflow_recovers():
    cap = FrameCapture()
    framer = SlipFramer(cap, buf_size=8)

    # Overflow
    framer.process_byte(END)
    for _ in range(20):
        framer.process_byte(0x41)
    framer.process_byte(END)

    # Next frame works
    framer.process_bytes(SlipFrameEncoder.encode(b"\x42"))

    assert len(cap.frames) == 1
    assert cap.frames[0] == b"\x42"


def test_reset_clears_state():
    cap = FrameCapture()
    framer = SlipFramer(cap)

    framer.process_byte(END)
    framer.process_byte(0x41)
    framer.reset()

    framer.process_bytes(SlipFrameEncoder.encode(b"ok"))

    assert len(cap.frames) == 1
    assert cap.frames[0] == b"ok"


def test_cross_language_vector():
    """Encode in Python, verify structure matches C++ expectations."""
    encoded = SlipFrameEncoder.encode(b"A")
    assert encoded[0] == END
    assert encoded[-1] == END
    assert encoded[1] == ord("A")
    assert len(encoded) == 3  # END + 'A' + END

    # Roundtrip
    cap = FrameCapture()
    framer = SlipFramer(cap)
    framer.process_bytes(encoded)
    assert cap.frames[0] == b"A"
