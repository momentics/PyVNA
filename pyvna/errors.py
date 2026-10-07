"""Exception hierarchy for the PyVNA package."""


class PyVNAError(Exception):
    """Base class for all errors raised by PyVNA."""


class DeviceError(PyVNAError):
    """The device is not reachable or misbehaves at the protocol level."""


class IdentificationError(DeviceError):
    """A probe concluded that the device does not speak this protocol family."""


class ProtocolError(DeviceError):
    """A device response was malformed, incomplete, or timed out mid-session."""


class CalibrationError(PyVNAError):
    """Calibration data is degenerate and error terms cannot be computed or applied."""


class CalibrationCancelledError(PyVNAError):
    """The caller cancelled a calibration run before it completed."""
