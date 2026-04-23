"""Tests for the embedded test protocol and TestSession."""

import json
import time
from collections import deque
from unittest.mock import MagicMock

import pytest

from embedded_bridge.testing.protocol import (
    SOH,
    STX,
    parse_json_line,
    parse_marker,
)
from embedded_bridge.testing.types import TestInfo, TestOutcome
from embedded_bridge.testing.session import TestSession


# --- Protocol parsing tests ---


class TestParseMarker:
    def test_ppk_start(self):
        result = parse_marker("T=1234567 PPK_START")
        assert result is not None
        ts, payload = result
        assert payload == "PPK_START"

    def test_sleep_with_duration(self):
        result = parse_marker("T=1234567 SLEEP:30")
        assert result is not None
        ts, payload = result
        assert payload == "SLEEP:30"

    def test_test_started_with_id(self):
        result = parse_marker("T=1234567 TEST_STARTED:sleep_w2")
        assert result is not None
        _, payload = result
        assert payload == "TEST_STARTED:sleep_w2"

    def test_timestamp_with_fractional(self):
        result = parse_marker("T=1234.567890 PPK_STOP")
        assert result is not None
        ts, payload = result
        assert abs(ts - 1234.567890) < 1e-9
        assert payload == "PPK_STOP"

    def test_timestamp_integer_only(self):
        result = parse_marker("T=1234567 PPK_START")
        assert result is not None
        ts, _ = result
        assert ts == 1234567.0

    def test_not_a_marker(self):
        assert parse_marker("not a marker") is None
        assert parse_marker("") is None
        assert parse_marker("T= no_timestamp") is None

    def test_whitespace_stripped(self):
        result = parse_marker("  T=100 PPK_START  ")
        assert result is not None
        assert result[1] == "PPK_START"

    def test_existing_event_capture_format(self):
        """Protocol markers coexist with EventCapture's T= format."""
        result = parse_marker("T=1986.449564 GPS_STARTED")
        assert result is not None
        ts, payload = result
        assert abs(ts - 1986.449564) < 1e-9
        assert payload == "GPS_STARTED"


class TestParseJsonLine:
    def test_valid_json_object(self):
        line = '{"type":"test_catalog","tests":[]}'
        result = parse_json_line(line)
        assert result == {"type": "test_catalog", "tests": []}

    def test_not_json(self):
        assert parse_json_line("not json") is None
        assert parse_json_line("") is None

    def test_json_array_ignored(self):
        assert parse_json_line("[1, 2, 3]") is None

    def test_whitespace_stripped(self):
        result = parse_json_line('  {"type":"test_end"}  ')
        assert result == {"type": "test_end"}


# --- TestSession tests ---


class FakeTransport:
    """Mock transport that feeds pre-configured responses."""

    def __init__(self, responses: list[bytes] | None = None):
        self._responses = deque(responses or [])
        self._written: list[bytes] = []
        self._connected = True
        self._port_path = "/dev/fake"

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def read(self, size: int = -1, timeout: float | None = None) -> bytes:
        if self._responses:
            return self._responses.popleft()
        return b""

    def write(self, data: bytes) -> None:
        self._written.append(data)

    def is_connected(self) -> bool:
        return self._connected

    @property
    def port_path(self) -> str:
        return self._port_path

    def push_response(self, data: bytes) -> None:
        self._responses.append(data)


class TestDiscovery:
    def test_discover_parses_catalog(self):
        catalog_json = json.dumps({
            "type": "test_catalog",
            "tests": [
                {"id": "sleep_w1", "name": "Sleep: untouched", "group": "sleep"},
                {"id": "sleep_w2", "name": "Sleep: held in reset", "group": "sleep"},
            ],
        })
        transport = FakeTransport([
            b"some boot output\n",
            (catalog_json + "\n").encode(),
        ])

        session = TestSession(transport)
        tests = session.discover(timeout=2.0)

        assert len(tests) == 2
        assert tests[0].id == "sleep_w1"
        assert tests[0].name == "Sleep: untouched"
        assert tests[0].group == "sleep"
        assert tests[1].id == "sleep_w2"

        # Verify SOH was sent
        assert SOH in transport._written

    def test_discover_timeout(self):
        transport = FakeTransport([b"no catalog here\n"])
        session = TestSession(transport)

        with pytest.raises(TimeoutError):
            session.discover(timeout=0.5)


class TestStartTest:
    def test_start_sends_stx_and_waits(self):
        transport = FakeTransport([
            b"T=100 TEST_STARTED:sleep_w2\n",
        ])

        session = TestSession(transport)
        session.start_test("sleep_w2", timeout=2.0)

        # Verify STX + id was sent
        assert any(b"\x02sleep_w2\n" in w for w in transport._written)

    def test_start_timeout(self):
        transport = FakeTransport([b"wrong marker\n"])
        session = TestSession(transport)

        with pytest.raises(TimeoutError):
            session.start_test("sleep_w2", timeout=0.5)


class TestMonitor:
    def test_monitor_collects_markers(self):
        transport = FakeTransport([
            b"setup output line 1\n",
            b"T=200 PPK_START\n",
            b"T=300 PPK_STOP\n",
            b'{"type":"test_end","id":"sleep_w2","status":"ok"}\n',
            b"T=400 TEST_STOPPED:sleep_w2\n",
        ])

        session = TestSession(transport)
        outcome = session.monitor("sleep_w2", timeout=2.0)

        assert outcome.status == "ok"
        assert "PPK_START" in outcome.markers
        assert "PPK_STOP" in outcome.markers
        assert "TEST_STOPPED" in outcome.markers

    def test_monitor_detects_sleep_marker(self):
        # Simulate: SLEEP marker, then port "disappears" (read returns empty),
        # then device "wakes" (TEST_STOPPED)
        transport = FakeTransport([
            b"T=200 PPK_START\n",
            b"T=210 SLEEP:30\n",
        ])

        session = TestSession(transport)
        # Don't actually sleep — just verify the marker is recorded
        # (Full sleep/wake test would need real port monitoring)
        outcome = session.monitor("sleep_w2", timeout=1.0)

        assert outcome.sleep_expected_s == 30.0
        assert "SLEEP" in outcome.markers
        # Status will be timeout since we never get TEST_STOPPED
        assert outcome.status == "timeout"

    def test_monitor_sends_ack_on_sleep_marker(self):
        """Host sends ACK (0x06) to device after SLEEP: marker to confirm
        measurement setup is ready. Firmware waits for this before calling
        esp_deep_sleep_start()."""
        from embedded_bridge.testing.protocol import ACK

        transport = FakeTransport([
            b"T=200 PPK_START\n",
            b"T=210 SLEEP:30\n",
        ])

        session = TestSession(transport)
        session.monitor("sleep_w2", timeout=0.5)

        # Verify the ACK byte was sent
        assert ACK in transport._written, (
            f"Expected ACK ({ACK!r}) to be sent after SLEEP: marker. "
            f"Got writes: {transport._written}"
        )

    def test_ack_constant_is_documented_value(self):
        """ACK is ASCII ACK (0x06) per USB/serial convention."""
        from embedded_bridge.testing.protocol import ACK
        assert ACK == b"\x06"

    def test_monitor_timeout(self):
        transport = FakeTransport([b"no test_stopped\n"])
        session = TestSession(transport)
        outcome = session.monitor("sleep_w2", timeout=0.5)

        assert outcome.status == "timeout"
        assert any("did not complete" in w for w in outcome.warnings)

    def test_serial_log_captured(self):
        transport = FakeTransport([
            b"line 1\n",
            b"line 2\n",
            b"T=400 TEST_STOPPED:test1\n",
        ])

        session = TestSession(transport)
        outcome = session.monitor("test1", timeout=2.0)

        assert "line 1" in outcome.serial_log
        assert "line 2" in outcome.serial_log
