"""Crash and hang detection for embedded device output.

Monitors a stream of device output for crash indicators (backtraces,
watchdog resets, panics) and hangs (prolonged silence). Works standalone
— feed it lines from any source.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CrashPattern:
    """A substring pattern that indicates a device crash.

    Args:
        name: Human-readable category (e.g. "backtrace", "watchdog").
        pattern: Substring to match in device output lines.
    """

    name: str
    pattern: str


@dataclass
class CrashEvent:
    """Details of a detected crash or hang.

    Args:
        reason: Human-readable description of what was detected.
        pattern: The matched pattern string, or None for hangs.
        lines: Device output lines captured around the crash.
        timestamp: ``time.monotonic()`` value when detected.
    """

    reason: str
    pattern: str | None
    lines: list[str] = field(default_factory=list)
    timestamp: float = 0.0


# Default crash patterns for ESP32 / ESP-IDF.
ESP32_PATTERNS: list[CrashPattern] = [
    CrashPattern("backtrace", "Backtrace:"),
    CrashPattern("backtrace", "backtrace:"),
    CrashPattern("backtrace", "BACKTRACE:"),
    CrashPattern("guru_meditation", "Guru Meditation Error"),
    CrashPattern("panic_abort", "panic_abort"),
    CrashPattern("abort", "abort()"),
    CrashPattern("watchdog", "Task watchdog got triggered"),
    CrashPattern("watchdog", "WDT reset"),
]


class CrashDetector:
    """Monitors device output for crash indicators and hangs.

    Feed lines from any source — serial, PIO test runner, log file replay.
    When a crash pattern is detected, the detector buffers subsequent lines
    (to capture the backtrace) then triggers.

    Silent hang detection requires the caller to invoke ``check_timeout()``
    periodically when no messages arrive, since ``feed()`` is only called
    when there is output.

    Args:
        patterns: Crash patterns to detect. Defaults to ESP32_PATTERNS.
        silent_timeout: Seconds with no output before declaring a silent
            hang. None disables silent hang detection.
        crash_line_limit: Number of lines to buffer after a crash pattern
            is matched before declaring the crash complete.
        on_crash: Optional callback invoked when a crash is detected.
        clock: Callable returning monotonic time. Injectable for testing.
    """

    def __init__(
        self,
        patterns: list[CrashPattern] | None = None,
        silent_timeout: float | None = 45.0,
        crash_line_limit: int = 20,
        on_crash: Callable[[CrashEvent], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._patterns = patterns if patterns is not None else list(ESP32_PATTERNS)
        self._silent_timeout = silent_timeout
        self._crash_line_limit = crash_line_limit
        self._on_crash = on_crash
        self._clock = clock

        self._last_feed_time: float | None = None
        self._crash_detected = False
        self._crash_lines: list[str] = []
        self._crash_event: CrashEvent | None = None
        self._triggered_pattern: CrashPattern | None = None

    def feed(self, message: bytes | str) -> None:
        """Feed a message from the device.

        Checks for crash patterns and updates hang timers. If *message*
        is ``bytes``, it is decoded as UTF-8 (replacing errors) for
        pattern matching.

        Args:
            message: A line of device output.
        """
        if self._crash_event is not None:
            return  # already finalized

        now = self._clock()
        self._last_feed_time = now

        line = message.decode("utf-8", errors="replace") if isinstance(message, bytes) else message

        if self._crash_detected:
            self._crash_lines.append(line)
            if len(self._crash_lines) >= self._crash_line_limit:
                self._finalize_crash(now)
            return

        for cp in self._patterns:
            if cp.pattern in line:
                logger.warning("Crash detected: %s (%s)", cp.name, cp.pattern)
                self._crash_detected = True
                self._triggered_pattern = cp
                self._crash_lines.append(line)
                if self._crash_line_limit <= 1:
                    self._finalize_crash(now)
                return

    def check_timeout(self) -> None:
        """Check for silent hang.

        Call this periodically when no messages are arriving. It compares
        the time since the last ``feed()`` call against
        ``silent_timeout``.
        """
        if self._crash_event is not None:
            return
        if self._silent_timeout is None:
            return
        if self._last_feed_time is None:
            return  # no feed yet — nothing to time out on

        now = self._clock()
        elapsed = now - self._last_feed_time

        if elapsed >= self._silent_timeout:
            logger.warning("Silent hang detected: no output for %.1fs", elapsed)
            event = CrashEvent(
                reason=f"Silent hang: no output for {elapsed:.1f}s",
                pattern=None,
                lines=[],
                timestamp=now,
            )
            self._crash_event = event
            if self._on_crash is not None:
                self._on_crash(event)

    @property
    def triggered(self) -> bool:
        """True if a crash or hang has been detected and finalized."""
        return self._crash_event is not None

    @property
    def crash(self) -> CrashEvent | None:
        """The crash event, or None if no crash detected yet."""
        return self._crash_event

    def reset(self) -> None:
        """Reset all state. Clears crash detection and hang timers."""
        self._last_feed_time = None
        self._crash_detected = False
        self._crash_lines = []
        self._crash_event = None
        self._triggered_pattern = None

    def _finalize_crash(self, now: float) -> None:
        """Create the CrashEvent and invoke the callback."""
        cp = self._triggered_pattern
        event = CrashEvent(
            reason=f"Crash: {cp.name}" if cp else "Crash",
            pattern=cp.pattern if cp else None,
            lines=list(self._crash_lines),
            timestamp=now,
        )
        self._crash_event = event
        if self._on_crash is not None:
            self._on_crash(event)
