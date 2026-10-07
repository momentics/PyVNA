"""Tests for the serial port utility layer."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import pyvna.util.serial_port as serial_port_module
from pyvna.util.serial_port import (
    DEFAULT_READ_TIMEOUT,
    SerialPort,
    _validate_port_path,
    open_port,
)
from tests.test_vna import MockSerialPort


def test_open_port_default_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class FakeSerial:
        def __init__(self, path: str, **kwargs: object) -> None:
            captured["path"] = path
            captured.update(kwargs)

    monkeypatch.setattr(serial_port_module, "serial", SimpleNamespace(Serial=FakeSerial))
    port = open_port("COM1")
    assert isinstance(port, SerialPort)
    assert captured["baudrate"] == 115200
    assert captured["timeout"] == DEFAULT_READ_TIMEOUT
    assert DEFAULT_READ_TIMEOUT == 1.0


def test_validate_port_path_extended() -> None:
    # New valid port shapes beyond the base allowlist.
    valid_ports = [
        "/dev/ttyAMA0",
        "/dev/ttyO1",
        "/dev/serial/by-path/platform-xhci-usb",
        r"\\.\COM5",
    ]

    for port in valid_ports:
        # Should not raise an exception for valid ports (though the port may not exist)
        try:
            _validate_port_path(port)
        except ValueError:
            pytest.fail(f"Valid port incorrectly rejected: {port}")

    with pytest.raises(ValueError, match="Invalid serial port path"):
        _validate_port_path("/dev/ttyAMAabc")


def test_get_read_timeout_roundtrip() -> None:
    port = MockSerialPort()
    port.set_read_timeout(0.25)
    assert port.get_read_timeout() == 0.25
