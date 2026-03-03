"""Tests for SleepWakeMonitor."""

import re

from embedded_bridge.receivers.sleep_wake import (
    ESP32_SLEEP_PATTERNS,
    ESP32_WAKE_PATTERNS,
    SleepEvent,
    SleepPattern,
    SleepWakeMonitor,
)


class FakeClock:
    """Injectable clock for deterministic tests."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakePort:
    """Simulates a port path that can appear/disappear."""

    def __init__(self, exists: bool = True):
        self._exists = exists
        self.path = "/dev/fake_port"

    @property
    def exists(self) -> bool:
        return self._exists

    @exists.setter
    def exists(self, value: bool) -> None:
        self._exists = value


def make_monitor_with_fake_port(fake_port, **kwargs):
    """Create a SleepWakeMonitor with a FakePort injected via monkeypatch-style."""
    import os

    original_exists = os.path.exists

    def patched_exists(path):
        if path == fake_port.path:
            return fake_port.exists
        return original_exists(path)

    # We'll use a wrapper approach - the monitor checks os.path.exists internally
    # so we need to patch it. Instead, let's create the monitor and patch the method.
    monitor = SleepWakeMonitor(port_path=fake_port.path, **kwargs)

    # Monkey-patch os.path.exists for check_port calls
    import unittest.mock

    patcher = unittest.mock.patch("os.path.exists", side_effect=patched_exists)
    mock = patcher.start()
    return monitor, patcher


# --- Test sleep detection via patterns ---


class TestSleepDetection:
    def test_sleep_pattern_with_duration_and_reason(self):
        clock = FakeClock(100.0)
        monitor = SleepWakeMonitor(clock=clock)

        monitor.feed("Going to sleep. sleep for 60 seconds, because RFID unavailable")

        assert monitor.state == "sleeping"
        assert monitor.sleep_event is not None
        assert monitor.sleep_event.duration == 60.0
        assert monitor.sleep_event.reason == "RFID unavailable"
        assert monitor.sleep_event.timestamp == 100.0

    def test_sleep_pattern_with_duration_only(self):
        monitor = SleepWakeMonitor()
        monitor.feed("sleep for 30 seconds")

        assert monitor.state == "sleeping"
        assert monitor.sleep_event.duration == 30.0
        assert monitor.sleep_event.reason is None

    def test_sleep_pattern_singular_second(self):
        monitor = SleepWakeMonitor()
        monitor.feed("sleep for 1 second")

        assert monitor.state == "sleeping"
        assert monitor.sleep_event.duration == 1.0

    def test_sleep_pattern_case_insensitive(self):
        monitor = SleepWakeMonitor()
        monitor.feed("SLEEP FOR 60 SECONDS")

        assert monitor.state == "sleeping"

    def test_normal_output_does_not_trigger(self):
        monitor = SleepWakeMonitor()
        monitor.feed("Wi-Fi connected")
        monitor.feed("Sensor reading: 42")

        assert monitor.state == "awake"
        assert monitor.sleep_event is None

    def test_bytes_input_decoded(self):
        monitor = SleepWakeMonitor()
        monitor.feed(b"sleep for 10 seconds")

        assert monitor.state == "sleeping"
        assert monitor.sleep_event.duration == 10.0

    def test_bytes_with_invalid_utf8(self):
        monitor = SleepWakeMonitor()
        monitor.feed(b"sleep for 5 seconds \xff\xfe")

        assert monitor.state == "sleeping"

    def test_custom_sleep_patterns(self):
        custom = [
            SleepPattern("custom", re.compile(r"entering low power mode")),
        ]
        monitor = SleepWakeMonitor(sleep_patterns=custom)
        monitor.feed("entering low power mode")

        assert monitor.state == "sleeping"
        # Custom pattern has no capture groups, so duration/reason are None
        assert monitor.sleep_event.duration is None
        assert monitor.sleep_event.reason is None

    def test_on_sleep_callback(self):
        events = []
        monitor = SleepWakeMonitor(on_sleep=events.append)
        monitor.feed("sleep for 20 seconds, because idle timeout")

        assert len(events) == 1
        assert events[0].duration == 20.0
        assert events[0].reason == "idle timeout"

    def test_sleep_pattern_in_middle_of_line(self):
        monitor = SleepWakeMonitor()
        monitor.feed("[INFO] Device will sleep for 45 seconds, because scheduled")

        assert monitor.state == "sleeping"
        assert monitor.sleep_event.duration == 45.0


class TestWakeDetection:
    def test_wake_pattern_after_sleep(self):
        monitor = SleepWakeMonitor()
        monitor.feed("sleep for 10 seconds")
        assert monitor.state == "sleeping"

        monitor.feed("rst:0x5 (DEEPSLEEP_RESET)")
        assert monitor.state == "awake"

    def test_warm_boot_pattern(self):
        monitor = SleepWakeMonitor()
        monitor.feed("sleep for 10 seconds")
        monitor.feed("warm boot detected")

        assert monitor.state == "awake"

    def test_wake_pattern_case_insensitive(self):
        monitor = SleepWakeMonitor()
        monitor.feed("sleep for 10 seconds")
        monitor.feed("WARM BOOT")

        assert monitor.state == "awake"

    def test_on_wake_callback(self):
        wakes = []
        monitor = SleepWakeMonitor(on_wake=lambda: wakes.append(True))
        monitor.feed("sleep for 10 seconds")
        monitor.feed("warm boot")

        assert len(wakes) == 1

    def test_wake_without_prior_sleep_ignored(self):
        monitor = SleepWakeMonitor()
        monitor.feed("warm boot")

        assert monitor.state == "awake"

    def test_custom_wake_patterns(self):
        custom_wake = [SleepPattern("custom_wake", re.compile(r"SYSTEM READY"))]
        monitor = SleepWakeMonitor(wake_patterns=custom_wake)
        monitor.feed("sleep for 10 seconds")
        monitor.feed("SYSTEM READY")

        assert monitor.state == "awake"

    def test_multiple_sleep_wake_cycles(self):
        events = []
        wakes = []
        monitor = SleepWakeMonitor(
            on_sleep=events.append,
            on_wake=lambda: wakes.append(True),
        )

        monitor.feed("sleep for 10 seconds")
        assert monitor.state == "sleeping"
        monitor.feed("warm boot")
        assert monitor.state == "awake"

        monitor.feed("sleep for 20 seconds")
        assert monitor.state == "sleeping"
        monitor.feed("warm boot")
        assert monitor.state == "awake"

        assert len(events) == 2
        assert len(wakes) == 2


class TestPortDisappearance:
    def test_port_disappearance_triggers_sleeping(self):
        port = FakePort(exists=True)
        monitor, patcher = make_monitor_with_fake_port(port)
        try:
            assert monitor.state == "awake"

            port.exists = False
            monitor.check_port()

            assert monitor.state == "sleeping"
        finally:
            patcher.stop()

    def test_port_reappearance_triggers_waking(self):
        port = FakePort(exists=True)
        monitor, patcher = make_monitor_with_fake_port(port)
        try:
            port.exists = False
            monitor.check_port()
            assert monitor.state == "sleeping"

            port.exists = True
            monitor.check_port()
            assert monitor.state == "waking"
        finally:
            patcher.stop()

    def test_wake_pattern_after_port_reappearance(self):
        port = FakePort(exists=True)
        monitor, patcher = make_monitor_with_fake_port(port)
        try:
            port.exists = False
            monitor.check_port()

            port.exists = True
            monitor.check_port()
            assert monitor.state == "waking"

            monitor.feed("warm boot")
            assert monitor.state == "awake"
        finally:
            patcher.stop()

    def test_sleep_event_from_port_disappearance(self):
        clock = FakeClock(50.0)
        port = FakePort(exists=True)
        monitor, patcher = make_monitor_with_fake_port(port, clock=clock)
        try:
            port.exists = False
            monitor.check_port()

            assert monitor.sleep_event is not None
            assert monitor.sleep_event.duration is None
            assert monitor.sleep_event.reason is None
            assert monitor.sleep_event.timestamp == 50.0
        finally:
            patcher.stop()

    def test_port_gone_while_already_sleeping_no_change(self):
        port = FakePort(exists=True)
        monitor, patcher = make_monitor_with_fake_port(port)
        try:
            monitor.feed("sleep for 10 seconds")
            assert monitor.state == "sleeping"

            port.exists = False
            monitor.check_port()
            # Already sleeping, should stay sleeping (not re-trigger)
            assert monitor.state == "sleeping"
        finally:
            patcher.stop()


class TestNoPortPath:
    def test_check_port_noop_without_port_path(self):
        monitor = SleepWakeMonitor(port_path=None)
        monitor.check_port()
        assert monitor.state == "awake"

    def test_pattern_only_mode(self):
        monitor = SleepWakeMonitor(port_path=None)
        monitor.feed("sleep for 10 seconds")
        assert monitor.state == "sleeping"
        monitor.feed("warm boot")
        assert monitor.state == "awake"


class TestStateProperty:
    def test_initial_state_is_awake(self):
        monitor = SleepWakeMonitor()
        assert monitor.state == "awake"

    def test_state_after_sleep_pattern(self):
        monitor = SleepWakeMonitor()
        monitor.feed("sleep for 10 seconds")
        assert monitor.state == "sleeping"

    def test_state_after_wake(self):
        monitor = SleepWakeMonitor()
        monitor.feed("sleep for 10 seconds")
        monitor.feed("warm boot")
        assert monitor.state == "awake"


class TestSleepEvent:
    def test_none_initially(self):
        monitor = SleepWakeMonitor()
        assert monitor.sleep_event is None

    def test_populated_after_sleep(self):
        clock = FakeClock(42.0)
        monitor = SleepWakeMonitor(clock=clock)
        monitor.feed("sleep for 30 seconds, because idle timeout")

        event = monitor.sleep_event
        assert event is not None
        assert event.duration == 30.0
        assert event.reason == "idle timeout"
        assert event.timestamp == 42.0

    def test_preserved_after_wake(self):
        monitor = SleepWakeMonitor()
        monitor.feed("sleep for 10 seconds")
        monitor.feed("warm boot")

        # Sleep event is still accessible after wake
        assert monitor.sleep_event is not None

    def test_updated_on_second_sleep(self):
        clock = FakeClock(0.0)
        monitor = SleepWakeMonitor(clock=clock)

        monitor.feed("sleep for 10 seconds")
        clock.advance(15.0)
        monitor.feed("warm boot")

        clock.advance(5.0)
        monitor.feed("sleep for 20 seconds")

        assert monitor.sleep_event.duration == 20.0
        assert monitor.sleep_event.timestamp == 20.0


class TestReset:
    def test_reset_clears_state(self):
        monitor = SleepWakeMonitor()
        monitor.feed("sleep for 10 seconds")
        assert monitor.state == "sleeping"

        monitor.reset()
        assert monitor.state == "awake"
        assert monitor.sleep_event is None

    def test_reset_from_waking(self):
        port = FakePort(exists=True)
        monitor, patcher = make_monitor_with_fake_port(port)
        try:
            port.exists = False
            monitor.check_port()
            port.exists = True
            monitor.check_port()
            assert monitor.state == "waking"

            monitor.reset()
            assert monitor.state == "awake"
        finally:
            patcher.stop()


class TestReceiverProtocol:
    def test_satisfies_receiver_protocol(self):
        from embedded_bridge.receivers.base import Receiver

        monitor = SleepWakeMonitor()
        assert isinstance(monitor, Receiver)
