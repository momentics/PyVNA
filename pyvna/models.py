"""Core data structures shared across the PyVNA modules."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class SweepConfig:
    """Immutable sweep definition.

    Frequencies are in hertz and must lie inside the supported family range
    (50 kHz .. 2.7 GHz); `start == stop` denotes a single-point measurement.
    The point count is bounded by the family maximum (401); a device build
    with a lower limit rejects an oversized sweep with a textual error.
    """

    start: float
    stop: float
    points: int

    MIN_START_HZ = 50_000.0
    MAX_STOP_HZ = 2_700_000_000.0
    MAX_POINTS = 401

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", float(self.start))
        object.__setattr__(self, "stop", float(self.stop))
        object.__setattr__(self, "points", int(self.points))
        if self.start < SweepConfig.MIN_START_HZ:
            raise ValueError(f"sweep start must be >= {SweepConfig.MIN_START_HZ:.0f} Hz")
        if not (self.start <= self.stop <= SweepConfig.MAX_STOP_HZ):
            raise ValueError("sweep requires MIN_START_HZ <= start <= stop <= MAX_STOP_HZ")
        if self.points < 1:
            raise ValueError("sweep requires at least one point")
        if self.points > SweepConfig.MAX_POINTS:
            raise ValueError(f"sweep requires at most {SweepConfig.MAX_POINTS} points")


@dataclass(slots=True)
class VNAData:
    """One sweep result: frequency grid plus S11 and S21 complex samples."""

    frequencies: list[float] = field(default_factory=list)
    s11: list[complex] = field(default_factory=list)
    s21: list[complex] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.frequencies)

    @property
    def is_empty(self) -> bool:
        return not self.frequencies

    def validate(self) -> None:
        """Raise ValueError when the three arrays are inconsistent."""
        count = len(self.frequencies)
        if len(self.s11) != count or len(self.s21) != count:
            raise ValueError(
                f"frequencies/s11/s21 must have equal length "
                f"({count}/{len(self.s11)}/{len(self.s21)})"
            )
        for freq in self.frequencies:
            if not math.isfinite(freq):
                raise ValueError("frequencies contain a non-finite value")
        for name, samples in (("s11", self.s11), ("s21", self.s21)):
            for sample in samples:
                if not (math.isfinite(sample.real) and math.isfinite(sample.imag)):
                    raise ValueError(f"{name} contains a non-finite value")

    def to_touchstone(self) -> str:
        self.validate()
        lines = [
            "! PyVNA Data Export",
            f"! Date: {datetime.now(UTC).isoformat()}",
            "# Hz S RI R 50",
        ]
        for idx in range(len(self.frequencies)):
            freq = self.frequencies[idx]
            s11 = self.s11[idx]
            s21 = self.s21[idx]
            lines.append(f"{freq:.6f} {s11.real:.6f} {s11.imag:.6f} {s21.real:.6f} {s21.imag:.6f}")
        return "\n".join(lines) + "\n"

    def calculate_vswr(self) -> list[float]:
        self.validate()
        vswr: list[float] = []
        for reflection in self.s11:
            gamma = abs(reflection)
            if gamma >= 1.0:
                vswr.append(9999.0)
            else:
                vswr.append((1 + gamma) / (1 - gamma))
        return vswr


__all__ = ["SweepConfig", "VNAData"]
