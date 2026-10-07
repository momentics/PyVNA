"""Serial port abstractions used by the PyVNA drivers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

try:
    # pyserial does not ship type stubs, so the import is marked untyped.
    import serial  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - optional dependency
    serial = None


DEFAULT_READ_TIMEOUT = 1.0


@runtime_checkable
class SerialPortInterface(Protocol):
    """Protocol describing the operations needed by the VNA drivers."""

    def read(self, size: int) -> bytes: ...

    def readline(self) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def close(self) -> None: ...

    def set_read_timeout(self, timeout: float) -> None:
        """Finite read timeouts only; blocking reads are not part of this interface."""

        ...

    def get_read_timeout(self) -> float: ...


@dataclass
class SerialPort:
    """Wrapper around :mod:`pyserial` providing the expected interface."""

    _serial: serial.Serial

    def read(self, size: int) -> bytes:
        return self._serial.read(size)

    def readline(self) -> bytes:
        return self._serial.readline()

    def write(self, data: bytes) -> int:
        return self._serial.write(data)

    def close(self) -> None:
        self._serial.close()

    def set_read_timeout(self, timeout: float) -> None:
        self._serial.timeout = timeout

    def get_read_timeout(self) -> float:
        return self._serial.timeout


def _validate_port_path(path: str) -> None:
    """Validate that the provided path matches a known serial port pattern."""
    # On Unix-like systems, serial ports typically follow patterns like:
    # /dev/ttyS* followed by digits, /dev/ttyUSB* followed by digits,
    # /dev/ttyACM* followed by digits, /dev/cu.* (for macOS), or symlinks in by-id
    # On Windows, serial ports are typically COM* followed by numbers
    unix_pattern = (
        r"^/dev/(ttyS\d+|ttyUSB\d+|ttyACM\d+|ttyAMA\d+|ttyO\d+|cu\..+|serial/by-(id|path)/.+)$"
    )
    windows_pattern = r"^(COM\d+|\\\\\.\\COM\d+)$"

    # Check if it matches any known serial port patterns
    if not (
        re.match(unix_pattern, path, re.IGNORECASE)
        or re.match(windows_pattern, path, re.IGNORECASE)
    ):
        raise ValueError(
            f"Invalid serial port path: {path}. "
            "Expected format: /dev/ttyS*, /dev/ttyUSB*, /dev/ttyACM*, "
            "/dev/ttyAMA*, /dev/ttyO*, /dev/cu.*, /dev/serial/by-id/*, "
            "/dev/serial/by-path/*, COM* or \\\\.\\COM*"
        )


def open_port(path: str, baudrate: int = 115200) -> SerialPortInterface:
    """Open a serial port returning a :class:`SerialPortInterface` instance."""

    if serial is None:
        raise RuntimeError("pyserial is required to open serial ports")

    _validate_port_path(path)
    ser = serial.Serial(path, baudrate=baudrate, timeout=DEFAULT_READ_TIMEOUT, write_timeout=None)
    return SerialPort(ser)


__all__ = ["SerialPortInterface", "SerialPort", "open_port", "DEFAULT_READ_TIMEOUT"]
