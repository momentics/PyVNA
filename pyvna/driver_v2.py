"""Driver implementation for the NanoVNA V2/LiteVNA binary protocol."""

from __future__ import annotations

import struct

from .driver_base import BaseDriver
from .errors import IdentificationError, ProtocolError
from .models import SweepConfig, VNAData

OP_NOP = 0x00
OP_READ = 0x10
OP_WRITE2 = 0x21
OP_WRITE4 = 0x22
# 64-bit register write (float64 frequency registers); derived from OP_WRITE4,
# kept as a named constant.
OP_WRITE8 = OP_WRITE4 + 2
OP_READFIFO = 0x18

ADDR_SWEEP_START = 0x00
ADDR_SWEEP_STEP = 0x10
ADDR_SWEEP_POINTS = 0x20
ADDR_VALS_FIFO = 0x30
ADDR_DEVICE_VARIANT = 0xF0

V2_IDENTIFY_TIMEOUT = 0.5
V2_SCAN_BASE = 2.0
V2_SCAN_PER_POINT = 0.01


class V2Driver(BaseDriver):
    def identify(self) -> str:
        with self._read_timeout(V2_IDENTIFY_TIMEOUT):
            self.port.write(bytes(8))  # protocol reset
            self.port.write(bytes([OP_READ, ADDR_DEVICE_VARIANT]))
            variant_byte = self._read_exact(1, V2_IDENTIFY_TIMEOUT)
        variant = variant_byte[0]
        if variant not in (2, 4):
            raise IdentificationError(f"v2: unsupported device variant {variant:#04x}")
        model = f"NanoVNA_V2 (Variant {variant})"
        self.model = model
        return model

    def set_sweep(self, config: SweepConfig) -> None:
        self.config = config
        step = 0.0
        if config.points > 1:
            step = (config.stop - config.start) / float(config.points - 1)
        self._write_reg_float64(ADDR_SWEEP_START, config.start)
        self._write_reg_float64(ADDR_SWEEP_STEP, step)
        self._write_reg16(ADDR_SWEEP_POINTS, config.points)

    def scan(self) -> VNAData:
        config = self._require_config()
        deadline = V2_SCAN_BASE + V2_SCAN_PER_POINT * config.points
        self.port.write(bytes([OP_READFIFO, ADDR_VALS_FIFO, 0x00]))
        expected = config.points * 32
        raw = self._read_exact(expected, deadline)
        return self._parse_binary_data(raw)

    def _parse_binary_data(self, buf: bytes) -> VNAData:
        config = self._require_config()
        if len(buf) % 32 != 0:
            raise ProtocolError(f"v2: response length {len(buf)} is not a multiple of 32")
        points = len(buf) // 32
        if points == 0:
            raise ProtocolError("v2: device returned an empty response")
        if points != config.points:
            raise ProtocolError(f"v2: device returned {points} points, expected {config.points}")
        data = VNAData(
            frequencies=[0.0] * points,
            s11=[0j] * points,
            s21=[0j] * points,
        )
        step = 0.0
        if points > 1:
            step = (config.stop - config.start) / float(points - 1)
        for idx in range(points):
            offset = idx * 32
            chunk = buf[offset : offset + 32]
            s11_re = struct.unpack_from("<f", chunk, 0)[0]
            s11_im = struct.unpack_from("<f", chunk, 4)[0]
            s21_re = struct.unpack_from("<f", chunk, 16)[0]
            s21_im = struct.unpack_from("<f", chunk, 20)[0]
            data.frequencies[idx] = config.start + step * idx
            data.s11[idx] = complex(float(s11_re), float(s11_im))
            data.s21[idx] = complex(float(s21_re), float(s21_im))
        return data

    def _write_reg_float64(self, addr: int, value: float) -> None:
        payload = bytearray(10)
        payload[0] = OP_WRITE8
        payload[1] = addr & 0xFF
        struct.pack_into("<d", payload, 2, value)
        self.port.write(payload)

    def _write_reg16(self, addr: int, value: int) -> None:
        payload = bytearray(4)
        payload[0] = OP_WRITE2
        payload[1] = addr & 0xFF
        struct.pack_into("<H", payload, 2, value & 0xFFFF)
        self.port.write(payload)


__all__ = ["V2Driver"]
