"""Serial port abstractions used by the PyVNA drivers."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

try:
    import serial  # type: ignore[import]
except ImportError:  # pragma: no cover - optional dependency
    serial = None  # type: ignore[assignment]


@runtime_checkable
class SerialPortInterface(Protocol):
    """Protocol describing the operations needed by the VNA drivers."""

    def read(self, size: int) -> bytes: ...

    def readline(self) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def close(self) -> None: ...

    def set_read_timeout(self, timeout: float | None) -> None: ...


@dataclass
class SerialPort:
    """Wrapper around :mod:`pyserial` providing the expected interface."""

    _serial: "serial.Serial" # type: ignore

    def read(self, size: int) -> bytes:
        return self._serial.read(size)

    def readline(self) -> bytes:
        return self._serial.readline()

    def write(self, data: bytes) -> int:
        return self._serial.write(data)

    def close(self) -> None:
        self._serial.close()

    def set_read_timeout(self, timeout: float | None) -> None:
        self._serial.timeout = timeout


def _validate_port_path(path: str) -> None:
    """Validate that the provided path is a legitimate serial port path."""
    # On Unix-like systems, serial ports typically follow patterns like:
    # /dev/ttyS* followed by digits, /dev/ttyUSB* followed by digits,
    # /dev/ttyACM* followed by digits, /dev/cu.* (for macOS), or symlinks in by-id
    # On Windows, serial ports are typically COM* followed by numbers
    unix_pattern = r'^(/dev/(ttyS\d+|ttyUSB\d+|ttyACM\d+|cu\..+)|/dev/serial/by-id/.+)$'
    windows_pattern = r'^COM\d+$'

    # Check if it matches any known serial port patterns
    if not (re.match(unix_pattern, path, re.IGNORECASE) or
            re.match(windows_pattern, path, re.IGNORECASE)):
        raise ValueError(f"Invalid serial port path: {path}. "
                        "Expected format: /dev/ttyS*, /dev/ttyUSB*, /dev/ttyACM*, "
                        "/dev/cu.*, /dev/serial/by-id/*, or COM*")


def open_port(path: str, baudrate: int = 115200) -> SerialPortInterface:
    """Open a serial port returning a :class:`SerialPortInterface` instance."""

    if serial is None:
        raise RuntimeError("pyserial is required to open serial ports")

    _validate_port_path(path)
    ser = serial.Serial(path, baudrate=baudrate, timeout=None, write_timeout=None)
    return SerialPort(ser)


__all__ = ["SerialPortInterface", "SerialPort", "open_port"]
