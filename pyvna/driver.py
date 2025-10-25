"""Driver abstractions and VNA pooling logic."""
from __future__ import annotations

from threading import RLock
from typing import Dict, Protocol

from .util.serial_port import SerialPortInterface, open_port
from .models import SweepConfig, VNAData
from .vna import VNA
from .driver_v1 import V1Driver
from .driver_v2 import V2Driver


class Driver(Protocol):
    """Interface that every hardware driver must implement."""

    def identify(self) -> str: ...

    def set_sweep(self, config: SweepConfig) -> None: ...

    def scan(self) -> VNAData: ...

    def close(self) -> None: ...


def driver_factory(port: SerialPortInterface) -> Driver:
    """Try to detect the device and instantiate the right driver."""

    v1_driver = V1Driver(port)
    try:
        v1_driver.identify()
        return v1_driver
    except Exception:
        pass

    v2_driver = V2Driver(port)
    try:
        v2_driver.identify()
        return v2_driver
    except Exception as exc:
        raise RuntimeError("failed to identify VNA device") from exc


class VNAPool:
    """Manage a pool of open VNAs for concurrent access."""

    def __init__(self) -> None:
        self._devices: Dict[str, VNA] = {}
        self._lock = RLock()

    def get(self, port_path: str) -> VNA:
        with self._lock:
            if port_path in self._devices:
                return self._devices[port_path]

            port = open_port(port_path, baudrate=115200)
            try:
                driver = driver_factory(port)
            except Exception:
                port.close()
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
