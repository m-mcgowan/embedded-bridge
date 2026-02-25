"""Base receiver protocol for incoming device messages."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Receiver(Protocol):
    """Consumes incoming messages from the device.

    Any object with a compatible ``feed()`` method satisfies this protocol.
    Receivers work standalone — they don't need transport or framing.
    PlatformIO test runners feed them lines directly.
    """

    def feed(self, message: bytes | str) -> None: ...
