from __future__ import annotations

import struct
import threading
from collections import deque

import pytest

from pyvna.driver import driver_factory
from pyvna.driver_v1 import V1Driver
from pyvna.driver_v2 import V2Driver, OP_READ, ADDR_DEVICE_VARIANT
from pyvna.models import SweepConfig, VNAData
from pyvna.vna import VNA
from pyvna.calibration import (
    CalibrationPlan,
    CalibrationMethod,
    CalibrationStep,
    CalibrationStandard,
)


class MockSerialPort:
    def __init__(self) -> None:
        self._read_buffer = deque()  # type: deque[int]
        self._write_buffer = bytearray()
        self._lock = threading.Lock()
        self.variant = 0
        self.timeout = None

    def read(self, size: int) -> bytes:
        with self._lock:
            data = bytearray()
            while size and self._read_buffer:
                data.append(self._read_buffer.popleft())
                size -= 1
            return bytes(data)

    def readline(self) -> bytes:
        with self._lock:
            data = bytearray()
            while self._read_buffer:
                byte = self._read_buffer.popleft()
                data.append(byte)
                if byte == 0x0A:  # \n
                    break
            return bytes(data)

    def write(self, data: bytes) -> int:
        with self._lock:
            self._write_buffer.extend(data)
            if len(data) >= 2 and data[0] == OP_READ and data[1] == ADDR_DEVICE_VARIANT and self.variant:
                self._read_buffer.append(self.variant)
            # Handle V1 vs X protocol - V1 uses "version\n", X uses "version\r\n"
            elif b"version\r\n" in data:  # X protocol command
                # For X protocol, if we have set V1-style response data, it should not be used for X
                # Instead, we should timeout or not respond properly (simulate non-X device)
                # If there's pre-existing V1-style data, this suggests it's not an X device
                current_buffer_content = bytes(list(self._read_buffer))
                if b"nanovna h" in current_buffer_content.lower():
                    # This is a V1-style device, so X protocol should not work properly
                    # Don't add a response, leave buffer as is, which will cause timeout in X driver
                    pass
                else:
                    # For a proper X device, respond with shell style
                    self._read_buffer.extend(b"NanoVNA-X version 1.0\r\nch> ")
            elif b"version\n" in data and b"\r\n" not in data:  # V1 protocol command
                # For V1 protocol, if we have pre-stored response data, send it
                # This path would be hit if V1 driver runs directly
                # In tests this is handled by set_read_data
                pass
            elif b"sweep " in data and b"\r\n" in data and b"ch> " not in data:
                # Add prompt after sweep command
                self._read_buffer.extend(b"ch> ")
            elif b"scan " in data and b"0x83" in data:  # binary scan
                # Simulate binary scan response with prompt
                import struct
                # Create binary mask (0x83) and point count (1 point)
                self._read_buffer.extend(struct.pack('<HH', 0x83, 1))
                # Add frequency (1000000 Hz)
                self._read_buffer.extend(struct.pack('<I', 1000000))
                # Add S11 (0.5-0.5j) as floats
                self._read_buffer.extend(struct.pack('<ff', 0.5, -0.5))
                # Add S21 (0.1-0.1j) as floats
                self._read_buffer.extend(struct.pack('<ff', 0.1, -0.1))
                self._read_buffer.extend(b"ch> ")
            elif b"scan " in data and b"\r\n" in data:  # text scan
                # Add text scan response with prompt
                self._read_buffer.extend(b"1000000 0.5 -0.5 0.1 -0.1\r\nch> ")
            return len(data)

    def close(self) -> None:  # pragma: no cover - nothing to close in the mock
        pass

    def set_read_timeout(self, timeout: float | None) -> None:
        self.timeout = timeout

    def set_read_data(self, payload: bytes) -> None:
        with self._lock:
            self._read_buffer.extend(payload)

    def clear(self) -> None:
        with self._lock:
            self._read_buffer.clear()
            self._write_buffer.clear()


def float32_bytes(value: float) -> bytes:
    return struct.pack("<f", value)


def test_driver_factory_selects_v1() -> None:
    mock = MockSerialPort()
    mock.set_read_data(b"NanoVNA H\n")
    driver = driver_factory(mock)
    assert isinstance(driver, V1Driver)


def test_driver_factory_selects_v2() -> None:
    mock = MockSerialPort()
    mock.variant = 0x02
    mock.set_read_data(b"unrecognized\n")
    driver = driver_factory(mock)
    assert isinstance(driver, V2Driver)


def test_v1driver_scan() -> None:
    mock = MockSerialPort()
    driver = V1Driver(mock)
    driver.set_sweep(SweepConfig(start=0, stop=0, points=1))
    mock.set_read_data(b"1000000 0.5 -0.5 0.1 -0.1\n")
    data = driver.scan()
    assert len(data.s11) == 1
    assert data.s11[0] == complex(0.5, -0.5)


def test_v1driver_scan_invalid_data() -> None:
    mock = MockSerialPort()
    driver = V1Driver(mock)
    driver.set_sweep(SweepConfig(start=0, stop=0, points=1))
    mock.set_read_data(b"1000000 0.5 nope 0.1 -0.1\n")
    with pytest.raises(ValueError):
        driver.scan()

    mock = MockSerialPort()
    driver = V1Driver(mock)
    driver.set_sweep(SweepConfig(start=0, stop=0, points=1))
    mock.set_read_data(b"1000000 0.5\n")
    with pytest.raises(ValueError):
        driver.scan()


def test_v2driver_scan() -> None:
    mock = MockSerialPort()
    driver = V2Driver(mock)
    driver.set_sweep(SweepConfig(start=1e6, stop=1e6, points=1))
    payload = bytearray()
    payload.extend(float32_bytes(0.5))
    payload.extend(float32_bytes(-0.5))
    payload.extend(b"\x00" * 8)
    payload.extend(float32_bytes(0.1))
    payload.extend(float32_bytes(-0.1))
    payload.extend(b"\x00" * 8)
    mock.set_read_data(bytes(payload))
    data = driver.scan()
    assert len(data.s11) == 1
    assert pytest.approx(data.s11[0].real, rel=1e-6) == 0.5
    assert data.frequencies[0] == pytest.approx(1e6)


def test_v2driver_scan_unexpected_eof() -> None:
    mock = MockSerialPort()
    driver = V2Driver(mock)
    driver.set_sweep(SweepConfig(start=1e6, stop=2e6, points=2))
    payload = bytearray()
    payload.extend(float32_bytes(0.5))
    payload.extend(float32_bytes(-0.5))
    payload.extend(b"\x00" * 8)
    payload.extend(float32_bytes(0.1))
    payload.extend(float32_bytes(-0.1))
    payload.extend(b"\x00" * 8)
    mock.set_read_data(bytes(payload))
    with pytest.raises(RuntimeError):
        driver.scan()


def test_v2driver_parse_binary_data_validation() -> None:
    mock = MockSerialPort()
    driver = V2Driver(mock)
    driver.config = SweepConfig(start=1e6, stop=2e6, points=2)
    with pytest.raises(ValueError):
        driver._parse_binary_data(b"\x00" * 12)
    with pytest.raises(ValueError):
        driver._parse_binary_data(b"\x00" * 32)


def test_vnadata_to_touchstone_precision() -> None:
    data = VNAData(
        frequencies=[1.23456789e6],
        s11=[complex(0.5, -0.5)],
        s21=[complex(0.1, -0.1)],
    )
    touchstone = data.to_touchstone()
    assert "1234567.890000" in touchstone


class StubDriver:
    def __init__(self, sequence: list[VNAData]) -> None:
        self._sequence = sequence
        self._lock = threading.Lock()
        self._cursor = 0

    def identify(self) -> str:  # pragma: no cover - not used in tests
        return "stub"

    def set_sweep(self, config: SweepConfig) -> None:
        pass

    def scan(self) -> VNAData:
        with self._lock:
            if self._cursor >= len(self._sequence):
                raise RuntimeError("no more data")
            result = self._sequence[self._cursor]
            self._cursor += 1
            return result

    def close(self) -> None:
        pass


def apply_three_term_error_model(e00: complex, e11: complex, tracking: complex, gamma: complex) -> complex:
    numerator = e11 * gamma
    denominator = 1 - tracking * gamma
    return e00 + numerator / denominator


def test_vna_calibration_workflow() -> None:
    freq = [1e9]
    e00 = complex(0.05, -0.01)
    e11 = complex(0.92, 0.02)
    tracking = complex(0.12, -0.03)
    open_meas = apply_three_term_error_model(e00, e11, tracking, complex(1, 0))
    short_meas = apply_three_term_error_model(e00, e11, tracking, complex(-1, 0))
    load_meas = apply_three_term_error_model(e00, e11, tracking, complex(0, 0))
    unknown_gamma = complex(0.3, -0.1)
    unknown_meas = apply_three_term_error_model(e00, e11, tracking, unknown_gamma)

    driver = StubDriver(
        [
            VNAData(frequencies=freq, s11=[open_meas], s21=[0j]),
            VNAData(frequencies=freq, s11=[short_meas], s21=[0j]),
            VNAData(frequencies=freq, s11=[load_meas], s21=[0j]),
            VNAData(frequencies=freq, s11=[unknown_meas], s21=[0j]),
        ]
    )

    vna = VNA(driver)
    plan = CalibrationPlan(
        name="test",
        sweep=SweepConfig(start=1e9, stop=1e9 + 1, points=1),
        steps=[
            CalibrationStep(standard=CalibrationStandard.OPEN),
            CalibrationStep(standard=CalibrationStandard.SHORT),
            CalibrationStep(standard=CalibrationStandard.LOAD),
        ],
    )

    profile = vna.acquire_calibration(plan)
    assert profile.method == CalibrationMethod.SOL

    data = vna.get_data()
    assert len(data.s11) == 1
    assert pytest.approx(abs(data.s11[0] - unknown_gamma), rel=1e-6) == 0

    driver._sequence.append(VNAData(frequencies=freq, s11=[unknown_meas], s21=[0j]))
    vna.clear_calibration()
    raw = vna.get_data()
    assert pytest.approx(abs(raw.s11[0] - unknown_meas), rel=1e-9) == 0

    driver._sequence.append(VNAData(frequencies=freq, s11=[unknown_meas], s21=[0j]))
    vna.load_calibration(profile)
    calibrated = vna.get_data()
    assert pytest.approx(abs(calibrated.s11[0] - unknown_gamma), rel=1e-6) == 0


def test_vna_apply_calibration_without_profile() -> None:
    vna = VNA(StubDriver([]))
    with pytest.raises(ValueError):
        vna.apply_calibration(VNAData())


def test_open_port_validation() -> None:
    from pyvna.util.serial_port import open_port

    # Test valid Unix-like ports
    valid_ports = [
        "/dev/ttyUSB0",
        "/dev/ttyACM0",
        "/dev/ttyS0",
        "/dev/cu.usbserial",
        "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_AH026KKK-if00-port0",
        "COM1",
        "COM10"
    ]

    # Test validation by importing the implementation
    from pyvna.util.serial_port import _validate_port_path

    for port in valid_ports:
        # Should not raise an exception for valid ports (though the port may not exist)
        try:
            _validate_port_path(port)
        except ValueError:
            pytest.fail(f"Valid port incorrectly rejected: {port}")

    # Test invalid ports
    invalid_ports = [
        "/etc/passwd",
        "../etc/passwd",
        "/tmp/evil_file.txt",
        "script.py",
        "../../../etc/passwd",
        "/dev/random",
        "/dev/zero",
        "COM",
        "",
        "COM-1",
        "/dev/ttyUSBabc",  # No number after USB
        "/dev/invalid"
    ]

    for port in invalid_ports:
        with pytest.raises(ValueError, match="Invalid serial port path"):
            _validate_port_path(port)


def test_driver_factory_selects_x() -> None:
    """Test that driver factory correctly selects X driver for NanoVNA-X devices."""
    mock = MockSerialPort()
    # Set up the mock with an initial prompt to flush, then version response
    mock.set_read_data(b"\r\nch> \r\nNanoVNA Shell\r\nch> ")
    # X driver should be tried first and succeed
    driver = driver_factory(mock)
    from pyvna.driver_x import XDriver
    assert isinstance(driver, XDriver)


def test_xdriver_scan() -> None:
    """Test X driver scan functionality."""
    mock = MockSerialPort()
    from pyvna.driver_x import XDriver
    driver = XDriver(mock)
    driver.set_sweep(SweepConfig(start=1e6, stop=1e6, points=1))
    data = driver.scan()
    assert len(data.s11) == 1
    assert data.s11[0] == complex(0.5, -0.5)
    assert data.s21[0] == complex(0.1, -0.1)
    assert data.frequencies[0] == 1e6


def test_xdriver_scan_binary() -> None:
    """Test X driver binary scan functionality."""
    mock = MockSerialPort()
    from pyvna.driver_x import XDriver
    driver = XDriver(mock)
    driver.config = SweepConfig(start=1e6, stop=1e6, points=1)

    # Test that it can read binary data
    data = driver._read_scan_binary()
    assert len(data.s11) == 1
    assert pytest.approx(data.s11[0].real, rel=1e-6) == 0.5
    assert pytest.approx(data.s11[0].imag, rel=1e-6) == -0.5
    assert pytest.approx(data.s21[0].real, rel=1e-6) == 0.1
    assert pytest.approx(data.s21[0].imag, rel=1e-6) == -0.1
    assert data.frequencies[0] == pytest.approx(1e6)

