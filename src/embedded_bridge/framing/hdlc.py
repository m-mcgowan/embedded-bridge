"""HDLC-like frame encoder/decoder (RFC 1662 style).

Matches the C++ implementation in embedded-menu framing/hdlc.h.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum, auto

from .crc16 import CRC16_GOOD, CRC16_INIT, crc16_hdlc_update

FLAG: int = 0x7E
ESC: int = 0x7D
ESC_XOR: int = 0x20
XON: int = 0x11
XOFF: int = 0x13


class _State(Enum):
    IDLE = auto()
    IN_FRAME = auto()
    ESCAPE = auto()
    ERROR = auto()


class HdlcFramer:
    """Stateful HDLC frame decoder.

    Feed raw bytes via process_byte(). On valid frame (good CRC-16/HDLC),
    invokes the callback with the un-stuffed, CRC-stripped payload.
    Corrupt or overflowed frames are silently discarded.
    """

    def __init__(
        self,
        on_frame: Callable[[bytes], None],
        buf_size: int = 256,
    ) -> None:
        self._on_frame = on_frame
        self._buf_size = buf_size
        self._buf = bytearray()
        self._state = _State.IDLE
        self._flow_control = False

    def process_byte(self, c: int) -> None:
        if self._flow_control and c in (XON, XOFF):
            return

        if self._state == _State.IDLE:
            if c == FLAG:
                self._buf.clear()
                self._state = _State.IN_FRAME

        elif self._state == _State.IN_FRAME:
            if c == FLAG:
                self._deliver()
                self._buf.clear()
            elif c == ESC:
                self._state = _State.ESCAPE
            else:
                self._store(c)

        elif self._state == _State.ESCAPE:
            if c == FLAG:
                self._buf.clear()
                self._state = _State.IN_FRAME
            else:
                self._store(c ^ ESC_XOR)
                self._state = _State.IN_FRAME

        elif self._state == _State.ERROR:
            if c == FLAG:
                self._buf.clear()
                self._state = _State.IN_FRAME

    def process_bytes(self, data: bytes | bytearray) -> None:
        for b in data:
            self.process_byte(b)

    def reset(self) -> None:
        self._state = _State.IDLE
        self._buf.clear()

    def set_flow_control(self, enable: bool) -> None:
        self._flow_control = enable

    def _store(self, c: int) -> None:
        if len(self._buf) < self._buf_size:
            self._buf.append(c)
        else:
            self._state = _State.ERROR

    def _deliver(self) -> None:
        if len(self._buf) < 2:
            return

        crc = CRC16_INIT
        for b in self._buf:
            crc = crc16_hdlc_update(crc, b)

        if (crc ^ 0xFFFF) & 0xFFFF != CRC16_GOOD:
            return

        self._on_frame(bytes(self._buf[:-2]))


class HdlcFrameEncoder:
    """Encode payloads into HDLC frames."""

    @staticmethod
    def encode(payload: bytes | bytearray) -> bytes:
        """Encode a payload into a complete HDLC frame.

        Returns FLAG + byte-stuffed payload + CRC + FLAG.
        """
        # Compute CRC over payload
        crc = CRC16_INIT
        for b in payload:
            crc = crc16_hdlc_update(crc, b)
        crc = (crc ^ 0xFFFF) & 0xFFFF

        out = bytearray()
        out.append(FLAG)

        for b in payload:
            _emit_stuffed(out, b)

        # CRC bytes (little-endian), byte-stuffed
        _emit_stuffed(out, crc & 0xFF)
        _emit_stuffed(out, crc >> 8)

        out.append(FLAG)
        return bytes(out)


def _emit_stuffed(out: bytearray, c: int) -> None:
    if c in (FLAG, ESC):
        out.append(ESC)
        out.append(c ^ ESC_XOR)
    else:
        out.append(c)
