"""Tests for EventCapture receiver."""

import pytest

from embedded_bridge.receivers.event_capture import (
    EventCapture,
    EventSpan,
    TraceEvent,
)
from embedded_bridge.receivers.base import Receiver


# ── Helpers ──────────────────────────────────────────────────────────


class FakeClock:
    """Controllable clock for deterministic tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ── Protocol compliance ──────────────────────────────────────────────


class TestProtocol:
    def test_satisfies_receiver_protocol(self):
        capture = EventCapture()
        assert isinstance(capture, Receiver)


# ── Basic parsing ────────────────────────────────────────────────────


class TestParsing:
    def test_parse_started_event(self):
        clock = FakeClock(100.0)
        capture = EventCapture(clock=clock)

        capture.feed("T=0.001600 GPS_STARTED")

        assert len(capture.events) == 1
        e = capture.events[0]
        assert e.name == "GPS"
        assert e.action == "STARTED"
        assert e.device_timestamp_s == pytest.approx(0.0016)
        assert e.host_timestamp_s == 100.0

    def test_parse_stopped_event(self):
        clock = FakeClock(200.0)
        capture = EventCapture(clock=clock)

        capture.feed("T=5.123456 IMU_STOPPED")

        assert len(capture.events) == 1
        e = capture.events[0]
        assert e.name == "IMU"
        assert e.action == "STOPPED"
        assert e.device_timestamp_s == pytest.approx(5.123456)

    def test_parse_large_timestamp(self):
        capture = EventCapture()
        capture.feed("T=3600.000000 LONG_TEST_STARTED")

        assert len(capture.events) == 1
        assert capture.events[0].device_timestamp_s == pytest.approx(3600.0)
        assert capture.events[0].name == "LONG_TEST"

    def test_bytes_input(self):
        capture = EventCapture()
        capture.feed(b"T=0.001000 GPS_STARTED")

        assert len(capture.events) == 1
        assert capture.events[0].name == "GPS"

    def test_whitespace_stripped(self):
        capture = EventCapture()
        capture.feed("  T=0.001000 GPS_STARTED  \n")

        assert len(capture.events) == 1
        assert capture.events[0].name == "GPS"


# ── Non-matching lines ───────────────────────────────────────────────


class TestIgnoredLines:
    def test_ignore_empty_line(self):
        capture = EventCapture()
        capture.feed("")
        assert len(capture.events) == 0

    def test_ignore_regular_log(self):
        capture = EventCapture()
        capture.feed("power_profile: holding 'gps' active for 5000 ms")
        assert len(capture.events) == 0

    def test_ignore_chrome_json(self):
        capture = EventCapture()
        capture.feed('{"ph":"B","ts":1000,"name":"gps","pid":1,"tid":1}')
        assert len(capture.events) == 0

    def test_ignore_partial_t_line(self):
        capture = EventCapture()
        capture.feed("T=0.001000")  # No event name
        assert len(capture.events) == 0

    def test_ignore_malformed_timestamp(self):
        capture = EventCapture()
        capture.feed("T=abc.def GPS_STARTED")
        assert len(capture.events) == 0

    def test_ignore_unknown_action(self):
        capture = EventCapture()
        capture.feed("T=0.001000 GPS_RUNNING")  # Not STARTED or STOPPED
        assert len(capture.events) == 0


# ── Span pairing ─────────────────────────────────────────────────────


class TestSpans:
    def test_start_stop_pair(self):
        clock = FakeClock(10.0)
        capture = EventCapture(clock=clock)

        capture.feed("T=0.001600 GPS_STARTED")
        assert len(capture.spans) == 0
        assert "GPS" in capture.pending

        clock.advance(5.0)
        capture.feed("T=5.001600 GPS_STOPPED")

        assert len(capture.spans) == 1
        span = capture.spans[0]
        assert span.name == "GPS"
        assert span.device_duration_s == pytest.approx(5.0)
        assert span.host_duration_s == pytest.approx(5.0)
        assert "GPS" not in capture.pending

    def test_multiple_spans(self):
        capture = EventCapture()

        capture.feed("T=0.000000 ALL_OFF_STARTED")
        capture.feed("T=5.000000 ALL_OFF_STOPPED")
        capture.feed("T=5.100000 GPS_STARTED")
        capture.feed("T=10.100000 GPS_STOPPED")
        capture.feed("T=10.200000 IMU_STARTED")
        capture.feed("T=15.200000 IMU_STOPPED")

        assert len(capture.spans) == 3
        assert [s.name for s in capture.spans] == ["ALL_OFF", "GPS", "IMU"]

    def test_nested_spans(self):
        """Outer scope wraps inner scopes (like peripheral_cycle wrapping all_off)."""
        capture = EventCapture()

        capture.feed("T=0.000000 PERIPHERAL_CYCLE_STARTED")
        capture.feed("T=0.100000 ALL_OFF_STARTED")
        capture.feed("T=5.100000 ALL_OFF_STOPPED")
        capture.feed("T=5.200000 GPS_STARTED")
        capture.feed("T=10.200000 GPS_STOPPED")
        capture.feed("T=10.300000 PERIPHERAL_CYCLE_STOPPED")

        assert len(capture.spans) == 3
        names = [s.name for s in capture.spans]
        assert "ALL_OFF" in names
        assert "GPS" in names
        assert "PERIPHERAL_CYCLE" in names

    def test_unmatched_stop(self):
        """STOP without START logs warning but doesn't crash."""
        capture = EventCapture()
        capture.feed("T=0.001000 GPS_STOPPED")

        assert len(capture.events) == 1
        assert len(capture.spans) == 0

    def test_duplicate_start(self):
        """Second START for same name replaces the first (with warning)."""
        capture = EventCapture()

        capture.feed("T=0.001000 GPS_STARTED")
        capture.feed("T=1.000000 GPS_STARTED")  # Duplicate
        capture.feed("T=2.000000 GPS_STOPPED")

        assert len(capture.spans) == 1
        span = capture.spans[0]
        # Should match the second START
        assert span.start.device_timestamp_s == pytest.approx(1.0)
        assert span.device_duration_s == pytest.approx(1.0)


# ── Callbacks ────────────────────────────────────────────────────────


class TestCallbacks:
    def test_on_event_callback(self):
        received: list[TraceEvent] = []
        capture = EventCapture(on_event=received.append)

        capture.feed("T=0.001000 GPS_STARTED")
        capture.feed("Regular log line")
        capture.feed("T=5.001000 GPS_STOPPED")

        assert len(received) == 2
        assert received[0].name == "GPS"
        assert received[0].action == "STARTED"
        assert received[1].action == "STOPPED"

    def test_on_span_callback(self):
        received: list[EventSpan] = []
        capture = EventCapture(on_span=received.append)

        capture.feed("T=0.001000 GPS_STARTED")
        assert len(received) == 0

        capture.feed("T=5.001000 GPS_STOPPED")
        assert len(received) == 1
        assert received[0].name == "GPS"


# ── Event names ──────────────────────────────────────────────────────


class TestEventNames:
    def test_event_names(self):
        capture = EventCapture()

        capture.feed("T=0.000000 GPS_STARTED")
        capture.feed("T=1.000000 IMU_STARTED")
        capture.feed("T=2.000000 GPS_STOPPED")

        assert capture.event_names == {"GPS", "IMU"}

    def test_event_names_empty(self):
        capture = EventCapture()
        assert capture.event_names == set()


# ── Reset ────────────────────────────────────────────────────────────


class TestReset:
    def test_reset_clears_everything(self):
        capture = EventCapture()

        capture.feed("T=0.001000 GPS_STARTED")
        capture.feed("T=5.001000 GPS_STOPPED")
        capture.feed("T=6.000000 IMU_STARTED")

        assert len(capture.events) == 3
        assert len(capture.spans) == 1
        assert len(capture.pending) == 1

        capture.reset()

        assert len(capture.events) == 0
        assert len(capture.spans) == 0
        assert len(capture.pending) == 0
        assert capture.event_names == set()


# ── Real-world serial output ────────────────────────────────────────


class TestRealWorldOutput:
    """Test with output matching what the provisioner actually produces."""

    def test_peripheral_cycle_output(self):
        """Simulates output from the K (per-peripheral profile) menu item."""
        clock = FakeClock(0.0)
        capture = EventCapture(clock=clock)

        lines = [
            "Starting peripheral cycle...",
            "power_profile: beginning peripheral cycle",
            "T=1.234567 PERIPHERAL_CYCLE_STARTED",
            '{"ph":"B","ts":1234567,"name":"peripheral_cycle","pid":1,"tid":1}',
            "power_profile: all off, holding for 5000 ms",
            "T=1.234600 ALL_OFF_STARTED",
            '{"ph":"B","ts":1234600,"name":"all_off","pid":1,"tid":1}',
        ]

        for line in lines:
            clock.advance(0.01)
            capture.feed(line)

        # Should only capture T= lines, not Chrome JSON
        assert len(capture.events) == 2
        assert capture.events[0].name == "PERIPHERAL_CYCLE"
        assert capture.events[1].name == "ALL_OFF"

    def test_full_cycle_spans(self):
        """Full cycle: baseline → GPS → IMU → baseline."""
        capture = EventCapture()

        events = [
            "T=0.000000 PERIPHERAL_CYCLE_STARTED",
            "T=0.001000 ALL_OFF_STARTED",
            "T=5.001000 ALL_OFF_STOPPED",
            "T=5.010000 GPS_STARTED",
            "T=10.010000 GPS_STOPPED",
            "T=10.020000 IMU_STARTED",
            "T=15.020000 IMU_STOPPED",
            "T=15.030000 ALL_OFF_STARTED",
            "T=20.030000 ALL_OFF_STOPPED",
            "T=20.031000 PERIPHERAL_CYCLE_STOPPED",
        ]

        for line in events:
            capture.feed(line)

        assert len(capture.spans) == 5  # cycle, 2x all_off, gps, imu

        # Check peripheral durations
        peripheral_spans = {s.name: s for s in capture.spans if s.name not in ("PERIPHERAL_CYCLE",)}
        gps = [s for s in capture.spans if s.name == "GPS"][0]
        imu = [s for s in capture.spans if s.name == "IMU"][0]

        assert gps.device_duration_s == pytest.approx(5.0)
        assert imu.device_duration_s == pytest.approx(5.0)

        # ALL_OFF appears twice — both should be captured
        all_off_spans = [s for s in capture.spans if s.name == "ALL_OFF"]
        assert len(all_off_spans) == 2
