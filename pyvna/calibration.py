"""Calibration models and helpers."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .errors import CalibrationError
from .models import SweepConfig, VNAData


class CalibrationMethod(StrEnum):
    SOL = "SOL"


class CalibrationStandard(StrEnum):
    """Calibration standards supported by the SOL plan. THRU is measured and
    stored for two-port plans; host-side profiles apply one-port (S11)
    error terms, S21 is returned as measured."""

    OPEN = "open"
    SHORT = "short"
    LOAD = "load"
    THRU = "thru"


@dataclass
class CalibrationStep:
    standard: CalibrationStandard


@dataclass
class CalibrationPlan:
    name: str
    sweep: SweepConfig
    steps: list[CalibrationStep]


CalibrationPrompt = Callable[[CalibrationStandard], None]


@dataclass
class CalibrationMeasurement:
    frequencies: list[float]
    s11: list[complex]
    s21: list[complex]


@dataclass
class CalibrationErrorTerms:
    directivity: list[complex] = field(default_factory=list)
    source_match: list[complex] = field(default_factory=list)
    reflection_tracking: list[complex] = field(default_factory=list)


@dataclass
class CalibrationProfile:
    name: str
    method: CalibrationMethod
    created_at: datetime
    sweep: SweepConfig
    frequencies: list[float]
    standards: dict[CalibrationStandard, CalibrationMeasurement]
    error_terms: CalibrationErrorTerms

    def validate(self) -> None:
        if not self.frequencies:
            raise ValueError("calibration profile does not contain frequency data")
        ft_len = len(self.frequencies)
        if (
            len(self.error_terms.directivity) != ft_len
            or len(self.error_terms.source_match) != ft_len
            or len(self.error_terms.reflection_tracking) != ft_len
        ):
            raise ValueError("calibration coefficients do not match frequency grid")
        if self.method is CalibrationMethod.SOL:
            for required in (
                CalibrationStandard.OPEN,
                CalibrationStandard.SHORT,
                CalibrationStandard.LOAD,
            ):
                if required not in self.standards:
                    raise ValueError(f"missing calibration measurement for {required.value}")

    def apply(self, data: VNAData) -> VNAData:
        """De-embeds S11 with the three-term reflection error model and returns a
        copy of the data; S21 passes through unmodified."""
        data.validate()
        if len(data.frequencies) != len(self.frequencies):
            raise ValueError("data frequency grid does not match calibration")
        for idx, freq in enumerate(data.frequencies):
            if not math.isclose(freq, self.frequencies[idx], rel_tol=0, abs_tol=1e-3):
                raise ValueError("data frequencies do not match calibration")
        calibrated = VNAData(
            frequencies=data.frequencies.copy(),
            s11=[0j] * len(data.s11),
            s21=data.s21.copy(),
        )
        for idx, measurement in enumerate(data.s11):
            e00 = self.error_terms.directivity[idx]
            e11 = self.error_terms.source_match[idx]
            tracking = self.error_terms.reflection_tracking[idx]
            numerator = measurement - e00
            denominator = e11 + tracking * (measurement - e00)
            if denominator == 0:
                raise CalibrationError(
                    f"degenerate calibration data at {data.frequencies[idx]:.3f} Hz: "
                    "zero denominator"
                )
            calibrated.s11[idx] = numerator / denominator
        return calibrated


def _clone_floats(values: list[float]) -> list[float]:
    return list(values) if values is not None else []


def _frequencies_match(a: list[float], b: list[float]) -> bool:
    if len(a) != len(b):
        return False
    return all(math.isclose(x, y, rel_tol=0, abs_tol=1e-3) for x, y in zip(a, b, strict=True))


def compute_error_terms(profile: CalibrationProfile) -> None:
    try:
        open_meas = profile.standards[CalibrationStandard.OPEN]
        short_meas = profile.standards[CalibrationStandard.SHORT]
        load_meas = profile.standards[CalibrationStandard.LOAD]
    except KeyError as exc:
        raise ValueError("SOL calibration requires open, short and load measurements") from exc

    if not load_meas.s11:
        raise ValueError("calibration measurements are empty")
    if not (
        _frequencies_match(load_meas.frequencies, open_meas.frequencies)
        and _frequencies_match(load_meas.frequencies, short_meas.frequencies)
    ):
        raise CalibrationError("calibration standards use mismatched frequency grids")

    count = len(load_meas.s11)
    directivity: list[complex] = [0j] * count
    source_match: list[complex] = [0j] * count
    tracking: list[complex] = [0j] * count

    for idx in range(count):
        e00 = load_meas.s11[idx]
        lo = open_meas.s11[idx] - e00
        ls = short_meas.s11[idx] - e00
        denom = lo - ls
        if denom == 0:
            raise CalibrationError(
                f"degenerate calibration data at {load_meas.frequencies[idx]:.3f} Hz: "
                "zero denominator"
            )
        e10e32 = (lo + ls) / denom
        e11 = -ls * (1 + e10e32)
        directivity[idx] = e00
        source_match[idx] = e11
        tracking[idx] = e10e32

    profile.frequencies = _clone_floats(load_meas.frequencies)
    profile.error_terms = CalibrationErrorTerms(
        directivity=directivity,
        source_match=source_match,
        reflection_tracking=tracking,
    )


__all__ = [
    "CalibrationMethod",
    "CalibrationStandard",
    "CalibrationStep",
    "CalibrationPlan",
    "CalibrationPrompt",
    "CalibrationMeasurement",
    "CalibrationErrorTerms",
    "CalibrationProfile",
    "compute_error_terms",
]
