"""Tests for calibration: error terms, profile application, acquisition."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Event

import pytest

from pyvna.calibration import (
    CalibrationErrorTerms,
    CalibrationMeasurement,
    CalibrationMethod,
    CalibrationPlan,
    CalibrationProfile,
    CalibrationStandard,
    CalibrationStep,
    compute_error_terms,
)
from pyvna.errors import CalibrationCancelledError, CalibrationError
from pyvna.models import SweepConfig, VNAData
from pyvna.vna import VNA
from tests.devices import apply_three_term_error_model
from tests.test_vna import StubDriver

E00 = complex(0.05, -0.01)
E11 = complex(0.92, 0.02)
TRACKING = complex(0.12, -0.03)


def _measurement(frequencies: list[float], s11: list[complex]) -> CalibrationMeasurement:
    return CalibrationMeasurement(
        frequencies=list(frequencies),
        s11=list(s11),
        s21=[0j] * len(frequencies),
    )


def _profile(standards: dict) -> CalibrationProfile:
    return CalibrationProfile(
        name="sol",
        method=CalibrationMethod.SOL,
        created_at=datetime.now(UTC),
        sweep=SweepConfig(start=1e9, stop=1e9 + 1, points=1),
        frequencies=[],
        standards=standards,
        error_terms=CalibrationErrorTerms(),
    )


def test_error_terms_recover_model() -> None:
    # Build the standard responses from known error terms, then recover them.
    freqs = [1e9]
    open_meas = _measurement(
        freqs, [apply_three_term_error_model(E00, E11, TRACKING, complex(1, 0))]
    )
    short_meas = _measurement(
        freqs, [apply_three_term_error_model(E00, E11, TRACKING, complex(-1, 0))]
    )
    load_meas = _measurement(
        freqs, [apply_three_term_error_model(E00, E11, TRACKING, complex(0, 0))]
    )
    profile = _profile(
        {
            CalibrationStandard.OPEN: open_meas,
            CalibrationStandard.SHORT: short_meas,
            CalibrationStandard.LOAD: load_meas,
        }
    )
    compute_error_terms(profile)
    assert profile.error_terms.directivity[0] == pytest.approx(E00, rel=1e-6)
    assert profile.error_terms.source_match[0] == pytest.approx(E11, rel=1e-6)
    assert profile.error_terms.reflection_tracking[0] == pytest.approx(TRACKING, rel=1e-6)


def test_compute_error_terms_zero_denominator() -> None:
    # Identical open and short responses leave the solver a zero denominator.
    same = _measurement([1e9], [complex(0.4, -0.2)])
    load = _measurement([1e9], [complex(0.1, 0.1)])
    profile = _profile(
        {
            CalibrationStandard.OPEN: same,
            CalibrationStandard.SHORT: same,
            CalibrationStandard.LOAD: load,
        }
    )
    with pytest.raises(CalibrationError):
        compute_error_terms(profile)


def test_compute_error_terms_grid_mismatch() -> None:
    open_meas = _measurement([1e9], [complex(0.4, -0.2)])
    short_meas = _measurement([2e9], [complex(0.3, 0.1)])  # shifted grid position
    load = _measurement([1e9], [complex(0.1, 0.1)])
    profile = _profile(
        {
            CalibrationStandard.OPEN: open_meas,
            CalibrationStandard.SHORT: short_meas,
            CalibrationStandard.LOAD: load,
        }
    )
    with pytest.raises(CalibrationError):
        compute_error_terms(profile)


def test_apply_zero_denominator() -> None:
    # e11 + tracking * (m - e00) == 0 for m = 1 with the terms below.
    profile = CalibrationProfile(
        name="sol",
        method=CalibrationMethod.SOL,
        created_at=datetime.now(UTC),
        sweep=SweepConfig(start=1e6, stop=1e6 + 1, points=1),
        frequencies=[1e6],
        standards={},
        error_terms=CalibrationErrorTerms(
            directivity=[0j],
            source_match=[complex(1.0)],
            reflection_tracking=[complex(-1.0)],
        ),
    )
    data = VNAData(frequencies=[1e6], s11=[complex(1.0)], s21=[0j])
    with pytest.raises(CalibrationError):
        profile.apply(data)


class CountingStubDriver:
    """Driver double that counts scans; used to prove cancellation is pre-scan."""

    def __init__(self) -> None:
        self.model = "stub"
        self.scan_count = 0

    def identify(self) -> str:
        return "stub"

    def set_sweep(self, config: SweepConfig) -> None:
        pass

    def scan(self) -> VNAData:
        self.scan_count += 1
        raise AssertionError("scan must not run once calibration is cancelled")

    def close(self) -> None:
        pass


def _single_step_plan() -> CalibrationPlan:
    return CalibrationPlan(
        name="sol",
        sweep=SweepConfig(start=1e9, stop=1e9 + 1, points=1),
        steps=[CalibrationStep(standard=CalibrationStandard.OPEN)],
    )


def test_acquire_calibration_cancelled() -> None:
    driver = CountingStubDriver()
    vna = VNA(driver)
    cancel = Event()
    cancel.set()
    with pytest.raises(CalibrationCancelledError):
        vna.acquire_calibration(_single_step_plan(), cancel_event=cancel)
    # Cancellation is checked before the very first step is scanned.
    assert driver.scan_count == 0


def test_acquire_calibration_prompt_aborts() -> None:
    raw = complex(0.3, -0.1)
    freqs = [1e9]
    driver = StubDriver(
        [
            VNAData(frequencies=freqs, s11=[raw], s21=[0j]),  # never consumed: prompt aborts first
            VNAData(frequencies=freqs, s11=[raw], s21=[0j]),  # consumed by get_data after the abort
        ]
    )
    vna = VNA(driver)

    def prompt(standard: CalibrationStandard) -> None:
        raise RuntimeError("prompt aborted the run")

    with pytest.raises(RuntimeError, match="prompt aborted"):
        vna.acquire_calibration(_single_step_plan(), prompt=prompt)

    # The profile was not installed: the next read returns raw data.
    data = vna.get_data()
    assert data.s11 == [raw]
