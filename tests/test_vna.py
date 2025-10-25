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

