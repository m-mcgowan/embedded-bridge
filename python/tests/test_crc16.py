"""Tests for CRC-16/HDLC — shared test vectors with C++ implementation."""

from embedded_bridge.framing.crc16 import (
    CRC16_GOOD,
    CRC16_INIT,
    crc16_hdlc,
    crc16_hdlc_update,
)


def test_known_vector():
    """Standard CRC-16/HDLC check value for '123456789'."""
    assert crc16_hdlc(b"123456789") == 0x906E


def test_empty_buffer():
    """Empty buffer returns INIT ^ xorout = 0."""
    assert crc16_hdlc(b"") == 0x0000


def test_buffer_matches_incremental():
    data = bytes([0x01, 0x02, 0x03, 0x04])
    incremental = CRC16_INIT
    for b in data:
        incremental = crc16_hdlc_update(incremental, b)
    # crc16_hdlc applies final XOR; incremental does not
    assert crc16_hdlc(data) == (incremental ^ 0xFFFF) & 0xFFFF


def test_residue_check():
    """CRC over payload + CRC bytes (LE) should equal CRC16_GOOD."""
    payload = b"ABC"
    crc = crc16_hdlc(payload)

    # HDLC sends CRC low byte first
    frame = payload + bytes([crc & 0xFF, crc >> 8])
    assert crc16_hdlc(frame) == CRC16_GOOD


def test_residue_with_known_vector():
    data = b"123456789"
    crc = crc16_hdlc(data)  # 0x906E
    frame = data + bytes([crc & 0xFF, crc >> 8])
    assert crc16_hdlc(frame) == CRC16_GOOD


def test_different_data_different_crc():
    assert crc16_hdlc(bytes([0x01, 0x02])) != crc16_hdlc(bytes([0x01, 0x03]))


def test_single_byte():
    crc = crc16_hdlc_update(CRC16_INIT, 0x00)
    assert crc != CRC16_INIT
