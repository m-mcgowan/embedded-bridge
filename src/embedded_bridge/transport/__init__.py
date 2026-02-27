"""Transport layer for bidirectional communication with embedded devices."""

from .base import Transport

__all__ = [
    "Transport",
]

# SerialTransport is not imported here to avoid requiring pyserial.
# Import directly: from embedded_bridge.transport.serial import SerialTransport
