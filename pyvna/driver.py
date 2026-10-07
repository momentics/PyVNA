"""Driver abstractions and VNA pooling logic."""

from __future__ import annotations

import logging
import threading
from typing import Protocol

from .driver_v1 import V1Driver
from .driver_v2 import V2Driver
from .driver_x import XDriver
from .models import SweepConfig, VNAData
from .util.serial_port import SerialPortInterface, open_port
from .vna import VNA

logger = logging.getLogger(__name__)


class Driver(Protocol):
    """Structural interface implemented by BaseDriver subclasses; also usable for test doubles."""

    model: str | None

    def identify(self) -> str: ...

    def set_sweep(self, config: SweepConfig) -> None: ...

    def scan(self) -> VNAData: ...

    def close(self) -> None: ...


def driver_factory(port: SerialPortInterface) -> Driver:
    """Probe the device with every known protocol and return a working driver.

    Probes are bounded in time, so the call always terminates. The returned
    driver has `model` set to its identification string.
    """
    failures: list[str] = []
    last_error: Exception | None = None
    for driver_cls in (XDriver, V1Driver, V2Driver):
        driver = driver_cls(port)
        try:
            driver.identify()
            return driver
        except Exception as exc:  # probe failure: record and try the next protocol
            last_error = exc
            logger.debug("%s probe failed: %s", driver_cls.__name__, exc)
            failures.append(f"{driver_cls.__name__}: {exc}")
    message = "unable to identify VNA device (" + "; ".join(failures) + ")"
    raise RuntimeError(message) from last_error


class VNAPool:
    """Manages open VNA devices for concurrent access.

    Devices are cached by port path; opening and identification run outside
    the pool lock so that a slow probe never blocks other ports.
    """

    def __init__(self) -> None:
        self._devices: dict[str, VNA] = {}
        self._opening: dict[str, threading.Event] = {}
        self._open_errors: dict[str, Exception] = {}
        self._lock = threading.Lock()

    def get(self, port_path: str) -> VNA:
        with self._lock:
            vna = self._devices.get(port_path)
            if vna is not None:
                return vna
            event = self._opening.get(port_path)
            if event is None:
                event = threading.Event()
                self._opening[port_path] = event
                owner = True
            else:
                owner = False

        if not owner:
            event.wait()
            with self._lock:
                error = self._open_errors.pop(port_path, None)
                vna = self._devices.get(port_path)
            if vna is not None:
                return vna
            if error is None:  # unreachable: owner always stores device or error
                raise RuntimeError(f"pool state lost for port {port_path}")
            raise error

        try:
            vna = self._open_device(port_path)
            with self._lock:
                self._devices[port_path] = vna
                self._open_errors.pop(port_path, None)
            return vna
        except Exception as exc:
            with self._lock:
                self._open_errors[port_path] = exc
            raise
        finally:
            with self._lock:
                self._opening.pop(port_path, None)
            event.set()

    def release(self, port_path: str) -> bool:
        """Close and drop one device; returns True when a device was present."""
        with self._lock:
            vna = self._devices.pop(port_path, None)
        if vna is None:
            return False
        vna.close()
        return True

    def close_all(self) -> None:
        with self._lock:
            devices = list(self._devices.values())
            self._devices.clear()
        for device in devices:
            device.close()

    @staticmethod
    def _open_device(port_path: str) -> VNA:
        port = open_port(port_path)
        try:
            driver = driver_factory(port)
        except Exception:
            port.close()
            raise
        logger.debug("opened %s as %s", port_path, type(driver).__name__)
        return VNA(driver)


__all__ = ["Driver", "driver_factory", "VNAPool"]
