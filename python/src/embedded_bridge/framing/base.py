"""Framer protocol — segments a byte stream into messages."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Framer(Protocol):
    """Segments a byte stream into discrete messages.

    Feed raw bytes via feed(). Retrieve complete messages via drain().
    Messages are either str (text lines) or bytes (binary frames).
    """

    def feed(self, data: bytes) -> None: ...

    def drain(self) -> list[bytes | str]: ...
