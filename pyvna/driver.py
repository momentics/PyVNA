"""Driver abstractions and VNA pooling logic."""

from __future__ import annotations

import logging
from threading import RLock
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
    """Manage a pool of open VNAs for concurrent access."""

    def __init__(self) -> None:
        self._devices: dict[str, VNA] = {}
        self._lock = RLock()

    def get(self, port_path: str) -> VNA:
        with self._lock:
            if port_path in self._devices:
                return self._devices[port_path]

            logger.debug("opening %s", port_path)
            port = open_port(port_path, baudrate=115200)
            try:
                driver = driver_factory(port)
            except Exception:
                port.close()
                logger.debug("failed to identify device on %s", port_path)
                raise

            vna = VNA(driver)
            self._devices[port_path] = vna
            return vna

    def close_all(self) -> None:
        with self._lock:
            for vna in self._devices.values():
                vna.close()
            self._devices.clear()


__all__ = ["Driver", "driver_factory", "VNAPool"]
