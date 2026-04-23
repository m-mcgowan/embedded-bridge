"""Test session — orchestrates test discovery and execution over serial.

Manages the protocol lifecycle: discovery (SOH → catalog), execution
(STX → markers → outcome), and sleep monitoring. Consumers like
power-profiler or doctest runners provide the transport and act on the
results.

This module knows nothing about PPK2 or power measurement — it only
handles the serial protocol and timing markers. Sleep/wake detection
can be customized via an injectable ``sleep_detector`` callback.

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

    def start_test(
        self,
        test_id: str,
        timeout: float = 10.0,
        params: dict | None = None,
    ) -> None:
        """Send STX + test ID [+ params]. Blocks until TEST_STARTED marker.

        Args:
            test_id: Test identifier from the catalog.
            timeout: Max seconds to wait for TEST_STARTED.
            params: Optional JSON-serializable params sent to firmware.
                The firmware receives these as a parsed JSON object and
                can use them to customize test behavior (e.g.
                ``{"button_wakeup": false}``).

        Raises:
            TimeoutError: If TEST_STARTED not received within timeout.
        """
        line = test_id
        if params:
            line += " " + json.dumps(params, separators=(",", ":"))
        cmd = STX + line.encode("ascii") + b"\n"
        self._transport.write(cmd)
        logger.debug("Sent STX + %s", line)

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
        sleep_detector: Callable[[], str] | None = None,
    ) -> TestOutcome:
        """Monitor a running test until completion.

        Reads serial output, tracks T= markers, handles sleep/wake
        transitions. Returns when TEST_STOPPED is received or timeout.

        Sleep detection uses the ``sleep_detector`` callback if provided.
        The detector should return one of:

        - ``"active"`` — device is still running (hasn't entered sleep)
        - ``"sleeping"`` — device is in deep sleep
        - ``"waking"`` — device is waking up (current rising)
        - ``"unknown"`` — not enough data yet

        If no detector is provided, falls back to port existence check
        (``os.path.exists``) for sleep/wake detection.

        Args:
            test_id: Expected test ID (for matching TEST_STOPPED).
            timeout: Max seconds to wait for test completion.
            port_poll_interval: How often to poll during sleep.
            sleep_detector: Optional callback for sleep/wake detection.

        Returns:
            TestOutcome with markers, serial log, and status.
        """
        outcome = TestOutcome(test_id=test_id)
        sleeping = False
        sleep_confirmed = False
        sleep_start_time: float | None = None
        sleep_expected_s: float | None = None

        deadline = self._clock() + timeout

        while self._clock() < deadline:
            if sleeping:
                elapsed = self._clock() - sleep_start_time

                # Check for overall sleep timeout
                if sleep_expected_s is not None:
                    if elapsed > sleep_expected_s + 15:
                        outcome.warnings.append(
                            f"Device did not wake after {elapsed:.0f}s "
                            f"(expected {sleep_expected_s:.0f}s)"
                        )
                        outcome.status = "timeout"
                        return outcome

                time.sleep(port_poll_interval)

                if not sleep_confirmed:
                    # Phase 1: Wait for device to actually enter sleep
                    state = self._check_sleep_state(sleep_detector)
                    if state == "sleeping":
                        sleep_confirmed = True
                        logger.info(
                            "Sleep confirmed after %.1fs", elapsed,
                        )
                    elif state == "active" and elapsed > 10.0:
                        outcome.warnings.append(
                            "Device did not enter deep sleep within 10s "
                            "of SLEEP marker"
                        )
                        outcome.status = "error"
                        return outcome
                    continue

                # Phase 2: Wait for device to wake (port reappears)
                state = self._check_sleep_state(sleep_detector)
                if state == "sleeping":
                    continue

                # Phase 3: Device is waking — reconnect and handshake
                logger.info(
                    "Wake detected after %.1fs", elapsed,
                )
                wake_time = self._clock()
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

                # Reconnect serial
                if not self._reconnect_after_wake():
                    outcome.warnings.append("Failed to reconnect after wake")
                    outcome.status = "error"
                    return outcome

                sleeping = False
                sleep_confirmed = False
                continue

            # Normal mode: read serial
            line = self._read_line(timeout=0.5)
            if line is None:
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

                    # Send ACK to signal the device can enter sleep.
                    # Firmware waits for ACK before calling esp_deep_sleep_start()
                    # so measurement setup completes before USB-CDC drops.
                    try:
                        from .protocol import ACK
                        self._transport.write(ACK)
                    except Exception as e:
                        logger.warning("Failed to send sleep ACK: %s", e)

                    # Disconnect transport — device will enter deep sleep.
                    try:
                        self._transport.disconnect()
                    except Exception:
                        pass
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

    def _check_sleep_state(
        self, detector: Callable[[], str] | None,
    ) -> str:
        """Check whether device is sleeping, using CDC port check.

        USB-CDC port disappearance is the primary sleep indicator —
        the ESP32-S3 USB-CDC ``/dev/cu.usbmodem*`` node vanishes when
        the chip enters deep sleep and reappears after wake.

        Returns:
            ``"sleeping"`` — port has disappeared (device in deep sleep).
            ``"active"`` — port still present (or reappeared after wake).
            ``"unknown"`` — no port path available.

        An optional ``detector`` callback can override this for
        special cases (e.g. PPK2-based detection).
        """
        if detector is not None:
            return detector()

        # Primary: USB-CDC port existence
        port_path = self._transport.port_path
        if not port_path:
            return "unknown"

        if os.path.exists(port_path):
            return "active"
        else:
            return "sleeping"

    def _reconnect_after_wake(self, max_attempts: int = 10) -> bool:
        """Reconnect serial after device wakes from deep sleep.

        Tries to reconnect, then sends SOH for the wake handshake.
        The firmware waits for SOH before emitting PPK_STOP and
        TEST_STOPPED markers.

        Returns:
            True if reconnected successfully.
        """
        # Ensure disconnected first
        try:
            self._transport.disconnect()
        except Exception:
            pass

        # Reset decoder for fresh connection
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._line_buf.clear()

        for attempt in range(1, max_attempts + 1):
            try:
                self._transport.connect()
                logger.info("Reconnected (attempt %d)", attempt)

                # Send SOH for wake handshake
                self._transport.write(SOH)
                logger.debug("Sent SOH (wake handshake)")
                return True
            except Exception as e:
                logger.debug(
                    "Reconnect attempt %d failed: %s", attempt, e,
                )
                time.sleep(1.0)

        return False

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
                data = b""

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
