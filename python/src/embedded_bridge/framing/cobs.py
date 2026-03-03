"""COBS (Consistent Overhead Byte Stuffing) frame encoder/decoder.

Matches the C++ implementation in embedded-menu framing/cobs.h.
Delimiter is 0x00. No built-in CRC.
"""

from __future__ import annotations

from collections.abc import Callable


class CobsFramer:
    """Stateful COBS frame decoder.

    Feed raw bytes via process_byte(). On 0x00 delimiter, decodes COBS
    and delivers the payload via callback. Empty frames are silently ignored.
    """

    def __init__(
        self,
        on_frame: Callable[[bytes], None],
        buf_size: int = 256,
    ) -> None:
        self._on_frame = on_frame
        self._buf_size = buf_size
        self._raw = bytearray()
        self._error = False

    def process_byte(self, c: int) -> None:
        if c == 0x00:
            if len(self._raw) > 0 and not self._error:
                decoded = cobs_decode(bytes(self._raw))
                if decoded is not None:
                    self._on_frame(decoded)
            self._raw.clear()
            self._error = False
        else:
            if len(self._raw) < self._buf_size + 1:  # +1 for COBS overhead
                self._raw.append(c)
            else:
                self._error = True

    def process_bytes(self, data: bytes | bytearray) -> None:
        for b in data:
            self.process_byte(b)

    def reset(self) -> None:
        self._raw.clear()
        self._error = False


class CobsFrameEncoder:
    """Encode payloads into COBS frames."""

    @staticmethod
    def encode(payload: bytes | bytearray) -> bytes:
        """Encode a payload into a COBS frame.

        Returns COBS-encoded payload + 0x00 delimiter.
        """
        encoded = cobs_encode(payload)
        return encoded + b"\x00"


def cobs_encode(data: bytes | bytearray) -> bytes:
    """COBS-encode a byte buffer (no delimiter appended)."""
    out = bytearray()
    out.append(0)  # placeholder for first code byte
    code_pos = 0
    code = 1

    for b in data:
        if b == 0x00:
            out[code_pos] = code
            code_pos = len(out)
            out.append(0)  # placeholder
            code = 1
        else:
            out.append(b)
            code += 1
            if code == 0xFF:
                out[code_pos] = code
                code_pos = len(out)
                out.append(0)  # placeholder
                code = 1

    out[code_pos] = code
    return bytes(out)


def cobs_decode(data: bytes | bytearray) -> bytes | None:
    """COBS-decode a byte buffer (without delimiter). Returns None on error."""
    out = bytearray()
    i = 0
    src_len = len(data)

    while i < src_len:
        code = data[i]
        i += 1
        if code == 0:
            return None  # unexpected zero

        count = code - 1
        if i + count > src_len:
            return None  # truncated

        for _ in range(count):
            out.append(data[i])
            i += 1

        # If code < 0xFF and there's more data, emit a zero separator
        if code < 0xFF and i < src_len:
            out.append(0x00)

    return bytes(out)
