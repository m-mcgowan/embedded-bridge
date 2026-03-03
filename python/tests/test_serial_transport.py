"""Tests for SerialTransport.

Uses unittest.mock to patch pyserial — no real hardware needed.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from embedded_bridge.transport.base import Transport
from embedded_bridge.transport.serial import (
    SerialTransport,
    resolve_port,
    port_exists,
)


# ── Protocol compliance ──────────────────────────────────────────────


class TestProtocol:
    def test_satisfies_transport_protocol(self):
        """SerialTransport satisfies the Transport protocol."""
        transport = SerialTransport("/dev/fake")
        assert isinstance(transport, Transport)


# ── resolve_port ─────────────────────────────────────────────────────


class TestResolvePort:
    def test_port_path_passthrough(self):
        """Port paths starting with /dev/ are returned as-is."""
        assert resolve_port("/dev/cu.usbmodem1234") == "/dev/cu.usbmodem1234"

    def test_com_port_passthrough(self):
        """Windows COM ports are returned as-is."""
        assert resolve_port("COM3") == "COM3"

    @patch("embedded_bridge.transport.serial.subprocess.run")
    def test_device_name_resolution(self, mock_run):
        """Device names are resolved via usb-device port."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="/dev/cu.usbmodem5678\n",
        )
        result = resolve_port("1.10")
        assert result == "/dev/cu.usbmodem5678"
        mock_run.assert_called_once_with(
            ["usb-device", "port", "1.10"],
            capture_output=True,
            text=True,
            timeout=10,
        )

    @patch("embedded_bridge.transport.serial.subprocess.run")
    def test_device_not_found(self, mock_run):
        """FileNotFoundError when usb-device fails."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="Device not found",
        )
        with pytest.raises(FileNotFoundError, match="Device not found"):
            resolve_port("nonexistent")

    @patch("embedded_bridge.transport.serial.subprocess.run")
    def test_empty_output(self, mock_run):
        """FileNotFoundError when usb-device returns empty string."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="  \n",
        )
        with pytest.raises(FileNotFoundError, match="empty port"):
            resolve_port("1.10")


# ── Connect / disconnect ─────────────────────────────────────────────


class TestConnectDisconnect:
    @patch("embedded_bridge.transport.serial.serial.Serial")
    @patch("embedded_bridge.transport.serial.resolve_port", return_value="/dev/fake")
    def test_connect_opens_serial(self, mock_resolve, mock_serial_cls):
        transport = SerialTransport("1.10", baudrate=921600)
        transport.connect()

        mock_resolve.assert_called_once_with("1.10")
        mock_serial_cls.assert_called_once_with(
            port="/dev/fake",
            baudrate=921600,
            timeout=0,
            exclusive=True,
        )
        assert transport.port_path == "/dev/fake"

    @patch("embedded_bridge.transport.serial.serial.Serial")
    @patch("embedded_bridge.transport.serial.resolve_port", return_value="/dev/fake")
    def test_connect_idempotent(self, mock_resolve, mock_serial_cls):
        """Calling connect() when already connected is a no-op."""
        mock_ser = MagicMock()
        mock_ser.is_open = True
        mock_serial_cls.return_value = mock_ser

        transport = SerialTransport("/dev/fake")
        transport.connect()
        transport.connect()  # Second call should be no-op

        assert mock_serial_cls.call_count == 1

    @patch("embedded_bridge.transport.serial.serial.Serial")
    @patch("embedded_bridge.transport.serial.resolve_port", return_value="/dev/fake")
    def test_disconnect_closes(self, mock_resolve, mock_serial_cls):
        mock_ser = MagicMock()
        mock_serial_cls.return_value = mock_ser

        transport = SerialTransport("/dev/fake")
        transport.connect()
        transport.disconnect()

        mock_ser.close.assert_called_once()

    @patch("embedded_bridge.transport.serial.serial.Serial")
    @patch("embedded_bridge.transport.serial.resolve_port", return_value="/dev/fake")
    def test_disconnect_when_not_connected(self, mock_resolve, mock_serial_cls):
        """Disconnect when not connected doesn't raise."""
        transport = SerialTransport("/dev/fake")
        transport.disconnect()  # Should not raise


# ── Read ─────────────────────────────────────────────────────────────


class TestRead:
    @patch("embedded_bridge.transport.serial.serial.Serial")
    @patch("embedded_bridge.transport.serial.resolve_port", return_value="/dev/fake")
    def test_read_available_data(self, mock_resolve, mock_serial_cls):
        mock_ser = MagicMock()
        mock_ser.is_open = True
        mock_ser.in_waiting = 10
        mock_ser.read.return_value = b"T=0.001 GPS"
        mock_serial_cls.return_value = mock_ser

        transport = SerialTransport("/dev/fake")
        transport.connect()
        data = transport.read(timeout=1.0)

        assert data == b"T=0.001 GPS"

    @patch("embedded_bridge.transport.serial.time")
    @patch("embedded_bridge.transport.serial.serial.Serial")
    @patch("embedded_bridge.transport.serial.resolve_port", return_value="/dev/fake")
    def test_read_timeout_returns_empty(self, mock_resolve, mock_serial_cls, mock_time):
        """Read with timeout returns empty bytes when no data."""
        mock_ser = MagicMock()
        mock_ser.is_open = True
        mock_ser.in_waiting = 0
        mock_serial_cls.return_value = mock_ser

        # Simulate time passing beyond deadline
        mock_time.monotonic.side_effect = [0.0, 1.1]
        mock_time.sleep = MagicMock()

        transport = SerialTransport("/dev/fake")
        transport.connect()
        data = transport.read(timeout=1.0)

        assert data == b""

    def test_read_not_connected_raises(self):
        transport = SerialTransport("/dev/fake")
        with pytest.raises(ConnectionError, match="Not connected"):
            transport.read(timeout=0)


# ── Write ────────────────────────────────────────────────────────────


class TestWrite:
    @patch("embedded_bridge.transport.serial.serial.Serial")
    @patch("embedded_bridge.transport.serial.resolve_port", return_value="/dev/fake")
    def test_write_sends_data(self, mock_resolve, mock_serial_cls):
        mock_ser = MagicMock()
        mock_ser.is_open = True
        mock_serial_cls.return_value = mock_ser

        transport = SerialTransport("/dev/fake")
        transport.connect()
        transport.write(b"T\n")

        mock_ser.write.assert_called_once_with(b"T\n")
        mock_ser.flush.assert_called_once()

    def test_write_not_connected_raises(self):
        transport = SerialTransport("/dev/fake")
        with pytest.raises(ConnectionError, match="Not connected"):
            transport.write(b"T\n")


# ── is_connected ─────────────────────────────────────────────────────


class TestIsConnected:
    def test_not_connected_initially(self):
        transport = SerialTransport("/dev/fake")
        assert not transport.is_connected()

    @patch("embedded_bridge.transport.serial.port_exists", return_value=True)
    @patch("embedded_bridge.transport.serial.serial.Serial")
    @patch("embedded_bridge.transport.serial.resolve_port", return_value="/dev/fake")
    def test_connected_after_open(self, mock_resolve, mock_serial_cls, mock_exists):
        mock_ser = MagicMock()
        mock_ser.is_open = True
        mock_serial_cls.return_value = mock_ser

        transport = SerialTransport("/dev/fake")
        transport.connect()
        assert transport.is_connected()

    @patch("embedded_bridge.transport.serial.port_exists", return_value=False)
    @patch("embedded_bridge.transport.serial.serial.Serial")
    @patch("embedded_bridge.transport.serial.resolve_port", return_value="/dev/fake")
    def test_not_connected_when_port_vanishes(self, mock_resolve, mock_serial_cls, mock_exists):
        """USB-CDC port disappears during deep sleep."""
        mock_ser = MagicMock()
        mock_ser.is_open = True
        mock_serial_cls.return_value = mock_ser

        transport = SerialTransport("/dev/fake")
        transport.connect()
        assert not transport.is_connected()


# ── Context manager ──────────────────────────────────────────────────


class TestContextManager:
    @patch("embedded_bridge.transport.serial.serial.Serial")
    @patch("embedded_bridge.transport.serial.resolve_port", return_value="/dev/fake")
    def test_context_manager(self, mock_resolve, mock_serial_cls):
        mock_ser = MagicMock()
        mock_ser.is_open = True
        mock_serial_cls.return_value = mock_ser

        with SerialTransport("/dev/fake") as transport:
            assert transport.port_path == "/dev/fake"

        mock_ser.close.assert_called_once()


# ── Reconnect ────────────────────────────────────────────────────────


class TestReconnect:
    @patch("embedded_bridge.transport.serial.time")
    @patch("embedded_bridge.transport.serial.port_exists", return_value=True)
    @patch("embedded_bridge.transport.serial.serial.Serial")
    @patch("embedded_bridge.transport.serial.resolve_port", return_value="/dev/fake")
    def test_reconnect_on_read_error(
        self, mock_resolve, mock_serial_cls, mock_exists, mock_time
    ):
        """With reconnect=True, a read error triggers reconnect."""
        import serial as pyserial

        # First serial instance: fails on read
        mock_ser1 = MagicMock()
        mock_ser1.is_open = True
        type(mock_ser1).in_waiting = PropertyMock(
            side_effect=pyserial.SerialException("Port gone")
        )

        # Second serial instance: works
        mock_ser2 = MagicMock()
        mock_ser2.is_open = True
        type(mock_ser2).in_waiting = PropertyMock(return_value=5)
        mock_ser2.read.return_value = b"hello"

        mock_serial_cls.side_effect = [mock_ser1, mock_ser2]
        mock_time.monotonic.side_effect = [0.0, 0.0, 0.5]  # within timeout
        mock_time.sleep = MagicMock()

        transport = SerialTransport("/dev/fake", reconnect=True, reconnect_timeout=5.0)
        transport.connect()
        data = transport.read(timeout=1.0)

        assert data == b"hello"
        assert mock_serial_cls.call_count == 2

    @patch("embedded_bridge.transport.serial.serial.Serial")
    @patch("embedded_bridge.transport.serial.resolve_port", return_value="/dev/fake")
    def test_no_reconnect_raises_on_read_error(self, mock_resolve, mock_serial_cls):
        """With reconnect=False (default), read errors raise ConnectionError."""
        import serial as pyserial

        mock_ser = MagicMock()
        mock_ser.is_open = True
        type(mock_ser).in_waiting = PropertyMock(
            side_effect=pyserial.SerialException("Port gone")
        )
        mock_serial_cls.return_value = mock_ser

        transport = SerialTransport("/dev/fake")
        transport.connect()

        with pytest.raises(ConnectionError, match="Serial read failed"):
            transport.read(timeout=0)


# ── repr ─────────────────────────────────────────────────────────────


class TestRepr:
    def test_repr_disconnected(self):
        transport = SerialTransport("1.10", baudrate=921600)
        r = repr(transport)
        assert "1.10" in r
        assert "921600" in r
        assert "disconnected" in r
