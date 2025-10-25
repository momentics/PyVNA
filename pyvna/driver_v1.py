"""Driver implementation for the NanoVNA V1 text protocol."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .util.serial_port import SerialPortInterface
from .models import SweepConfig, VNAData


@dataclass
class V1Driver:
    port: SerialPortInterface
    config: SweepConfig = field(default_factory=lambda: SweepConfig(0.0, 0.0, 0))

    def identify(self) -> str:
        self.port.set_read_timeout(0.5)
        try:
            self.port.write(b"version\n")
            response = self.port.readline()
            if not response:
                raise RuntimeError("v1: no response to version command")
            text = response.decode("utf-8", errors="ignore")
            if "nanovna" in text.lower():
                return text.strip()
            raise RuntimeError("v1: device did not identify as NanoVNA V1")
        finally:
            self.port.set_read_timeout(None)

    def set_sweep(self, config: SweepConfig) -> None:
        self.config = config
        command = f"sweep {int(config.start)} {int(config.stop)} {config.points}\n".encode()
        self.port.write(command)

    def scan(self) -> VNAData:
        self.port.write(b"data\n")
        time.sleep(0.1)
        return self._read_data()

    def close(self) -> None:
        self.port.close()

    def _read_data(self) -> VNAData:
        data = VNAData(
            frequencies=[],
            s11=[],
            s21=[],
        )
        for idx in range(self.config.points):
            line_bytes = self.port.readline()
            if not line_bytes:
                raise RuntimeError(
                    f"v1: expected {self.config.points} data lines, received {idx}"
                )
            line = line_bytes.decode("utf-8", errors="ignore").strip()
            parts = line.split()
            if len(parts) < 5:
                raise ValueError(
                    f"v1: line {idx + 1} contained {len(parts)} values, expected 5"
                )
            try:
                freq = float(parts[0])
                s11_re = float(parts[1])
                s11_im = float(parts[2])
                s21_re = float(parts[3])
                s21_im = float(parts[4])
            except ValueError as exc:
                raise ValueError(f"v1: failed to parse float on line {idx + 1}") from exc

            data.frequencies.append(freq)
            data.s11.append(complex(s11_re, s11_im))
            data.s21.append(complex(s21_re, s21_im))
        return data


__all__ = ["V1Driver"]
