"""Transport layer for bidirectional communication with embedded devices."""

from .base import Transport

__all__ = [
    "Transport",
]

# Concrete transports are not imported here to avoid requiring their
# optional dependencies.  Import directly:
#
#   from embedded_bridge.transport.serial import SerialTransport
#   from embedded_bridge.transport.websocket import WebSocketTransport
