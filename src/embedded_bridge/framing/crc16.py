"""CRC-16/HDLC (CRC-16/IBM-SDLC, CRC-16/X.25).

Reflected variant: poly 0x8408 (reflected 0x1021), init 0xFFFF, xorout 0xFFFF.
Matches the C++ implementation in embedded-menu detail/crc16.h.

Check value for b"123456789": 0x906E
Residue after valid frame: 0x0F47
"""

from __future__ import annotations

CRC16_INIT: int = 0xFFFF
CRC16_GOOD: int = 0x0F47


def crc16_hdlc_update(crc: int, byte: int) -> int:
    """Update CRC with a single byte (reflected poly 0x8408)."""
    crc ^= byte & 0xFF
    for _ in range(8):
        if crc & 0x0001:
            crc = (crc >> 1) ^ 0x8408
        else:
            crc = crc >> 1
    return crc & 0xFFFF


def crc16_hdlc(data: bytes | bytearray) -> int:
    """Compute CRC-16/HDLC over a byte buffer.

    Returns the complemented CRC (init=0xFFFF, xorout=0xFFFF).
    """
    crc = CRC16_INIT
    for b in data:
        crc = crc16_hdlc_update(crc, b)
    return (crc ^ 0xFFFF) & 0xFFFF
