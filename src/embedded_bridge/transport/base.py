"""Transport protocol for bidirectional byte streams to embedded devices."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Transport(Protocol):
    """Bidirectional byte stream to/from an embedded device.

    Transports deliver and accept raw bytes — they know nothing about
    lines, frames, or message boundaries. Reconnection policy, port
    discovery, and baud rate configuration are transport-specific concerns
    handled by each implementation.
    """

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def read(self, size: int = -1, timeout: float | None = None) -> bytes: ...

    def write(self, data: bytes) -> None: ...

    def is_connected(self) -> bool: ...

    @property
    def port_path(self) -> str | None:
        """Underlying port path, if applicable (for sleep/wake detection)."""
        ...
