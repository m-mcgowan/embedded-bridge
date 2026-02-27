"""Serial transport wrapping pyserial with reconnect and device discovery.

Connects by device name (via ``usb-device port``) or direct port path.
Handles USB-CDC port disappearance during deep sleep with configurable
reconnect policy.

Requires ``pyserial>=3.5``::

    pip install embedded-bridge[serial]

Usage::

    transport = SerialTransport("1.10")       # by device name
    transport = SerialTransport("/dev/cu.usbmodem1234")  # by port path
    transport.connect()
    data = transport.read(timeout=1.0)
    transport.write(b"T\\n")
    transport.disconnect()
"""

import logging
import subprocess
import time
from pathlib import Path

try:
    import serial
    import serial.tools.list_ports
except ImportError as e:
    raise ImportError(
        "pyserial is required for SerialTransport. "
        "Install with: pip install embedded-bridge[serial]"
    ) from e

logger = logging.getLogger(__name__)


def resolve_port(device_name: str) -> str:
    """Resolve a device name to a serial port path via ``usb-device port``.

    Args:
        device_name: Friendly device name (e.g. "1.10") or a port path
            (e.g. "/dev/cu.usbmodem1234").

    Returns:
        Absolute port path string.

    Raises:
        FileNotFoundError: If usb-device is not installed or device not found.
    """
    # If it looks like a port path already, return as-is
    if device_name.startswith("/dev/") or device_name.startswith("COM"):
        return device_name

    try:
        result = subprocess.run(
            ["usb-device", "port", device_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise FileNotFoundError(
                f"usb-device port '{device_name}' failed: {result.stderr.strip()}"
            )
        port = result.stdout.strip()
        if not port:
            raise FileNotFoundError(
                f"usb-device returned empty port for '{device_name}'"
            )
        return port
    except FileNotFoundError:
        raise
    except subprocess.TimeoutExpired:
        raise FileNotFoundError(
            f"usb-device timed out resolving '{device_name}'"
        )
    except OSError as e:
        raise FileNotFoundError(
            f"usb-device not found on PATH: {e}"
        ) from e


def port_exists(port_path: str) -> bool:
    """Check whether a serial port exists on the filesystem."""
    return Path(port_path).exists()


class SerialTransport:
    """Serial transport wrapping pyserial.

    Args:
        port: Device name (resolved via ``usb-device port``) or direct port path.
        baudrate: Serial baud rate. Default 115200.
        reconnect: Whether to attempt reconnect on disconnect. Default False.
        reconnect_interval: Seconds between reconnect attempts. Default 1.0.
        reconnect_timeout: Max seconds to wait for reconnect. Default 30.0.
    """

    def __init__(
        self,
        port: str,
        *,
        baudrate: int = 115200,
        reconnect: bool = False,
        reconnect_interval: float = 1.0,
        reconnect_timeout: float = 30.0,
    ) -> None:
        self._port_spec = port
        self._baudrate = baudrate
        self._reconnect = reconnect
        self._reconnect_interval = reconnect_interval
        self._reconnect_timeout = reconnect_timeout
        self._serial: serial.Serial | None = None
        self._port_path: str | None = None

    def connect(self) -> None:
        """Open the serial connection.

        Resolves device name to port path if needed, then opens pyserial.

        Raises:
            FileNotFoundError: If the port cannot be resolved or doesn't exist.
            serial.SerialException: If the port cannot be opened.
        """
        if self._serial is not None and self._serial.is_open:
            return

        self._port_path = resolve_port(self._port_spec)

        logger.info("Opening %s at %d baud", self._port_path, self._baudrate)
        self._serial = serial.Serial(
            port=self._port_path,
            baudrate=self._baudrate,
            timeout=0,  # Non-blocking by default; read() manages timeout
            exclusive=True,
        )

    def disconnect(self) -> None:
        """Close the serial connection."""
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
        logger.info("Disconnected from %s", self._port_path)

    def read(self, size: int = -1, timeout: float | None = None) -> bytes:
        """Read bytes from the serial port.

        Args:
            size: Max bytes to read. -1 means read all available.
            timeout: Max seconds to wait for data. None blocks indefinitely.
                0 returns immediately with whatever is available.

        Returns:
            Bytes read (may be empty if timeout expires with no data).

        Raises:
            ConnectionError: If not connected.
        """
        ser = self._ensure_connected()

        if timeout is not None:
            deadline = time.monotonic() + timeout
        else:
            deadline = None

        while True:
            try:
                if size == -1:
                    waiting = ser.in_waiting
                    if waiting > 0:
                        return ser.read(waiting)
                else:
                    waiting = ser.in_waiting
                    if waiting > 0:
                        return ser.read(min(size, waiting))

                # No data available
                if deadline is not None and time.monotonic() >= deadline:
                    return b""

                # Brief sleep to avoid busy-wait
                time.sleep(0.01)

            except (serial.SerialException, OSError) as e:
                if self._reconnect:
                    logger.warning("Read error, attempting reconnect: %s", e)
                    self._do_reconnect()
                    ser = self._ensure_connected()
                else:
                    raise ConnectionError(f"Serial read failed: {e}") from e

    def write(self, data: bytes) -> None:
        """Write bytes to the serial port.

        Args:
            data: Bytes to send.

        Raises:
            ConnectionError: If not connected or write fails.
        """
        ser = self._ensure_connected()
        try:
            ser.write(data)
            ser.flush()
        except (serial.SerialException, OSError) as e:
            if self._reconnect:
                logger.warning("Write error, attempting reconnect: %s", e)
                self._do_reconnect()
                # Retry once after reconnect
                ser = self._ensure_connected()
                ser.write(data)
                ser.flush()
            else:
                raise ConnectionError(f"Serial write failed: {e}") from e

    def is_connected(self) -> bool:
        """Check if the serial port is open and the device is still present."""
        if self._serial is None or not self._serial.is_open:
            return False
        # Check the port still exists on the filesystem (USB-CDC disappears on sleep)
        if self._port_path and not port_exists(self._port_path):
            return False
        return True

    @property
    def port_path(self) -> str | None:
        """The resolved serial port path, or None if not yet connected."""
        return self._port_path

    def _ensure_connected(self) -> serial.Serial:
        """Return the open serial port or raise ConnectionError."""
        if self._serial is None or not self._serial.is_open:
            raise ConnectionError(
                "Not connected. Call connect() first."
            )
        return self._serial

    def _do_reconnect(self) -> None:
        """Attempt to reconnect within the configured timeout."""
        self.disconnect()

        deadline = time.monotonic() + self._reconnect_timeout
        attempt = 0

        while time.monotonic() < deadline:
            attempt += 1
            try:
                # Re-resolve port (may change after USB re-enumeration)
                self._port_path = resolve_port(self._port_spec)
                if not port_exists(self._port_path):
                    raise FileNotFoundError(f"Port {self._port_path} not found")

                logger.info(
                    "Reconnect attempt %d: %s", attempt, self._port_path
                )
                self._serial = serial.Serial(
                    port=self._port_path,
                    baudrate=self._baudrate,
                    timeout=0,
                    exclusive=True,
                )
                logger.info("Reconnected to %s", self._port_path)
                return
            except (serial.SerialException, FileNotFoundError, OSError) as e:
                logger.debug("Reconnect attempt %d failed: %s", attempt, e)
                time.sleep(self._reconnect_interval)

        raise ConnectionError(
            f"Failed to reconnect to '{self._port_spec}' "
            f"after {self._reconnect_timeout}s ({attempt} attempts)"
        )

    def __enter__(self) -> "SerialTransport":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.disconnect()

    def __repr__(self) -> str:
        status = "connected" if self.is_connected() else "disconnected"
        return (
            f"SerialTransport({self._port_spec!r}, "
            f"baudrate={self._baudrate}, {status})"
        )
