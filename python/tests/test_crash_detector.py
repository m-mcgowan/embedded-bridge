"""Tests for crash detection."""

from embedded_bridge.receivers.crash_detector import (
    CrashDetector,
    CrashEvent,
    CrashPattern,
    ESP32_PATTERNS,
)


class FakeClock:
    """Controllable clock for deterministic hang detection tests."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------


class TestCrashPatternDetection:
    def test_backtrace_triggers_crash(self):
        d = CrashDetector(crash_line_limit=1)
        d.feed("Backtrace: 0x400d1234:0x3ffb5e70")
        assert d.triggered
        assert d.crash.pattern == "Backtrace:"

    def test_guru_meditation_triggers_crash(self):
        d = CrashDetector(crash_line_limit=1)
        d.feed("Guru Meditation Error: Core  0 panic'ed (LoadProhibited)")
        assert d.triggered
        assert d.crash.pattern == "Guru Meditation Error"

    def test_watchdog_triggers_crash(self):
        d = CrashDetector(crash_line_limit=1)
        d.feed("Task watchdog got triggered. The following tasks did not reset")
        assert d.triggered

    def test_wdt_reset_triggers_crash(self):
        d = CrashDetector(crash_line_limit=1)
        d.feed("rst:0x8 (TG1WDT_SYS_RST),boot:0x13 WDT reset")
        assert d.triggered

    def test_abort_triggers_crash(self):
        d = CrashDetector(crash_line_limit=1)
        d.feed("abort() was called at PC 0x400d1234")
        assert d.triggered

    def test_panic_abort_triggers_crash(self):
        d = CrashDetector(crash_line_limit=1)
        d.feed("panic_abort: some message")
        assert d.triggered

    def test_normal_output_does_not_trigger(self):
        d = CrashDetector()
        d.feed("WiFi connected")
        d.feed("GPS fix acquired in 2.3s")
        d.feed("[INFO] Heap free: 142000 bytes")
        assert not d.triggered

    def test_pattern_in_middle_of_line(self):
        d = CrashDetector(crash_line_limit=1)
        d.feed("E (12345) some_module: Guru Meditation Error: something")
        assert d.triggered

    def test_crash_buffers_subsequent_lines(self):
        d = CrashDetector(crash_line_limit=5)
        d.feed("Backtrace: 0x400d1234:0x3ffb5e70")
        assert not d.triggered  # not finalized yet — need 4 more lines

        d.feed("0x400d1234:0x3ffb5e70 0x400d5678:0x3ffb5e80")
        d.feed("0x400d9abc:0x3ffb5e90")
        d.feed("")
        d.feed("ELF file SHA256: abcdef1234567890")
        assert d.triggered
        assert len(d.crash.lines) == 5
        assert "Backtrace:" in d.crash.lines[0]

    def test_crash_event_contains_reason(self):
        d = CrashDetector(crash_line_limit=1)
        d.feed("Task watchdog got triggered")
        assert "watchdog" in d.crash.reason

    def test_bytes_input_decoded(self):
        d = CrashDetector(crash_line_limit=1)
        d.feed(b"Backtrace: 0x400d1234")
        assert d.triggered
        assert d.crash.lines[0] == "Backtrace: 0x400d1234"

    def test_bytes_with_invalid_utf8(self):
        d = CrashDetector(crash_line_limit=1)
        d.feed(b"Backtrace: \xff\xfe 0x400d1234")
        assert d.triggered

    def test_feed_after_finalized_is_ignored(self):
        d = CrashDetector(crash_line_limit=1)
        d.feed("Backtrace: crash")
        assert d.triggered
        lines_before = len(d.crash.lines)
        d.feed("more output")
        assert len(d.crash.lines) == lines_before


# ---------------------------------------------------------------------------
# Callback
# ---------------------------------------------------------------------------


class TestCrashCallback:
    def test_on_crash_called_when_crash_detected(self):
        events: list[CrashEvent] = []
        d = CrashDetector(crash_line_limit=1, on_crash=events.append)
        d.feed("Backtrace: 0x400d1234")
        assert len(events) == 1

    def test_on_crash_receives_crash_event(self):
        events: list[CrashEvent] = []
        d = CrashDetector(crash_line_limit=1, on_crash=events.append)
        d.feed("Guru Meditation Error: panic")
        assert events[0].pattern == "Guru Meditation Error"
        assert len(events[0].lines) == 1

    def test_on_crash_not_called_for_normal_output(self):
        events: list[CrashEvent] = []
        d = CrashDetector(on_crash=events.append)
        d.feed("normal line 1")
        d.feed("normal line 2")
        assert len(events) == 0

    def test_on_crash_called_on_silent_hang(self):
        clock = FakeClock()
        events: list[CrashEvent] = []
        d = CrashDetector(
            silent_timeout=10.0,
            on_crash=events.append,
            clock=clock,
        )
        d.feed("some output")
        clock.advance(11.0)
        d.check_timeout()
        assert len(events) == 1
        assert events[0].pattern is None
        assert "Silent hang" in events[0].reason


# ---------------------------------------------------------------------------
# Silent hang detection
# ---------------------------------------------------------------------------


class TestSilentHangDetection:
    def test_silent_hang_after_timeout(self):
        clock = FakeClock()
        d = CrashDetector(silent_timeout=45.0, clock=clock)

        d.feed("boot message")
        clock.advance(46.0)
        d.check_timeout()

        assert d.triggered
        assert d.crash.pattern is None
        assert "45" in d.crash.reason or "46" in d.crash.reason

    def test_output_resets_silent_timer(self):
        clock = FakeClock()
        d = CrashDetector(silent_timeout=10.0, clock=clock)

        d.feed("line 1")
        clock.advance(9.0)
        d.feed("line 2")  # resets timer
        clock.advance(9.0)
        d.check_timeout()

        assert not d.triggered  # only 9s since last feed

    def test_silent_hang_disabled_when_none(self):
        clock = FakeClock()
        d = CrashDetector(silent_timeout=None, clock=clock)

        d.feed("something")
        clock.advance(1000.0)
        d.check_timeout()

        assert not d.triggered

    def test_check_timeout_before_any_feed(self):
        clock = FakeClock()
        d = CrashDetector(silent_timeout=10.0, clock=clock)
        clock.advance(100.0)
        d.check_timeout()
        assert not d.triggered  # no feed yet — nothing to time out on


# ---------------------------------------------------------------------------
# Custom patterns
# ---------------------------------------------------------------------------


class TestCustomPatterns:
    def test_custom_patterns_replace_defaults(self):
        custom = [CrashPattern("custom", "MY_CRASH_MARKER")]
        d = CrashDetector(patterns=custom, crash_line_limit=1)

        d.feed("Backtrace: 0x400d1234")  # ESP32 default — should NOT trigger
        assert not d.triggered

        d.feed("MY_CRASH_MARKER happened")
        assert d.triggered

    def test_empty_patterns_disables_pattern_detection(self):
        d = CrashDetector(patterns=[], silent_timeout=None)
        d.feed("Backtrace: 0x400d1234")
        d.feed("Guru Meditation Error: panic")
        assert not d.triggered


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_clears_crash_state(self):
        d = CrashDetector(crash_line_limit=1)
        d.feed("Backtrace: crash")
        assert d.triggered

        d.reset()
        assert not d.triggered
        assert d.crash is None

    def test_reset_clears_hang_timers(self):
        clock = FakeClock()
        d = CrashDetector(silent_timeout=10.0, clock=clock)

        d.feed("something")
        clock.advance(9.0)

        d.reset()
        clock.advance(5.0)
        d.check_timeout()
        assert not d.triggered  # timer was reset — no _last_feed_time

    def test_detects_crash_after_reset(self):
        d = CrashDetector(crash_line_limit=1)
        d.feed("Backtrace: first")
        assert d.triggered

        d.reset()
        d.feed("Backtrace: second")
        assert d.triggered
        assert "second" in d.crash.lines[0]


# ---------------------------------------------------------------------------
# Triggered property
# ---------------------------------------------------------------------------


class TestTriggeredProperty:
    def test_not_triggered_initially(self):
        d = CrashDetector()
        assert not d.triggered

    def test_triggered_after_crash(self):
        d = CrashDetector(crash_line_limit=1)
        d.feed("Backtrace: 0x400d1234")
        assert d.triggered

    def test_triggered_after_hang(self):
        clock = FakeClock()
        d = CrashDetector(silent_timeout=5.0, clock=clock)
        d.feed("output")
        clock.advance(6.0)
        d.check_timeout()
        assert d.triggered

    def test_not_triggered_after_reset(self):
        d = CrashDetector(crash_line_limit=1)
        d.feed("Backtrace: crash")
        d.reset()
        assert not d.triggered
