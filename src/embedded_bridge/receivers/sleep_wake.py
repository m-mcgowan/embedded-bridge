"""Sleep/wake transition detection for embedded devices.

Monitors device output and port state to detect when a device enters
deep sleep and when it wakes. Works standalone — feed it lines from any
source and optionally call ``check_port()`` to detect USB-CDC
disappearance.
"""

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SleepEvent:
    """Details of a detected sleep transition.

    Args:
        duration: Expected sleep duration in seconds, if reported.
        reason: Why the device is sleeping, if reported.
        timestamp: ``time.monotonic()`` value when detected.
    """

    duration: float | None
    reason: str | None
    timestamp: float


@dataclass(frozen=True)
class SleepPattern:
    """A regex pattern for detecting sleep or wake transitions.

    Args:
        name: Human-readable label (e.g. "esp32_sleep").
        pattern: Compiled regex. For sleep patterns, group 1 is duration
            (seconds) and group 2 is reason (both optional).
    """

    name: str
    pattern: re.Pattern[str]


ESP32_SLEEP_PATTERNS: list[SleepPattern] = [
    SleepPattern(
        "esp32_sleep",
        re.compile(r"sleep for (\d+) seconds?,?\s*(?:because\s+)?(.+)?", re.IGNORECASE),
    ),
]

ESP32_WAKE_PATTERNS: list[SleepPattern] = [
    SleepPattern("esp32_warm_boot", re.compile(r"warm boot", re.IGNORECASE)),
    SleepPattern("esp32_wakeup", re.compile(r"rst:0x5.*DEEPSLEEP_RESET", re.IGNORECASE)),
]


class SleepWakeMonitor:
    """Monitors device output for sleep/wake transitions.

    Detects sleep via serial pattern matching (firmware announces sleep)
    or port disappearance (USB-CDC powers down during deep sleep). Detects
    wake via port reappearance followed by a wake pattern in output.

    ``check_port()`` must be called periodically by the caller to detect
    port state changes, since ``feed()`` only sees serial output.

    Args:
        port_path: Device port path for USB-CDC disappearance detection.
            None disables port checking (pattern-only mode).
        sleep_patterns: Patterns indicating the device is entering sleep.
            Defaults to ESP32_SLEEP_PATTERNS.
        wake_patterns: Patterns indicating the device has woken.
            Defaults to ESP32_WAKE_PATTERNS.
        on_sleep: Callback invoked when sleep is detected.
        on_wake: Callback invoked when wake is detected.
        clock: Callable returning monotonic time. Injectable for testing.
    """

    def __init__(
        self,
        port_path: str | None = None,
        sleep_patterns: list[SleepPattern] | None = None,
        wake_patterns: list[SleepPattern] | None = None,
        on_sleep: Callable[[SleepEvent], None] | None = None,
        on_wake: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._port_path = port_path
        self._sleep_patterns = (
            sleep_patterns if sleep_patterns is not None else list(ESP32_SLEEP_PATTERNS)
        )
        self._wake_patterns = (
            wake_patterns if wake_patterns is not None else list(ESP32_WAKE_PATTERNS)
        )
        self._on_sleep = on_sleep
        self._on_wake = on_wake
        self._clock = clock

        self._state: str = "awake"
        self._sleep_event: SleepEvent | None = None
        self._port_was_gone: bool = False

    def feed(self, message: bytes | str) -> None:
        """Feed a message from the device.

        Checks for sleep/wake patterns. If *message* is ``bytes``, it is
        decoded as UTF-8 (replacing errors).

        Args:
            message: A line of device output.
        """
        line = (
            message.decode("utf-8", errors="replace")
            if isinstance(message, bytes)
            else message
        )

        if self._state == "awake":
            self._check_sleep_patterns(line)
        elif self._state in ("sleeping", "waking"):
            self._check_wake_patterns(line)

    def check_port(self) -> None:
        """Check port existence for USB-CDC disappearance/reappearance.

        Call this periodically. When the port disappears, the monitor
        transitions to sleeping. When it reappears, the monitor transitions
        to waking (awaiting a wake pattern to confirm awake).
        """
        if self._port_path is None:
            return

        port_exists = os.path.exists(self._port_path)

        if self._state == "awake" and not port_exists:
            logger.info("Port disappeared: %s — device entering sleep", self._port_path)
            self._port_was_gone = True
            self._transition_to_sleeping(duration=None, reason=None)

        elif self._state == "sleeping" and port_exists and self._port_was_gone:
            logger.info("Port reappeared: %s — device waking", self._port_path)
            self._state = "waking"

    @property
    def state(self) -> str:
        """Current state: ``"awake"``, ``"sleeping"``, or ``"waking"``."""
        return self._state

    @property
    def sleep_event(self) -> SleepEvent | None:
        """The current/last sleep event, or None."""
        return self._sleep_event

    def reset(self) -> None:
        """Reset to awake state, clearing all sleep/wake state."""
        self._state = "awake"
        self._sleep_event = None
        self._port_was_gone = False

    def _check_sleep_patterns(self, line: str) -> None:
        """Check line against sleep patterns."""
        for sp in self._sleep_patterns:
            match = sp.pattern.search(line)
            if match:
                duration = None
                reason = None
                try:
                    duration = float(match.group(1))
                except (IndexError, TypeError, ValueError):
                    pass
                try:
                    reason = match.group(2)
                    if reason:
                        reason = reason.strip()
                    if not reason:
                        reason = None
                except (IndexError, TypeError):
                    pass

                logger.info("Sleep detected via %s: duration=%s, reason=%s", sp.name, duration, reason)
                self._transition_to_sleeping(duration, reason)
                return

    def _check_wake_patterns(self, line: str) -> None:
        """Check line against wake patterns."""
        for wp in self._wake_patterns:
            if wp.pattern.search(line):
                logger.info("Wake detected via %s", wp.name)
                self._state = "awake"
                self._port_was_gone = False
                if self._on_wake is not None:
                    self._on_wake()
                return

    def _transition_to_sleeping(self, duration: float | None, reason: str | None) -> None:
        """Transition to sleeping state."""
        event = SleepEvent(
            duration=duration,
            reason=reason,
            timestamp=self._clock(),
        )
        self._sleep_event = event
        self._state = "sleeping"
        if self._on_sleep is not None:
            self._on_sleep(event)
