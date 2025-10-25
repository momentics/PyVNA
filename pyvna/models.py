"""Core data structures shared across the PyVNA modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List

@dataclass
class SweepConfig:
    start: float
    stop: float
    points: int


@dataclass
class VNAData:
    frequencies: List[float] = field(default_factory=list)
    s11: List[complex] = field(default_factory=list)
    s21: List[complex] = field(default_factory=list)

    def to_touchstone(self) -> str:
        lines = ["! PyVNA Data Export", f"! Date: {datetime.now(timezone.utc).isoformat()}", "# Hz S RI R 50"]
        for idx in range(len(self.frequencies)):
            freq = self.frequencies[idx]
            s11 = self.s11[idx]
            s21 = self.s21[idx]
            lines.append(
                f"{freq:.6f} {s11.real:.6f} {s11.imag:.6f} {s21.real:.6f} {s21.imag:.6f}"
            )
        return "\n".join(lines) + "\n"

    def calculate_vswr(self) -> List[float]:
        vswr: List[float] = []
        for reflection in self.s11:
            gamma = abs(reflection)
            if gamma >= 1.0:
                vswr.append(9999.0)
            else:
                vswr.append((1 + gamma) / (1 - gamma))
        return vswr


__all__ = ["SweepConfig", "VNAData"]
