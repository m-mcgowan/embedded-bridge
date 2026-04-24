"""Capture Chrome JSON trace events from embedded-tracer serial output.

Parses Chrome JSON lines emitted by SerialTracer — scope begin/end events
(``ph:"B"``/``ph:"E"``) with microsecond timestamps. Maintains a log of
timestamped events and pairs them into spans.

Bridges between embedded-tracer serial output and ppk2-python's
EventMapper channel encoding — the same serial stream can feed both
Perfetto trace collection and PPK2 power attribution.

Usage::

    capture = EventCapture()
    capture.feed('{"ph":"B","ts":1600,"name":"gps","pid":1,"tid":1}')
    capture.feed('{"ph":"E","ts":4200,"name":"gps","pid":1,"tid":1}')

    for span in capture.spans:
        print(f"{span.name}: {span.duration_s:.3f}s")
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)

# SerialTracer emits uint32_t µs timestamps from embedded-trace; they wrap
# every 2**32 µs ≈ 71.58 minutes of continuous tracer activity. The library
# is deliberately wrap-unaware on-device (would need shared state with race
# hazards across tasks). Host-side wrap detection restores monotonicity.
#
# Only a *large* backwards step counts as a wrap — on dual-core ESP32 two
# tasks on different cores can emit events whose timestamps arrive at the
# Serial line with slight backwards skew. Threshold: half the wrap period
# (2**31 µs ≈ 35.8 min). Real wraps are always close to -2**32; jitter
# is always a few ms.
#
# See embedded-trace/docs/design.md#timestamp-wrap.
_TIMESTAMP_WRAP_US = 1 << 32
_WRAP_THRESHOLD_US = 1 << 31


@dataclass(frozen=True)
class TraceEvent:
    """A single Chrome JSON trace event from the device.

    Args:
        name: Event/scope name (e.g. "gps_fix", "imu_sample").
        action: "STARTED" or "STOPPED".
        device_timestamp_s: Timestamp from the ``ts`` field (converted to seconds).
        host_timestamp_s: ``time.monotonic()`` when the line was received.
    """

    name: str
    action: str
    device_timestamp_s: float
    host_timestamp_s: float


@dataclass(frozen=True)
class EventSpan:
    """A matched START/STOP pair for a single event.

    Args:
        name: Event name.
        start: The STARTED event.
        stop: The STOPPED event.
    """

    name: str
    start: TraceEvent
    stop: TraceEvent

    @property
    def device_duration_s(self) -> float:
        """Duration in seconds based on device timestamps."""
        return self.stop.device_timestamp_s - self.start.device_timestamp_s

    @property
    def host_duration_s(self) -> float:
        """Duration in seconds based on host timestamps."""
        return self.stop.host_timestamp_s - self.start.host_timestamp_s


class EventCapture:
    """Receiver that captures Chrome JSON trace events from embedded-tracer.

    Satisfies the ``Receiver`` protocol — feed it lines from any source.

    Args:
        clock: Callable returning monotonic time (default: ``time.monotonic``).
            Override for deterministic testing.
        on_event: Optional callback invoked for each parsed event.
        on_span: Optional callback invoked when a START/STOP pair completes.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        on_event: Callable[[TraceEvent], None] | None = None,
        on_span: Callable[[EventSpan], None] | None = None,
    ) -> None:
        self._clock = clock
        self._on_event = on_event
        self._on_span = on_span
        self._events: list[TraceEvent] = []
        self._spans: list[EventSpan] = []
        # Pending START events keyed by name (most recent wins)
        self._pending: dict[str, TraceEvent] = {}
        # Wrap detection: uint32_t µs timestamps wrap every ~71.58 min.
        # See _TIMESTAMP_WRAP_US.
        self._last_raw_ts_us: int | None = None
        self._wrap_count: int = 0

    def feed(self, message: bytes | str) -> None:
        """Consume a line of device output.

        Lines containing Chrome JSON with ``ph:"B"`` or ``ph:"E"`` are
        captured. All other lines are silently ignored.
        """
        if isinstance(message, bytes):
            try:
                message = message.decode("utf-8", errors="replace")
            except Exception:
                return

        line = message.strip()
        if not line.startswith("{"):
            return

        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return

        ph = obj.get("ph")
        if ph not in ("B", "E"):
            return

        name = obj.get("name")
        if not name:
            return

        raw_ts_us = obj.get("ts", 0)
        # Detect uint32_t wrap: only a *large* backwards step counts, to
        # avoid false wraps from dual-core jitter. See _WRAP_THRESHOLD_US.
        if (
            self._last_raw_ts_us is not None
            and raw_ts_us - self._last_raw_ts_us < -_WRAP_THRESHOLD_US
        ):
            self._wrap_count += 1
            logger.info(
                "EventCapture: timestamp wrap detected (raw %d vs previous %d) — "
                "wrap count now %d, adding %d µs to subsequent events",
                raw_ts_us,
                self._last_raw_ts_us,
                self._wrap_count,
                self._wrap_count * _TIMESTAMP_WRAP_US,
            )
        self._last_raw_ts_us = raw_ts_us
        adjusted_ts_us = raw_ts_us + self._wrap_count * _TIMESTAMP_WRAP_US

        action = "STARTED" if ph == "B" else "STOPPED"
        device_ts = adjusted_ts_us / 1_000_000  # µs → seconds
        host_ts = self._clock()

        event = TraceEvent(
            name=name,
            action=action,
            device_timestamp_s=device_ts,
            host_timestamp_s=host_ts,
        )
        self._events.append(event)

        if self._on_event:
            self._on_event(event)

        if action == "STARTED":
            if name in self._pending:
                logger.warning(
                    "EventCapture: duplicate START for '%s' "
                    "(previous START at device_t=%.6f)",
                    name,
                    self._pending[name].device_timestamp_s,
                )
            self._pending[name] = event
        elif action == "STOPPED":
            start = self._pending.pop(name, None)
            if start is None:
                logger.warning(
                    "EventCapture: STOP for '%s' without matching START",
                    name,
                )
            else:
                span = EventSpan(name=name, start=start, stop=event)
                self._spans.append(span)
                if self._on_span:
                    self._on_span(span)

    @property
    def events(self) -> list[TraceEvent]:
        """All captured events in order."""
        return list(self._events)

    @property
    def spans(self) -> list[EventSpan]:
        """All completed START/STOP pairs in order."""
        return list(self._spans)

    @property
    def pending(self) -> dict[str, TraceEvent]:
        """Events that have started but not yet stopped."""
        return dict(self._pending)

    @property
    def event_names(self) -> set[str]:
        """Unique event names seen so far."""
        return {e.name for e in self._events}

    def reset(self) -> None:
        """Clear all captured events and spans."""
        self._events.clear()
        self._spans.clear()
        self._pending.clear()
        self._last_raw_ts_us = None
        self._wrap_count = 0
