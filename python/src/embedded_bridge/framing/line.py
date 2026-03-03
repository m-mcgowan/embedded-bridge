"""LineFramer — segments a byte stream into text lines on newline boundaries."""

from __future__ import annotations

import codecs


class LineFramer:
    """Splits a byte stream into text lines on ``\\n`` boundaries.

    Handles incremental UTF-8 decoding across feed() calls and strips
    trailing ``\\r`` (CRLF normalization). Incomplete lines stay buffered
    until the next newline arrives.
    """

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._buf = ""
        self._messages: list[str] = []

    def feed(self, data: bytes) -> None:
        text = self._decoder.decode(data)
        self._buf += text

        while "\n" in self._buf:
            line, _, self._buf = self._buf.partition("\n")
            # CRLF normalization
            if line.endswith("\r"):
                line = line[:-1]
            self._messages.append(line)

    def drain(self) -> list[str]:
        messages = self._messages
        self._messages = []
        return messages

    def reset(self) -> None:
        self._decoder.reset()
        self._buf = ""
        self._messages.clear()
