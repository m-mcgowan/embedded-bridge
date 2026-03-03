"""Pluggable serial framing protocols (HDLC, SLIP, COBS) and message protocol."""

from .base import Framer
from .cobs import CobsFrameEncoder, CobsFramer, cobs_decode, cobs_encode
from .crc16 import CRC16_GOOD, CRC16_INIT, crc16_hdlc, crc16_hdlc_update
from .hdlc import ESC, FLAG, XOFF, XON, HdlcFrameEncoder, HdlcFramer
from .line import LineFramer
from .message import (
    BINARY_PROTOCOL_V1,
    SOH,
    MessageHandler,
    MessageReader,
    MessageWriter,
    StreamingMessageHandler,
    decode_varint,
    encode_varint,
)
from .slip import SlipFrameEncoder, SlipFramer

__all__ = [
    # Framer protocol
    "Framer",
    # Line framer
    "LineFramer",
    # Message protocol (application layer)
    "SOH",
    "BINARY_PROTOCOL_V1",
    "MessageReader",
    "MessageWriter",
    "MessageHandler",
    "StreamingMessageHandler",
    "encode_varint",
    "decode_varint",
    # CRC-16/HDLC
    "CRC16_INIT",
    "CRC16_GOOD",
    "crc16_hdlc",
    "crc16_hdlc_update",
    # HDLC
    "FLAG",
    "ESC",
    "XON",
    "XOFF",
    "HdlcFramer",
    "HdlcFrameEncoder",
    # SLIP
    "SlipFramer",
    "SlipFrameEncoder",
    # COBS
    "CobsFramer",
    "CobsFrameEncoder",
    "cobs_encode",
    "cobs_decode",
]
