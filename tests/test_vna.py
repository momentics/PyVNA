from __future__ import annotations

import math
import struct
import threading
import time
from collections import deque

import pytest

from pyvna.calibration import (
    CalibrationMethod,
    CalibrationPlan,
    CalibrationStandard,
    CalibrationStep,
)
from pyvna.driver import driver_factory
from pyvna.driver_v1 import V1Driver
from pyvna.driver_v2 import V2Driver
from pyvna.driver_x import XDriver
from pyvna.errors import DeviceError, IdentificationError, ProtocolError
from pyvna.models import SweepConfig, VNAData
from pyvna.util.serial_port import DEFAULT_READ_TIMEOUT
from pyvna.vna import VNA
from tests.devices import (
    FakeV1Device,
    FakeV2Device,
    FakeXDevice,
    apply_three_term_error_model,
)


class MockSerialPort:
    """In-memory serial port used by the driver and factory tests.

    Reads emulate pyserial's bounded reads: when the buffer is empty a read
    blocks for the current timeout (via _on_idle) and then returns b"".
    Tests that exercise deadline logic without real waiting hang a fake
    clock on both the port and the driver under test.
    """

    def __init__(self, device=None) -> None:
        self._read_buffer = deque()  # type: deque[int]
        self._write_buffer = bytearray()
        self._lock = threading.Lock()
        self.timeout_values: list[float] = []
        self.timeout = DEFAULT_READ_TIMEOUT
        self.device = device
        self._on_idle = time.sleep
        if device is not None:
            device.attach(self)

    def read(self, size: int) -> bytes:
        with self._lock:
            data = bytearray()
            while size and self._read_buffer:
                data.append(self._read_buffer.popleft())
                size -= 1
            if not data:
                # Emulate the expiry of the finite read timeout: pyserial
                # blocks for the timeout, then returns an empty chunk.
                self._on_idle(self.timeout)
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
        if self.device is not None:
            self.device.on_write(bytes(data), self)
        return len(data)

    def close(self) -> None:  # pragma: no cover - nothing to close in the mock
        pass

    def set_read_timeout(self, timeout: float) -> None:
        if timeout is None:
            raise TypeError("blocking reads are forbidden")
        self.timeout = timeout
        self.timeout_values.append(timeout)

    def get_read_timeout(self) -> float:
        return self.timeout

    def set_read_data(self, payload: bytes) -> None:
        with self._lock:
            self._read_buffer.extend(payload)

    def clear(self) -> None:
        with self._lock:
            self._read_buffer.clear()
            self._write_buffer.clear()


class FakeClock:
    """Controllable monotonic clock for deadline logic in tests."""

    def __init__(self) -> None:
        self.now = 0.0
        self.elapsed = 0.0

    def advance(self, seconds: float) -> None:
        self.now += seconds
        self.elapsed += seconds

    def __call__(self) -> float:
        return self.now


def float32_bytes(value: float) -> bytes:
    return struct.pack("<f", value)


def test_driver_factory_selects_v1() -> None:
    mock = MockSerialPort(device=FakeV1Device())
    driver = driver_factory(mock)
    assert isinstance(driver, V1Driver)


def test_driver_factory_selects_v2() -> None:
    mock = MockSerialPort(device=FakeV2Device(variant=2))
    driver = driver_factory(mock)
    assert isinstance(driver, V2Driver)


def test_v1driver_scan() -> None:
    mock = MockSerialPort()
    driver = V1Driver(mock)
    driver.set_sweep(SweepConfig(start=1_000_000, stop=1_000_000, points=1))
    mock.set_read_data(b"1000000 0.5 -0.5 0.1 -0.1\r\n")
    data = driver.scan()
    assert len(data.s11) == 1
    assert data.s11[0] == complex(0.5, -0.5)


def test_v1driver_scan_invalid_data() -> None:
    mock = MockSerialPort()
    driver = V1Driver(mock)
    driver.set_sweep(SweepConfig(start=1_000_000, stop=1_000_000, points=1))
    mock.set_read_data(b"1000000 0.5 nope 0.1 -0.1\n")
    with pytest.raises(ProtocolError):
        driver.scan()

    mock = MockSerialPort()
    driver = V1Driver(mock)
    driver.set_sweep(SweepConfig(start=1_000_000, stop=1_000_000, points=1))
    mock.set_read_data(b"1000000 0.5\n")
    with pytest.raises(ProtocolError):
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
    fake = FakeClock()
    mock._on_idle = fake.advance
    driver = V2Driver(mock)
    driver._clock = fake
    driver.set_sweep(SweepConfig(start=1e6, stop=2e6, points=2))
    payload = bytearray()
    payload.extend(float32_bytes(0.5))
    payload.extend(float32_bytes(-0.5))
    payload.extend(b"\x00" * 8)
    payload.extend(float32_bytes(0.1))
    payload.extend(float32_bytes(-0.1))
    payload.extend(b"\x00" * 8)
    mock.set_read_data(bytes(payload))
    with pytest.raises(ProtocolError):
        driver.scan()


def test_v2driver_parse_binary_data_validation() -> None:
    mock = MockSerialPort()
    driver = V2Driver(mock)
    driver.config = SweepConfig(start=1e6, stop=2e6, points=2)
    with pytest.raises(ProtocolError):
        driver._parse_binary_data(b"\x00" * 12)
    with pytest.raises(ProtocolError):
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
        self.model = None

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

    # Test valid Unix-like ports
    valid_ports = [
        "/dev/ttyUSB0",
        "/dev/ttyACM0",
        "/dev/ttyS0",
        "/dev/cu.usbserial",
        "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_AH026KKK-if00-port0",
        "COM1",
        "COM10",
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
        "/dev/invalid",
    ]

    for port in invalid_ports:
        with pytest.raises(ValueError, match="Invalid serial port path"):
            _validate_port_path(port)


def test_driver_factory_selects_x() -> None:
    """Test that driver factory correctly selects X driver for NanoVNA-X devices."""
    mock = MockSerialPort(device=FakeXDevice())
    # X driver should be tried first and succeed
    driver = driver_factory(mock)
    assert isinstance(driver, XDriver)


class _FastXDevice(FakeXDevice):
    """FakeXDevice variant where scan output arrives before the early prompt.

    The measurement thread can finish its output before the shell loop prints
    the early prompt; the driver must accept that ordering as well.
    """

    def on_write(self, data: bytes, port) -> None:
        line = data.split(b"\r\n", 1)[0].decode("utf-8", "replace").strip()
        if line.startswith("scan "):
            # No early prompt: echo + payload + final prompt only.
            port.set_read_data(data + self._emit_scan(line) + FakeXDevice.PROMPT)
        else:
            super().on_write(data, port)


def test_xdriver_scan() -> None:
    """Test X driver scan functionality."""
    device = FakeXDevice()
    # Canned sweep: two points with explicit S11/S21 samples.
    device.scan_points = (1_000_000, 2_000_000)
    device.scan_s11 = (complex(0.5, -0.5), complex(-0.2, 0.3))
    device.scan_s21 = (complex(0.1, -0.1), complex(0.0, 0.2))
    port = MockSerialPort(device=device)
    driver = XDriver(port)
    assert driver.identify() == "NanoVNA-X Shell"
    driver.set_sweep(SweepConfig(start=1_000_000, stop=2_000_000, points=2))
    data = driver.scan()
    assert data.frequencies == [1_000_000.0, 2_000_000.0]
    # The binary payload carries float32 samples; compare with a tolerance.
    assert data.s11 == pytest.approx([complex(0.5, -0.5), complex(-0.2, 0.3)], rel=1e-6)
    assert data.s21 == pytest.approx([complex(0.1, -0.1), complex(0.0, 0.2)], rel=1e-6)


def test_xdriver_scan_fast_ordering() -> None:
    """Test X driver scan when the payload arrives before the early prompt."""
    device = _FastXDevice()
    # Same canned sweep as in test_xdriver_scan.
    device.scan_points = (1_000_000, 2_000_000)
    device.scan_s11 = (complex(0.5, -0.5), complex(-0.2, 0.3))
    device.scan_s21 = (complex(0.1, -0.1), complex(0.0, 0.2))
    port = MockSerialPort(device=device)
    driver = XDriver(port)
    assert driver.identify() == "NanoVNA-X Shell"
    driver.set_sweep(SweepConfig(start=1_000_000, stop=2_000_000, points=2))
    data = driver.scan()
    assert data.frequencies == [1_000_000.0, 2_000_000.0]
    # The binary payload carries float32 samples; compare with a tolerance.
    assert data.s11 == pytest.approx([complex(0.5, -0.5), complex(-0.2, 0.3)], rel=1e-6)
    assert data.s21 == pytest.approx([complex(0.1, -0.1), complex(0.0, 0.2)], rel=1e-6)


class _BadMaskXDevice(FakeXDevice):
    """FakeXDevice variant reporting a wrong mask in the binary scan header."""

    def _emit_scan(self, line: str) -> bytes:
        payload = bytearray(super()._emit_scan(line))
        # Rewrite the mask field of the 4-byte binary header; keep the count.
        payload[0:2] = struct.pack("<H", 0x83)
        return bytes(payload)


class _BadCountXDevice(FakeXDevice):
    """FakeXDevice variant reporting one extra point in the binary header."""

    def _emit_scan(self, line: str) -> bytes:
        parts = line.split()
        count = int(parts[3]) + 1  # requested point count plus one
        out = bytearray(struct.pack("<HH", 0x87, count))
        for i in range(count):
            freq = self.scan_points[i % len(self.scan_points)]
            s11 = self.scan_s11[i % len(self.scan_s11)]
            s21 = self.scan_s21[i % len(self.scan_s21)]
            out += struct.pack("<I", int(freq))
            out += struct.pack("<ff", s11.real, s11.imag)
            out += struct.pack("<ff", s21.real, s21.imag)
        return bytes(out)


def test_xdriver_scan_binary_golden() -> None:
    device = FakeXDevice()
    # Canned sweep: two points, explicit S11/S21 samples (the fake device honours these).
    device.scan_points = (1_000_000, 3_000_000)
    device.scan_s11 = (complex(0.5, -0.5), complex(-0.2, 0.3))
    device.scan_s21 = (complex(0.1, -0.1), complex(0.0, 0.2))
    port = MockSerialPort(device=device)
    driver = XDriver(port)
    driver.identify()
    driver.set_sweep(SweepConfig(start=1_000_000, stop=3_000_000, points=2))
    data = driver.scan()
    assert data.frequencies == [1_000_000.0, 3_000_000.0]
    # The binary payload carries float32 samples; compare with a tolerance.
    assert data.s11 == pytest.approx([complex(0.5, -0.5), complex(-0.2, 0.3)], rel=1e-6)
    assert data.s21 == pytest.approx([complex(0.1, -0.1), complex(0.0, 0.2)], rel=1e-6)
    # gate: frequencies are stored as float, not the raw uint32
    assert all(isinstance(f, float) for f in data.frequencies)


def test_xdriver_scan_binary_header_mismatch() -> None:
    device = _BadMaskXDevice()
    port = MockSerialPort(device=device)
    driver = XDriver(port)
    driver.identify()
    driver.set_sweep(SweepConfig(start=1_000_000, stop=2_000_000, points=1))
    with pytest.raises(ProtocolError, match="mask"):
        driver.scan()


def test_xdriver_scan_binary_point_count_mismatch() -> None:
    device = _BadCountXDevice()
    port = MockSerialPort(device=device)
    driver = XDriver(port)
    driver.identify()
    driver.set_sweep(SweepConfig(start=1_000_000, stop=2_000_000, points=1))
    with pytest.raises(ProtocolError, match="points"):
        driver.scan()


def test_xdriver_scan_silent_device_bounded() -> None:
    port = MockSerialPort()  # no device: the wire is completely silent
    fake = FakeClock()
    port._on_idle = fake.advance
    driver = XDriver(port)
    driver._clock = fake
    # Bypass set_sweep (it needs a live shell) to probe the scan deadline directly.
    driver.config = SweepConfig(start=1e6, stop=2e6, points=3)
    with pytest.raises(ProtocolError):
        driver.scan()
    # The budget is X_SCAN_BASE + 3 * X_SCAN_PER_POINT of fake time; well under the bound.
    assert fake.elapsed < 20


def test_xdriver_scan_binary_fast_ordering() -> None:
    """Binary scan when the payload arrives before the early prompt."""
    device = _FastXDevice()
    # Same canned sweep as in test_xdriver_scan.
    device.scan_points = (1_000_000, 2_000_000)
    device.scan_s11 = (complex(0.5, -0.5), complex(-0.2, 0.3))
    device.scan_s21 = (complex(0.1, -0.1), complex(0.0, 0.2))
    port = MockSerialPort(device=device)
    driver = XDriver(port)
    assert driver.identify() == "NanoVNA-X Shell"
    driver.set_sweep(SweepConfig(start=1_000_000, stop=2_000_000, points=2))
    data = driver.scan()
    assert data.frequencies == [1_000_000.0, 2_000_000.0]
    # The binary payload carries float32 samples; compare with a tolerance.
    assert data.s11 == pytest.approx([complex(0.5, -0.5), complex(-0.2, 0.3)], rel=1e-6)
    assert data.s21 == pytest.approx([complex(0.1, -0.1), complex(0.0, 0.2)], rel=1e-6)


def test_set_read_timeout_rejects_none() -> None:
    port = MockSerialPort()
    blocking_timeout = None
    with pytest.raises(TypeError):
        port.set_read_timeout(blocking_timeout)


@pytest.mark.parametrize(
    ("device", "driver_cls"),
    [
        pytest.param(FakeV1Device(), V1Driver, id="v1"),
        pytest.param(FakeV2Device(variant=2), V2Driver, id="v2"),
        pytest.param(FakeXDevice(), XDriver, id="x"),
    ],
)
def test_identify_uses_only_finite_timeouts(device: object, driver_cls: type) -> None:
    port = MockSerialPort(device=device)
    driver = driver_cls(port)
    driver.identify()
    assert port.timeout_values
    for value in port.timeout_values:
        assert math.isfinite(value) and value > 0


def test_x_identify_rejects_v1_device() -> None:
    with pytest.raises(IdentificationError):
        XDriver(MockSerialPort(device=FakeV1Device())).identify()

    # The same wire is a perfectly good V1 device on its own.
    port = MockSerialPort(device=FakeV1Device())
    driver = V1Driver(port)
    model = driver.identify()
    assert "nanovna" in model.lower()


def test_x_identify_version_fallback() -> None:
    # No banner on the wire: identification must fall back to the version
    # query, whose reply is a bare semver plus prompt and echoed command.
    port = MockSerialPort(device=FakeXDevice(banner=False))
    driver = XDriver(port)
    model = driver.identify()
    assert model == "NanoVNA-X 0.9.102"


def test_factory_error_aggregation() -> None:
    # A completely silent wire fails every probe; the error names them all.
    port = MockSerialPort()
    with pytest.raises(RuntimeError) as excinfo:
        driver_factory(port)
    message = str(excinfo.value)
    assert "XDriver" in message
    assert "V1Driver" in message
    assert "V2Driver" in message


def test_vna_model_property() -> None:
    port = MockSerialPort(device=FakeXDevice())
    driver = XDriver(port)
    driver.identify()
    vna = VNA(driver)
    assert isinstance(vna.model, str)

    # A driver that has not been identified reports it explicitly.
    unidentified = VNA(StubDriver([]))
    with pytest.raises(DeviceError):
        _ = unidentified.model


@pytest.mark.parametrize("driver_cls", [V1Driver, V2Driver, XDriver])
def test_unconfigured_scan_raises_device_error(driver_cls: type) -> None:
    driver = driver_cls(MockSerialPort())
    with pytest.raises(DeviceError):
        driver.scan()


def test_version_matches_pyproject() -> None:
    import tomllib
    from pathlib import Path

    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text("utf-8"))
    assert pyproject["project"]["version"] == __import__("pyvna").__version__
