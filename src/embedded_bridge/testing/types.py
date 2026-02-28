"""Types for the embedded test protocol.

The test protocol uses two channels on the same serial stream:
- JSON lines for metadata (test catalog, start/end status)
- T= markers for timing-critical events (PPK_START, SLEEP, etc.)
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TestInfo:
    """A test advertised by the device in its catalog.

    Args:
        id: Stable identifier used in commands and host metadata.
        name: Human-readable description (for display + mismatch detection).
        group: Grouping key for running related tests together.
    """

    id: str
    name: str
    group: str = ""


@dataclass
class TestOutcome:
    """Result of running a single test via the protocol.

    Captures everything the host observed during test execution:
    markers, serial output, sleep monitoring, and final status.

    Args:
        test_id: The test that was run.
        status: Final status: "ok", "error", or "timeout".
        markers: Timing markers with host-monotonic timestamps.
            Keys include PPK_START, PPK_STOP, SLEEP (with duration),
            TEST_STARTED, TEST_STOPPED.
        serial_log: All serial lines received during the test.
        warnings: Anomalies detected (premature/late wake, missing markers).
        sleep_expected_s: Expected sleep duration from SLEEP marker, if any.
        sleep_actual_s: Measured sleep duration (port gone time), if any.
    """

    test_id: str
    status: str = "ok"
    markers: dict[str, float] = field(default_factory=dict)
    serial_log: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sleep_expected_s: float | None = None
    sleep_actual_s: float | None = None
