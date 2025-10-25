"""Driver implementation for the NanoVNA V2/LiteVNA binary protocol."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .util.serial_port import SerialPortInterface
from .models import SweepConfig, VNAData


OP_NOP = 0x00
OP_READ = 0x10
OP_WRITE2 = 0x21
OP_WRITE4 = 0x22
OP_READFIFO = 0x18

ADDR_SWEEP_START = 0x00
ADDR_SWEEP_STEP = 0x10
ADDR_SWEEP_POINTS = 0x20
ADDR_VALS_FIFO = 0x30
ADDR_DEVICE_VARIANT = 0xF0


@dataclass
class V2Driver:
    port: SerialPortInterface
    config: SweepConfig = field(default_factory=lambda: SweepConfig(0.0, 0.0, 0))

    def __post_init__(self) -> None:
        self._reset_protocol()

    def _reset_protocol(self) -> None:
        self.port.write(bytes(8))

    def identify(self) -> str:
        self.port.set_read_timeout(0.5)
        try:
            self.port.write(bytes([OP_READ, ADDR_DEVICE_VARIANT]))
            buf = self._read_exact(1)
            variant = buf[0]
            if variant in (2, 4):
                return f"NanoVNA_V2 (Variant {variant})"
            raise RuntimeError("v2: device did not report a supported variant")
        finally:
            self.port.set_read_timeout(None)

    def set_sweep(self, config: SweepConfig) -> None:
        self.config = config
        step = 0.0
        if config.points > 1:
            step = (config.stop - config.start) / float(config.points - 1)
        self._write_reg_float64(ADDR_SWEEP_START, config.start)
        self._write_reg_float64(ADDR_SWEEP_STEP, step)
        self._write_reg16(ADDR_SWEEP_POINTS, config.points)

    def scan(self) -> VNAData:
        if self.config.points <= 0:
            raise RuntimeError("v2: sweep not configured or zero points requested")
        self.port.write(bytes([OP_READFIFO, ADDR_VALS_FIFO, 0x00]))
        expected = self.config.points * 32
        raw = self._read_exact(expected)
        return self._parse_binary_data(raw)

    def close(self) -> None:
        self.port.close()

    def _parse_binary_data(self, buf: bytes) -> VNAData:
        if len(buf) % 32 != 0:
            raise ValueError(f"v2: response length {len(buf)} is not a multiple of 32")
        points = len(buf) // 32
        if points == 0:
            raise ValueError("v2: device returned an empty response")
        if points != self.config.points:
            raise ValueError(
                f"v2: device returned {points} points, expected {self.config.points}"
            )
        data = VNAData(
            frequencies=[0.0] * points,
            s11=[0j] * points,
            s21=[0j] * points,
        )
        step = 0.0
        if points > 1:
            step = (self.config.stop - self.config.start) / float(points - 1)
        for idx in range(points):
            offset = idx * 32
            chunk = buf[offset : offset + 32]
            s11_re = struct.unpack_from("<f", chunk, 0)[0]
            s11_im = struct.unpack_from("<f", chunk, 4)[0]
            s21_re = struct.unpack_from("<f", chunk, 16)[0]
            s21_im = struct.unpack_from("<f", chunk, 20)[0]
            data.frequencies[idx] = self.config.start + step * idx
            data.s11[idx] = complex(float(s11_re), float(s11_im))
            data.s21[idx] = complex(float(s21_re), float(s21_im))
        return data

    def _write_reg_float64(self, addr: int, value: float) -> None:
        payload = bytearray(10)
        payload[0] = OP_WRITE4 + 2
        payload[1] = addr & 0xFF
        struct.pack_into("<d", payload, 2, value)
        self.port.write(payload)

    def _write_reg16(self, addr: int, value: int) -> None:
        payload = bytearray(4)
        payload[0] = OP_WRITE2
        payload[1] = addr & 0xFF
        struct.pack_into("<H", payload, 2, value & 0xFFFF)
        self.port.write(payload)

    def _read_exact(self, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            remaining = size - len(chunks)
            chunk = self.port.read(remaining)
            if not chunk:
                raise RuntimeError(
                    f"v2: expected {size} bytes, received {len(chunks)}"
                )
            chunks.extend(chunk)
        return bytes(chunks)


__all__ = ["V2Driver"]
