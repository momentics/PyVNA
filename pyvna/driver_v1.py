"""Driver implementation for the NanoVNA V1 text protocol."""

from __future__ import annotations

from .driver_base import BaseDriver
from .errors import IdentificationError, ProtocolError
from .models import SweepConfig, VNAData

V1_IDENTIFY_TIMEOUT = 0.5
V1_SCAN_BASE = 5.0
V1_SCAN_PER_POINT = 0.004
V1_SCAN_READ_TIMEOUT = 2.0


class V1Driver(BaseDriver):
    def identify(self) -> str:
        with self._read_timeout(V1_IDENTIFY_TIMEOUT):
            self.port.write(b"version\n")
            response = self.port.readline()
        text = response.decode("utf-8", "replace").strip()
        if not text or "nanovna" not in text.lower():
            raise IdentificationError("v1: no NanoVNA version string received")
        self.model = text
        return text

    def set_sweep(self, config: SweepConfig) -> None:
        self.config = config
        command = f"sweep {int(config.start)} {int(config.stop)} {config.points}\n".encode()
        self.port.write(command)

    def scan(self) -> VNAData:
        config = self._require_config()
        deadline = V1_SCAN_BASE + V1_SCAN_PER_POINT * config.points
        end = self._clock() + deadline
        self.port.write(b"data\n")
        data = VNAData(frequencies=[], s11=[], s21=[])
        with self._read_timeout(V1_SCAN_READ_TIMEOUT):
            for idx in range(config.points):
                remaining = end - self._clock()
                if remaining <= 0:
                    raise ProtocolError(
                        f"v1: timed out reading data (received {idx} of {config.points} lines)"
                    )
                self.port.set_read_timeout(min(remaining, V1_SCAN_READ_TIMEOUT))
                line_bytes = self.port.readline()
                line = line_bytes.decode("utf-8", "replace").strip()
                if not line:
                    raise ProtocolError(f"v1: no data received for line {idx + 1}")
                parts = line.split()
                if len(parts) < 5:
                    raise ProtocolError(
                        f"v1: line {idx + 1} contained {len(parts)} values, expected 5"
                    )
                try:
                    freq, s11_re, s11_im, s21_re, s21_im = (float(p) for p in parts[:5])
                except ValueError as exc:
                    raise ProtocolError(f"v1: failed to parse floats on line {idx + 1}") from exc
                data.frequencies.append(freq)
                data.s11.append(complex(s11_re, s11_im))
                data.s21.append(complex(s21_re, s21_im))
        return data


__all__ = ["V1Driver"]
