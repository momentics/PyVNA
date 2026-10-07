"""PyVNA: Python driver library for NanoVNA-family vector network analyzers."""

from importlib.metadata import PackageNotFoundError, version

from . import errors
from .calibration import (
    CalibrationErrorTerms,
    CalibrationMeasurement,
    CalibrationMethod,
    CalibrationPlan,
    CalibrationProfile,
    CalibrationPrompt,
    CalibrationStandard,
    CalibrationStep,
)
from .driver import Driver, VNAPool, driver_factory
from .driver_v1 import V1Driver
from .driver_v2 import V2Driver
from .driver_x import XDriver
from .errors import (
    CalibrationCancelledError,
    CalibrationError,
    DeviceError,
    IdentificationError,
    ProtocolError,
    PyVNAError,
)
from .models import SweepConfig, VNAData
from .vna import VNA

# The single source of truth for the version is pyproject.toml; the value
# below is read from the installed package metadata (PEP 621).
try:
    __version__ = version("pyvna")
except PackageNotFoundError:  # pragma: no cover - run from an uninstalled checkout
    __version__ = "0.0.0"

__all__ = [
    "__version__",
    "errors",
    "VNA",
    "SweepConfig",
    "VNAData",
    "VNAPool",
    "Driver",
    "driver_factory",
    "V1Driver",
    "V2Driver",
    "XDriver",
    "CalibrationMethod",
    "CalibrationPlan",
    "CalibrationProfile",
    "CalibrationPrompt",
    "CalibrationStandard",
    "CalibrationStep",
    "CalibrationMeasurement",
    "CalibrationErrorTerms",
    "PyVNAError",
    "DeviceError",
    "IdentificationError",
    "ProtocolError",
    "CalibrationError",
    "CalibrationCancelledError",
]
