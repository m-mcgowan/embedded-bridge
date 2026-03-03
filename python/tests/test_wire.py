"""Cross-language wire compatibility tests.

Loads shared test vectors from wire-tests/ and validates the Python
implementations produce identical results to the C++ implementations.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from embedded_bridge.framing.crc16 import crc16_hdlc, CRC16_GOOD
from embedded_bridge.framing.hdlc import HdlcFramer, HdlcFrameEncoder
from embedded_bridge.framing.slip import SlipFramer, SlipFrameEncoder
from embedded_bridge.framing.cobs import CobsFramer, CobsFrameEncoder, cobs_encode
from embedded_bridge.framing.message import (
    MessageReader,
    MessageWriter,
    encode_varint,
    decode_varint,
    SOH,
    BINARY_PROTOCOL_V1,
)

WIRE_TESTS_DIR = Path(__file__).parents[2] / "wire-tests"


def load_vectors(filename: str) -> dict:
    with open(WIRE_TESTS_DIR / filename) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# CRC-16
# ---------------------------------------------------------------------------


class TestCRC16Wire:
    vectors = load_vectors("crc16.json")

    @pytest.mark.parametrize(
        "v", vectors["vectors"], ids=lambda v: v["name"]
    )
    def test_crc(self, v: dict) -> None:
        data = bytes.fromhex(v["input_hex"])
        assert crc16_hdlc(data) == v["crc"]

    @pytest.mark.parametrize(
        "v", vectors["residue_vectors"], ids=lambda v: v["name"]
    )
    def test_residue(self, v: dict) -> None:
        frame = bytes.fromhex(v["frame_hex"])
        assert crc16_hdlc(frame) == v["frame_crc"]
        assert crc16_hdlc(frame) == self.vectors["good_residue"]


# ---------------------------------------------------------------------------
# HDLC
# ---------------------------------------------------------------------------


class TestHDLCWire:
    vectors = load_vectors("hdlc.json")

    @pytest.mark.parametrize(
        "v", vectors["encode_vectors"], ids=lambda v: v["name"]
    )
    def test_encode(self, v: dict) -> None:
        payload = bytes.fromhex(v["payload_hex"])
        encoded = HdlcFrameEncoder.encode(payload)
        assert encoded.hex() == v["encoded_hex"]

    @pytest.mark.parametrize(
        "v", vectors["encode_vectors"], ids=lambda v: v["name"]
    )
    def test_decode_roundtrip(self, v: dict) -> None:
        payload = bytes.fromhex(v["payload_hex"])
        wire = bytes.fromhex(v["encoded_hex"])
        frames: list[bytes] = []
        framer = HdlcFramer(lambda data: frames.append(data))
        framer.process_bytes(wire)
        assert len(frames) == 1
        assert frames[0] == payload

    @pytest.mark.parametrize(
        "v", vectors["decode_error_vectors"], ids=lambda v: v["name"]
    )
    def test_decode_error(self, v: dict) -> None:
        wire = bytes.fromhex(v["wire_hex"])
        frames: list[bytes] = []
        framer = HdlcFramer(lambda data: frames.append(data))
        framer.process_bytes(wire)
        assert len(frames) == 0


# ---------------------------------------------------------------------------
# SLIP
# ---------------------------------------------------------------------------


class TestSLIPWire:
    vectors = load_vectors("slip.json")

    @pytest.mark.parametrize(
        "v", vectors["encode_vectors"], ids=lambda v: v["name"]
    )
    def test_encode(self, v: dict) -> None:
        payload = bytes.fromhex(v["payload_hex"])
        encoded = SlipFrameEncoder.encode(payload)
        assert encoded.hex() == v["encoded_hex"]

    @pytest.mark.parametrize(
        "v", vectors["encode_vectors"], ids=lambda v: v["name"]
    )
    def test_decode_roundtrip(self, v: dict) -> None:
        payload = bytes.fromhex(v["payload_hex"])
        wire = bytes.fromhex(v["encoded_hex"])
        frames: list[bytes] = []
        framer = SlipFramer(lambda data: frames.append(data))
        framer.process_bytes(wire)
        assert len(frames) == 1
        assert frames[0] == payload


# ---------------------------------------------------------------------------
# COBS
# ---------------------------------------------------------------------------


class TestCOBSWire:
    vectors = load_vectors("cobs.json")

    @pytest.mark.parametrize(
        "v", vectors["encode_vectors"], ids=lambda v: v["name"]
    )
    def test_encode_raw(self, v: dict) -> None:
        payload = bytes.fromhex(v["payload_hex"])
        encoded = cobs_encode(payload)
        assert encoded.hex() == v["cobs_hex"]

    @pytest.mark.parametrize(
        "v", vectors["encode_vectors"], ids=lambda v: v["name"]
    )
    def test_encode_framed(self, v: dict) -> None:
        payload = bytes.fromhex(v["payload_hex"])
        framed = CobsFrameEncoder.encode(payload)
        assert framed.hex() == v["framed_hex"]

    @pytest.mark.parametrize(
        "v", vectors["encode_vectors"], ids=lambda v: v["name"]
    )
    def test_decode_roundtrip(self, v: dict) -> None:
        payload = bytes.fromhex(v["payload_hex"])
        wire = bytes.fromhex(v["framed_hex"])
        frames: list[bytes] = []
        framer = CobsFramer(lambda data: frames.append(data))
        framer.process_bytes(wire)
        assert len(frames) == 1
        assert frames[0] == payload


# ---------------------------------------------------------------------------
# Message protocol — varint
# ---------------------------------------------------------------------------


class TestVarintWire:
    vectors = load_vectors("message.json")

    @pytest.mark.parametrize(
        "v", vectors["varint_vectors"], ids=lambda v: str(v["value"])
    )
    def test_encode(self, v: dict) -> None:
        encoded = encode_varint(v["value"])
        assert encoded.hex() == v["encoded_hex"]
        assert len(encoded) == v["bytes"]

    @pytest.mark.parametrize(
        "v", vectors["varint_vectors"], ids=lambda v: str(v["value"])
    )
    def test_decode(self, v: dict) -> None:
        data = bytes.fromhex(v["encoded_hex"])
        value, consumed = decode_varint(data)
        assert value == v["value"]
        assert consumed == v["bytes"]


# ---------------------------------------------------------------------------
# Message protocol — writer
# ---------------------------------------------------------------------------


class TestMessageWriterWire:
    vectors = load_vectors("message.json")

    @pytest.mark.parametrize(
        "v", vectors["text_vectors"], ids=lambda v: v["name"]
    )
    def test_write_text(self, v: dict) -> None:
        output = bytearray()
        writer = MessageWriter(output.extend)
        writer.write_text(v["text"])
        assert output.hex() == v["wire_hex"]

    @pytest.mark.parametrize(
        "v", vectors["binary_vectors"], ids=lambda v: v["name"]
    )
    def test_write_binary(self, v: dict) -> None:
        output = bytearray()
        writer = MessageWriter(output.extend)
        payload = bytes.fromhex(v["payload_hex"])
        writer.write_binary(payload)
        assert output.hex() == v["wire_hex"]


# ---------------------------------------------------------------------------
# Message protocol — reader
# ---------------------------------------------------------------------------


class TestMessageReaderWire:
    vectors = load_vectors("message.json")

    @pytest.mark.parametrize(
        "v", vectors["reader_vectors"], ids=lambda v: v["name"]
    )
    def test_read(self, v: dict) -> None:
        wire = bytes.fromhex(v["wire_hex"])
        reader = MessageReader()
        reader.feed(wire)
        messages = reader.drain()

        expected = v["expected"]
        assert len(messages) == len(expected)
        for msg, exp in zip(messages, expected):
            if exp["type"] == "text":
                assert isinstance(msg, str)
                assert msg == exp["value"]
            else:
                assert isinstance(msg, bytes)
                assert msg.hex() == exp["value_hex"]
