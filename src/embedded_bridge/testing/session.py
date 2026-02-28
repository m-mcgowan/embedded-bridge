"""Test session — orchestrates test discovery and execution over serial.

Manages the protocol lifecycle: discovery (SOH → catalog), execution
(STX → markers → outcome), and sleep monitoring. Consumers like
power-profiler or doctest runners provide the transport and act on the
results.

This module knows nothing about PPK2 or power measurement — it only
handles the serial protocol and timing markers.

Usage::

    from embedded_bridge.transport.serial import SerialTransport
    from embedded_bridge.testing import TestSession

    transport = SerialTransport("1.10")
    transport.connect()

    session = TestSession(transport)
    catalog = session.discover()

    for test in catalog:
        session.start_test(test.id)
        outcome = session.monitor()
        print(f"{test.id}: {outcome.status}")
"""

import codecs
import json
import logging
import os
import time
from typing import Callable

from ..transport import Transport
from .protocol import SOH, STX, parse_json_line, parse_marker
from .types import TestInfo, TestOutcome

logger = logging.getLogger(__name__)


class TestSession:
    """Manages a test session with an embedded device.

    Args:
        transport: Connected transport to the device.
        clock: Monotonic clock (injectable for testing).
    """

    def __init__(
        self,
        transport: Transport,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._clock = clock
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._line_buf: list[str] = []

    def discover(self, timeout: float = 5.0) -> list[TestInfo]:
        """Send SOH and parse the test catalog.

        Args:
            timeout: Max seconds to wait for the catalog response.

        Returns:
            List of available tests.

        Raises:
            TimeoutError: If no catalog received within timeout.
        """
        self._transport.write(SOH)
        logger.debug("Sent SOH (discover)")

        deadline = self._clock() + timeout
        while self._clock() < deadline:
            line = self._read_line(timeout=0.5)
            if line is None:
                continue

            obj = parse_json_line(line)
            if obj and obj.get("type") == "test_catalog":
                tests = []
                for t in obj.get("tests", []):
                    tests.append(TestInfo(
                        id=t["id"],
                        name=t.get("name", t["id"]),
                        group=t.get("group", ""),
                    ))
                logger.info("Discovered %d tests", len(tests))
                return tests

        raise TimeoutError(
            f"No test_catalog received within {timeout}s"
        )

    def start_test(self, test_id: str, timeout: float = 10.0) -> None:
        """Send STX + test ID. Blocks until TEST_STARTED marker received.

        Args:
            test_id: Test identifier from the catalog.
            timeout: Max seconds to wait for TEST_STARTED.

        Raises:
            TimeoutError: If TEST_STARTED not received within timeout.
        """
        cmd = STX + test_id.encode("ascii") + b"\n"
        self._transport.write(cmd)
        logger.debug("Sent STX + %s", test_id)

        deadline = self._clock() + timeout
        while self._clock() < deadline:
            line = self._read_line(timeout=0.5)
            if line is None:
                continue

            result = parse_marker(line)
            if result:
                _, payload = result
                if payload == f"TEST_STARTED:{test_id}":
                    logger.info("Test started: %s", test_id)
                    return

        raise TimeoutError(
            f"TEST_STARTED:{test_id} not received within {timeout}s"
        )

    def monitor(
        self,
        test_id: str,
        timeout: float = 120.0,
        port_poll_interval: float = 1.0,
    ) -> TestOutcome:
        """Monitor a running test until completion.

        Reads serial output, tracks T= markers, monitors port for sleep/wake
        transitions. Returns when TEST_STOPPED is received or timeout expires.

        Args:
            test_id: Expected test ID (for matching TEST_STOPPED).
            timeout: Max seconds to wait for test completion.
            port_poll_interval: How often to check port existence during sleep.

        Returns:
            TestOutcome with markers, serial log, and status.
        """
        outcome = TestOutcome(test_id=test_id)
        port_path = self._transport.port_path
        sleeping = False
        sleep_start_time: float | None = None
        sleep_expected_s: float | None = None

        deadline = self._clock() + timeout

        while self._clock() < deadline:
            # If sleeping, monitor port instead of reading serial
            if sleeping:
                if port_path and os.path.exists(port_path):
                    # Port reappeared — device woke up
                    wake_time = self._clock()
                    if sleep_start_time is not None:
                        actual_sleep = wake_time - sleep_start_time
                        outcome.sleep_actual_s = actual_sleep

                        if sleep_expected_s is not None:
                            if actual_sleep < sleep_expected_s * 0.8:
                                outcome.warnings.append(
                                    f"Premature wake: {actual_sleep:.1f}s "
                                    f"(expected {sleep_expected_s:.0f}s)"
                                )
                            elif actual_sleep > sleep_expected_s * 1.3:
                                outcome.warnings.append(
                                    f"Late wake: {actual_sleep:.1f}s "
                                    f"(expected {sleep_expected_s:.0f}s)"
                                )

                    logger.info(
                        "Port reappeared after %.1fs",
                        actual_sleep if sleep_start_time else 0,
                    )
                    sleeping = False

                    # Wait for serial to stabilize after wake
                    time.sleep(2.0)

                    # Reconnect transport if needed
                    if not self._transport.is_connected():
                        try:
                            self._transport.connect()
                        except Exception as e:
                            logger.warning("Reconnect failed: %s", e)
                            time.sleep(1.0)
                            continue

                    # Reset decoder for fresh connection
                    self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
                    self._line_buf.clear()
                else:
                    # Still sleeping — check for timeout
                    if sleep_expected_s is not None and sleep_start_time is not None:
                        elapsed = self._clock() - sleep_start_time
                        if elapsed > sleep_expected_s + 15:
                            outcome.warnings.append(
                                f"Device did not wake after {elapsed:.0f}s "
                                f"(expected {sleep_expected_s:.0f}s)"
                            )
                            outcome.status = "timeout"
                            return outcome
                    time.sleep(port_poll_interval)
                    continue

            # Normal mode: read serial
            line = self._read_line(timeout=0.5)
            if line is None:
                # Check if port disappeared (entered sleep without SLEEP marker)
                if port_path and not os.path.exists(port_path) and not sleeping:
                    logger.info("Port disappeared — device likely sleeping")
                    sleeping = True
                    if sleep_start_time is None:
                        sleep_start_time = self._clock()
                continue

            outcome.serial_log.append(line)

            # Check for JSON metadata
            obj = parse_json_line(line)
            if obj:
                msg_type = obj.get("type")
                if msg_type == "test_end":
                    status = obj.get("status", "ok")
                    outcome.status = status
                    logger.info("Test end JSON: %s status=%s", test_id, status)
                continue

            # Check for T= markers
            result = parse_marker(line)
            if result:
                ts, payload = result
                host_ts = self._clock()

                if payload == "PPK_START":
                    outcome.markers["PPK_START"] = host_ts
                    logger.debug("PPK_START at host_t=%.3f", host_ts)

                elif payload == "PPK_STOP":
                    outcome.markers["PPK_STOP"] = host_ts
                    logger.debug("PPK_STOP at host_t=%.3f", host_ts)

                elif payload.startswith("SLEEP:"):
                    try:
                        duration = float(payload.split(":", 1)[1])
                    except (ValueError, IndexError):
                        duration = None
                    outcome.sleep_expected_s = duration
                    sleep_expected_s = duration
                    sleep_start_time = host_ts
                    outcome.markers["SLEEP"] = host_ts
                    logger.info("SLEEP marker: %s seconds", duration)

                    # Port will disappear shortly
                    sleeping = True

                elif payload == f"TEST_STOPPED:{test_id}":
                    outcome.markers["TEST_STOPPED"] = host_ts
                    logger.info("Test stopped: %s", test_id)
                    return outcome

                elif payload.startswith("TEST_STARTED:"):
                    outcome.markers["TEST_STARTED"] = host_ts

        outcome.status = "timeout"
        outcome.warnings.append(f"Test did not complete within {timeout}s")
        return outcome

    def _read_line(self, timeout: float = 0.5) -> str | None:
        """Read a single line from the transport.

        Uses an incremental UTF-8 decoder to handle multi-byte characters
        split across read boundaries.
        """
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            try:
                data = self._transport.read(timeout=min(0.1, timeout))
            except Exception:
                return None

            if not data:
                # Check if we have a complete line buffered
                text = "".join(self._line_buf)
                if "\n" in text:
                    before, _, after = text.partition("\n")
                    self._line_buf = list(after) if after else []
                    stripped = before.strip()
                    return stripped if stripped else None
                continue

            text = self._decoder.decode(data)
            self._line_buf.extend(text)

            # Check for complete lines
            joined = "".join(self._line_buf)
            if "\n" in joined:
                before, _, after = joined.partition("\n")
                self._line_buf = list(after) if after else []
                stripped = before.strip()
                if stripped:
                    return stripped

        return None
