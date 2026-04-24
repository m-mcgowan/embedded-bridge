"""Tests for EventCapture receiver."""

import json

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


def _b(name: str, ts_us: int, pid: int = 1, tid: int = 1) -> str:
    """Build a Chrome JSON begin event line."""
    return json.dumps({"ph": "B", "ts": ts_us, "name": name, "pid": pid, "tid": tid})


def _e(name: str, ts_us: int, pid: int = 1, tid: int = 1) -> str:
    """Build a Chrome JSON end event line."""
    return json.dumps({"ph": "E", "ts": ts_us, "name": name, "pid": pid, "tid": tid})


# ── Protocol compliance ──────────────────────────────────────────────


class TestProtocol:
    def test_satisfies_receiver_protocol(self):
        capture = EventCapture()
        assert isinstance(capture, Receiver)


# ── Basic parsing ────────────────────────────────────────────────────


class TestParsing:
    def test_parse_begin_event(self):
        clock = FakeClock(100.0)
        capture = EventCapture(clock=clock)

        capture.feed(_b("gps", 1600))

        assert len(capture.events) == 1
        e = capture.events[0]
        assert e.name == "gps"
        assert e.action == "STARTED"
        assert e.device_timestamp_s == pytest.approx(0.0016)
        assert e.host_timestamp_s == 100.0

    def test_parse_end_event(self):
        clock = FakeClock(200.0)
        capture = EventCapture(clock=clock)

        capture.feed(_e("imu", 5123456))

        assert len(capture.events) == 1
        e = capture.events[0]
        assert e.name == "imu"
        assert e.action == "STOPPED"
        assert e.device_timestamp_s == pytest.approx(5.123456)

    def test_parse_large_timestamp(self):
        capture = EventCapture()
        capture.feed(_b("long_test", 3600000000))

        assert len(capture.events) == 1
        assert capture.events[0].device_timestamp_s == pytest.approx(3600.0)
        assert capture.events[0].name == "long_test"

    def test_bytes_input(self):
        capture = EventCapture()
        capture.feed(_b("gps", 1000).encode("utf-8"))

        assert len(capture.events) == 1
        assert capture.events[0].name == "gps"

    def test_whitespace_stripped(self):
        capture = EventCapture()
        capture.feed(f"  {_b('gps', 1000)}  \n")

        assert len(capture.events) == 1
        assert capture.events[0].name == "gps"


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

    def test_ignore_t_markers(self):
        capture = EventCapture()
        capture.feed("T=0.001600 GPS_FIX_STARTED")
        assert len(capture.events) == 0

    def test_ignore_malformed_json(self):
        capture = EventCapture()
        capture.feed("{not valid json")
        assert len(capture.events) == 0

    def test_ignore_json_without_ph(self):
        capture = EventCapture()
        capture.feed('{"ts":1000,"name":"gps"}')
        assert len(capture.events) == 0

    def test_ignore_counter_events(self):
        capture = EventCapture()
        capture.feed('{"ph":"C","ts":1000,"name":"heap","pid":1,"args":{"value":102400}}')
        assert len(capture.events) == 0

    def test_ignore_json_without_name(self):
        capture = EventCapture()
        capture.feed('{"ph":"B","ts":1000,"pid":1,"tid":1}')
        assert len(capture.events) == 0


# ── Span pairing ─────────────────────────────────────────────────────


class TestSpans:
    def test_start_stop_pair(self):
        clock = FakeClock(10.0)
        capture = EventCapture(clock=clock)

        capture.feed(_b("gps", 1600))
        assert len(capture.spans) == 0
        assert "gps" in capture.pending

        clock.advance(5.0)
        capture.feed(_e("gps", 5001600))

        assert len(capture.spans) == 1
        span = capture.spans[0]
        assert span.name == "gps"
        assert span.device_duration_s == pytest.approx(5.0)
        assert span.host_duration_s == pytest.approx(5.0)
        assert "gps" not in capture.pending

    def test_multiple_spans(self):
        capture = EventCapture()

        capture.feed(_b("all_off", 0))
        capture.feed(_e("all_off", 5000000))
        capture.feed(_b("gps", 5100000))
        capture.feed(_e("gps", 10100000))
        capture.feed(_b("imu", 10200000))
        capture.feed(_e("imu", 15200000))

        assert len(capture.spans) == 3
        assert [s.name for s in capture.spans] == ["all_off", "gps", "imu"]

    def test_nested_spans(self):
        """Outer scope wraps inner scopes (like peripheral_cycle wrapping all_off)."""
        capture = EventCapture()

        capture.feed(_b("peripheral_cycle", 0))
        capture.feed(_b("all_off", 100000))
        capture.feed(_e("all_off", 5100000))
        capture.feed(_b("gps", 5200000))
        capture.feed(_e("gps", 10200000))
        capture.feed(_e("peripheral_cycle", 10300000))

        assert len(capture.spans) == 3
        names = [s.name for s in capture.spans]
        assert "all_off" in names
        assert "gps" in names
        assert "peripheral_cycle" in names

    def test_unmatched_stop(self):
        """STOP without START logs warning but doesn't crash."""
        capture = EventCapture()
        capture.feed(_e("gps", 1000))

        assert len(capture.events) == 1
        assert len(capture.spans) == 0

    def test_duplicate_start(self):
        """Second START for same name replaces the first (with warning)."""
        capture = EventCapture()

        capture.feed(_b("gps", 1000))
        capture.feed(_b("gps", 1000000))  # Duplicate
        capture.feed(_e("gps", 2000000))

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

        capture.feed(_b("gps", 1000))
        capture.feed("Regular log line")
        capture.feed(_e("gps", 5001000))

        assert len(received) == 2
        assert received[0].name == "gps"
        assert received[0].action == "STARTED"
        assert received[1].action == "STOPPED"

    def test_on_span_callback(self):
        received: list[EventSpan] = []
        capture = EventCapture(on_span=received.append)

        capture.feed(_b("gps", 1000))
        assert len(received) == 0

        capture.feed(_e("gps", 5001000))
        assert len(received) == 1
        assert received[0].name == "gps"


# ── Event names ──────────────────────────────────────────────────────


class TestEventNames:
    def test_event_names(self):
        capture = EventCapture()

        capture.feed(_b("gps", 0))
        capture.feed(_b("imu", 1000000))
        capture.feed(_e("gps", 2000000))

        assert capture.event_names == {"gps", "imu"}

    def test_event_names_empty(self):
        capture = EventCapture()
        assert capture.event_names == set()


# ── Reset ────────────────────────────────────────────────────────────


class TestReset:
    def test_reset_clears_everything(self):
        capture = EventCapture()

        capture.feed(_b("gps", 1000))
        capture.feed(_e("gps", 5001000))
        capture.feed(_b("imu", 6000000))

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
            '{"ph":"B","ts":1234567,"name":"peripheral_cycle","pid":1,"tid":1}',
            "T=1.234600 ALL_OFF_STARTED",  # legacy T= markers are ignored
            '{"ph":"B","ts":1234600,"name":"all_off","pid":1,"tid":1}',
        ]

        for line in lines:
            clock.advance(0.01)
            capture.feed(line)

        # Should capture Chrome JSON lines, not T= markers
        assert len(capture.events) == 2
        assert capture.events[0].name == "peripheral_cycle"
        assert capture.events[1].name == "all_off"

    def test_full_cycle_spans(self):
        """Full cycle: baseline -> GPS -> IMU -> baseline."""
        capture = EventCapture()

        events = [
            _b("peripheral_cycle", 0),
            _b("all_off", 1000),
            _e("all_off", 5001000),
            _b("gps", 5010000),
            _e("gps", 10010000),
            _b("imu", 10020000),
            _e("imu", 15020000),
            _b("all_off", 15030000),
            _e("all_off", 20030000),
            _e("peripheral_cycle", 20031000),
        ]

        for line in events:
            capture.feed(line)

        assert len(capture.spans) == 5  # cycle, 2x all_off, gps, imu

        # Check peripheral durations
        gps = [s for s in capture.spans if s.name == "gps"][0]
        imu = [s for s in capture.spans if s.name == "imu"][0]

        assert gps.device_duration_s == pytest.approx(5.0)
        assert imu.device_duration_s == pytest.approx(5.0)

        # all_off appears twice — both should be captured
        all_off_spans = [s for s in capture.spans if s.name == "all_off"]
        assert len(all_off_spans) == 2


# ── Timestamp wrap (uint32_t µs wraps every ~71.58 min) ──────────────

WRAP = 1 << 32  # 4_294_967_296 µs ≈ 71.58 minutes


class TestTimestampWrap:
    """SerialTracer emits uint32_t µs timestamps that wrap at 2**32.

    EventCapture detects ts < previous_ts and adds 2**32 to subsequent
    events so downstream (Perfetto etc.) sees a monotonic timeline.
    """

    def test_no_wrap_monotonic_input_unchanged(self):
        capture = EventCapture()
        capture.feed(_b("a", 1_000_000))
        capture.feed(_b("b", 2_000_000))
        capture.feed(_b("c", 3_000_000))

        assert [e.device_timestamp_s for e in capture.events] == [1.0, 2.0, 3.0]

    def test_single_wrap_adds_2_32(self):
        # Last pre-wrap ts at ~71 min, first post-wrap at ~0.
        pre = WRAP - 1_000_000            # 1 sec before wrap
        post = 500_000                    # 0.5 sec after wrap

        capture = EventCapture()
        capture.feed(_b("long_work", pre))
        capture.feed(_e("long_work", post))

        pre_s = pre / 1_000_000
        post_adjusted_s = (post + WRAP) / 1_000_000

        ts_values = [e.device_timestamp_s for e in capture.events]
        assert ts_values[0] == pytest.approx(pre_s)
        assert ts_values[1] == pytest.approx(post_adjusted_s)
        # Duration across wrap is 1.5 s, not ~-4294 s
        assert capture.spans[0].device_duration_s == pytest.approx(1.5)

    def test_multiple_wraps_accumulate(self):
        # Each wrap must be a *large* backwards step (> 2**31 µs).
        # Simulate two full rollovers: near-end → near-start → near-end → near-start.
        capture = EventCapture()
        raw_ts = [
            1_000,                # start of period 1
            WRAP - 1_000,         # near end of period 1
            1_000,                # wrapped → period 2 (wrap 1)
            WRAP - 1_000,         # near end of period 2
            1_000,                # wrapped → period 3 (wrap 2)
        ]
        for i, ts in enumerate(raw_ts):
            capture.feed(_b(f"s{i}", ts))

        expected_us = [
            1_000,
            WRAP - 1_000,
            1_000 + WRAP,
            WRAP - 1_000 + WRAP,
            1_000 + 2 * WRAP,
        ]
        observed = [e.device_timestamp_s * 1_000_000 for e in capture.events]
        for obs, exp in zip(observed, expected_us):
            assert obs == pytest.approx(exp)

    def test_small_backwards_step_is_not_wrap(self):
        # Dual-core ESP32: events from different cores can arrive with
        # small backwards skew. Threshold: only backwards steps > 2**31 µs
        # (~35.8 min) count as a wrap.
        capture = EventCapture()
        capture.feed(_b("gps", 1_000_500))   # from core A
        capture.feed(_b("imu", 1_000_000))   # from core B, 500 µs behind — NOT a wrap
        capture.feed(_b("gps", 2_000_000))

        ts_values = [e.device_timestamp_s for e in capture.events]
        # Raw values passed through unchanged; no +2**32 anywhere.
        assert ts_values == [pytest.approx(1.0005), pytest.approx(1.0), pytest.approx(2.0)]

    def test_wrap_does_not_trigger_on_equal_timestamps(self):
        # Equal ts is common for back-to-back events (same sample). Not a wrap.
        capture = EventCapture()
        capture.feed(_b("a", 500_000))
        capture.feed(_e("a", 500_000))
        capture.feed(_b("b", 500_000))

        assert all(e.device_timestamp_s == 0.5 for e in capture.events)

    def test_wrap_produces_monotonic_stream(self):
        # Feed a mix of events spanning a single wrap; resulting
        # device_timestamp_s must be strictly non-decreasing. The
        # pre-wrap tail is close to 2**32, the post-wrap start is small;
        # the step between WRAP - 5 and 0 is a wrap (large backwards).
        capture = EventCapture()
        raw = [100, 2_000_000, WRAP - 10, WRAP - 5, 0, 100, 1_000_000]
        for i, ts in enumerate(raw):
            capture.feed(_b(f"s{i}", ts))

        adjusted = [e.device_timestamp_s for e in capture.events]
        assert adjusted == sorted(adjusted)
