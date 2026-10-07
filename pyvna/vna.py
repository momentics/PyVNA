"""High level VNA façade over a concrete driver."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Event, RLock
from typing import TYPE_CHECKING

from .calibration import (
    CalibrationErrorTerms,
    CalibrationMeasurement,
    CalibrationMethod,
    CalibrationPlan,
    CalibrationProfile,
    CalibrationPrompt,
    compute_error_terms,
)
from .errors import DeviceError
from .models import SweepConfig, VNAData

if TYPE_CHECKING:  # pragma: no cover - only used for type checking
    from .driver import Driver


class VNA:
    """Represents a single VNA device bound to a concrete driver."""

    def __init__(self, driver: Driver) -> None:
        self._driver = driver
        self._lock = RLock()
        self._calibration: CalibrationProfile | None = None
        self._closed = False

    @property
    def model(self) -> str:
        """Identification string reported by the driver during probing."""
        model = self._driver.model
        if model is None:
            raise DeviceError("the driver has not been identified")
        return model

    def set_sweep(self, config: SweepConfig) -> None:
        with self._lock:
            self._driver.set_sweep(config)

    def get_data(self) -> VNAData:
        with self._lock:
            data = self._driver.scan()
            calibration = self._calibration
        data.validate()
        if calibration is None:
            return data
        return calibration.apply(data)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._driver.close()

    def load_calibration(self, profile: CalibrationProfile) -> None:
        if profile is None:
            raise ValueError("calibration profile cannot be None")
        profile.validate()
        with self._lock:
            self._calibration = profile

    def clear_calibration(self) -> None:
        with self._lock:
            self._calibration = None

    def apply_calibration(self, data: VNAData) -> VNAData:
        with self._lock:
            profile = self._calibration
        if profile is None:
            raise ValueError("calibration profile not loaded")
        return profile.apply(data)

    def acquire_calibration(
        self,
        plan: CalibrationPlan,
        prompt: CalibrationPrompt | None = None,
        cancel_event: Event | None = None,
    ) -> CalibrationProfile:
        if not plan.steps:
            raise ValueError("calibration plan does not contain steps")

        self.set_sweep(plan.sweep)

        profile = CalibrationProfile(
            name=plan.name,
            method=CalibrationMethod.SOL,
            created_at=datetime.now(UTC),
            sweep=plan.sweep,
            frequencies=[],
            standards={},
            error_terms=CalibrationErrorTerms(),
        )

        for step in plan.steps:
            if cancel_event and cancel_event.is_set():
                raise TimeoutError("calibration cancelled")
            if prompt is not None:
                prompt(step.standard)
            measurement = self._scan_once()
            profile.standards[step.standard] = CalibrationMeasurement(
                frequencies=list(measurement.frequencies),
                s11=list(measurement.s11),
                s21=list(measurement.s21),
            )

        compute_error_terms(profile)
        profile.validate()

        with self._lock:
            self._calibration = profile
        return profile

    def _scan_once(self) -> VNAData:
        with self._lock:
            return self._driver.scan()


__all__ = ["VNA", "SweepConfig", "VNAData"]
