"""Pluggable serial framing protocols (HDLC, SLIP, COBS)."""

from .cobs import CobsFrameEncoder, CobsFramer, cobs_decode, cobs_encode
from .crc16 import CRC16_GOOD, CRC16_INIT, crc16_hdlc, crc16_hdlc_update
from .hdlc import ESC, FLAG, XOFF, XON, HdlcFrameEncoder, HdlcFramer
from .slip import SlipFrameEncoder, SlipFramer

__all__ = [
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
