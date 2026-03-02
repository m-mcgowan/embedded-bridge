"""SLIP frame encoder/decoder (RFC 1055).

Matches the C++ implementation in embedded-menu framing/slip.h.
No built-in CRC — add application-layer integrity checking if needed.
"""

from __future__ import annotations

from collections.abc import Callable

END: int = 0xC0
ESC: int = 0xDB
ESC_END: int = 0xDC
ESC_ESC: int = 0xDD


class SlipFramer:
    """Stateful SLIP frame decoder.

    Feed raw bytes via process_byte(). On complete frame (END delimiter),
    invokes the callback with the un-stuffed payload.
    Empty frames (back-to-back ENDs) are silently ignored.
    """

    def __init__(
        self,
        on_frame: Callable[[bytes], None],
        buf_size: int = 256,
    ) -> None:
        self._on_frame = on_frame
        self._buf_size = buf_size
        self._buf = bytearray()
        self._error = False
        self._escape = False

    def process_byte(self, c: int) -> None:
        if c == END:
            if len(self._buf) > 0 and not self._error:
                self._on_frame(bytes(self._buf))
            self._buf.clear()
            self._error = False
            self._escape = False

        elif c == ESC:
            self._escape = True

        else:
            if self._escape:
                self._escape = False
                if c == ESC_END:
                    self._store(END)
                elif c == ESC_ESC:
                    self._store(ESC)
                else:
                    # Protocol error — invalid escape sequence
                    self._error = True
            else:
                self._store(c)

    def process_bytes(self, data: bytes | bytearray) -> None:
        for b in data:
            self.process_byte(b)

    def reset(self) -> None:
        self._buf.clear()
        self._error = False
        self._escape = False

    def _store(self, c: int) -> None:
        if len(self._buf) < self._buf_size:
            self._buf.append(c)
        else:
            self._error = True


class SlipFrameEncoder:
    """Encode payloads into SLIP frames."""

    @staticmethod
    def encode(payload: bytes | bytearray) -> bytes:
        """Encode a payload into a complete SLIP frame.

        Returns END + byte-stuffed payload + END (double-END for robustness).
        """
        out = bytearray()
        out.append(END)

        for b in payload:
            if b == END:
                out.append(ESC)
                out.append(ESC_END)
            elif b == ESC:
                out.append(ESC)
                out.append(ESC_ESC)
            else:
                out.append(b)

        out.append(END)
        return bytes(out)
